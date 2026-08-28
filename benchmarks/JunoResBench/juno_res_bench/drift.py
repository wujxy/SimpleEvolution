"""Slow calibration drift on the run clock (effects.md E2/D3 realism).

Real detectors are calibrated once and wander: per-PMT gains breathe with
temperature, the global light-yield/efficiency scale drifts, dark rates
move by tens of percent over a run. This module owns that hidden state:

  gain_i(t)  = g_static_i * (1 + OU_i(t)) * (1 + jitter_i(e))   [charge]
  pde_i(t)   = (1 + pde_static_i) * (1 + g_glob(t)) * (1 + OU_i(t)) - 1
  dcr(t)     = DCR_static * exp(OU_dcr(t))                      [global]

  OU(x; sigma, tau): exact update  x <- x*e^(-dt/tau) + sigma*sqrt(
  1 - e^(-2 dt/tau)) * N(0,1), initialized at stationarity, so the drift
  is visible from the first event and every realization is reproducible.

Deliberately ABSENT: a per-event common-mode scale (all channels together
x(1+delta)) — it is exactly degenerate with event energy and would be an
uncalibrable resolution floor, not a calibration problem. The per-PMT
wander averages down over ~5k active channels; the SLOW GLOBAL modes are
the ones a solver must track through time (train labels interleave the
run for exactly this reason).

The state lives in its own RNG (SeedSequence-derived, created only when
cfg.drift is on), so all frozen v1 RNG streams are untouched.
"""

import numpy as np

from .config import DetectorConfig
from .truth import DetectorCalibration


def _ou_step(x: np.ndarray, dt: float, sigma: float, tau: float,
             rng: np.random.Generator) -> np.ndarray:
    """One exact Ornstein-Uhlenbeck update over elapsed dt seconds."""
    decay = np.exp(-dt / tau)
    kick = sigma * np.sqrt(1.0 - decay * decay)
    return x * decay + kick * rng.standard_normal(x.shape)


class DriftState:
    """Hidden detector state advancing on the dataset run clock."""

    def __init__(self, cfg: DetectorConfig, n_pmt: int, seed: int):
        self.cfg = cfg
        self.n_pmt = n_pmt
        self.rng = np.random.default_rng([seed, 0x0D01F7])
        # stationary init: the run starts mid-wander, not at a special zero
        self.ou_gain = self.rng.normal(0.0, cfg.drift_gain_ou_sigma, n_pmt)
        self.ou_pde = self.rng.normal(0.0, cfg.drift_pde_ou_sigma, n_pmt)
        self.g_glob = self.rng.normal(0.0, cfg.drift_pde_global_sigma)
        self.ou_dcr = self.rng.normal(0.0, cfg.drift_dcr_log_sigma)
        self.t_last = None   # run-clock seconds of the last advance

    # ------------------------------------------------------------------
    def advance(self, t: float) -> None:
        """Move the OU processes from t_last to t (seconds, monotonic)."""
        if self.t_last is None:
            self.t_last = t
            return
        dt = max(0.0, t - self.t_last)
        self.t_last = t
        if dt == 0.0:
            return
        cfg = self.cfg
        self.ou_gain = _ou_step(self.ou_gain, dt, cfg.drift_gain_ou_sigma,
                                cfg.drift_gain_ou_tau_s, self.rng)
        self.ou_pde = _ou_step(self.ou_pde, dt, cfg.drift_pde_ou_sigma,
                               cfg.drift_pde_ou_tau_s, self.rng)
        self.g_glob = float(_ou_step(
            np.atleast_1d(self.g_glob), dt, cfg.drift_pde_global_sigma,
            cfg.drift_pde_global_tau_s, self.rng)[0])
        self.ou_dcr = float(_ou_step(
            np.atleast_1d(self.ou_dcr), dt, cfg.drift_dcr_log_sigma,
            cfg.drift_dcr_log_tau_s, self.rng)[0])

    # ------------------------------------------------------------------
    def effective_calibration(self, static: DetectorCalibration
                              ) -> DetectorCalibration:
        """Static per-PMT calibration modulated to the current instant.

        The per-event per-PMT gain jitter (HV noise) is drawn here — one
        draw per generated event keeps it deterministic in event order."""
        jitter = self.rng.normal(0.0, self.cfg.drift_gain_event_jitter,
                                 self.n_pmt)
        gain = static.gain * (1.0 + self.ou_gain) * (1.0 + jitter)
        pde = ((1.0 + static.pde_delta) * (1.0 + self.g_glob)
               * (1.0 + self.ou_pde) - 1.0)
        return DetectorCalibration(
            pde_delta=pde, gain=gain,
            time_offset_ns=static.time_offset_ns,
            tts_sigma_ns=static.tts_sigma_ns,
        )

    @property
    def dark_rate_scale(self) -> float:
        return float(np.exp(self.ou_dcr))
