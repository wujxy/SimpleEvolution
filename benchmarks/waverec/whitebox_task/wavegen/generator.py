"""Waveform generation: sample a pulse train, add noise, digitize."""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .config import PulseShape, SPEParams, WaveGenConfig


@dataclass(frozen=True)
class PulseTruth:
    """One photoelectron hit (ground truth for reconstruction)."""

    t_hit_ns: float   # time of the PE at the anode, relative to window start
    amplitude_pe: float  # charge in pe units (mean gain normalized to 1)

    def as_dict(self) -> dict:
        return {"t_hit_ns": self.t_hit_ns, "amplitude_pe": self.amplitude_pe}


@dataclass
class WaveEvent:
    """One channel readout: digitized waveform + its truth."""

    channel_id: int
    adc: np.ndarray            # int32, length n_samples
    truth: List[PulseTruth]

    def as_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "t_hit_ns": np.asarray([p.t_hit_ns for p in self.truth], dtype=np.float64),
            "amplitude_pe": np.asarray([p.amplitude_pe for p in self.truth], dtype=np.float64),
        }


class WaveformGenerator:
    """Truth -> digitized waveform.

    A single instance owns one RNG stream (seeded) and one fixed single-PE
    template, so a given (config, seed) reproduces data bit-for-bit.
    """

    def __init__(self, config: WaveGenConfig, seed: int = 0):
        self.cfg = config
        self.rng = np.random.default_rng(seed)
        self._template = self._build_template()

    # ------------------------------------------------------------------
    # single-PE template
    # ------------------------------------------------------------------
    def _build_template(self) -> np.ndarray:
        """Sample the single-PE pulse shape at the digitizer rate.

        Returns peak-normalized samples over [-pulse_length/2, +pulse_length/2)
        centered so that the pulse *peak* sits at the array center.
        """
        cfg = self.cfg
        half = cfg.pulse_length_ns
        t = np.arange(-half, half, cfg.sample_interval_ns, dtype=np.float64)
        if cfg.pulse_shape is PulseShape.LOG_NORMAL:
            dt = t - cfg.pulse_shift_ns
            s = np.zeros_like(t)
            pos = dt > 0
            s[pos] = np.exp(
                -np.log(dt[pos] / cfg.pulse_width_ns) ** 2 / (2.0 * cfg.pulse_mu**2)
            )
        elif cfg.pulse_shape is PulseShape.GAUSSIAN:
            s = np.exp(-t**2 / (2.0 * cfg.pulse_width_ns**2))
        else:  # pragma: no cover - enum exhausted
            raise ValueError(f"unknown pulse shape {cfg.pulse_shape}")
        s /= s.max()
        # normalize so that peak = 1 pe amplitude in Volts
        return s * cfg.pe_amplitude_v

    def template_peak_index(self) -> int:
        """Index in the template array where the peak sits."""
        return int(np.argmax(self._template))

    # ------------------------------------------------------------------
    # truth sampling
    # ------------------------------------------------------------------
    def _sample_amplitudes(self, n: int, channel_gain: float) -> np.ndarray:
        """Draw n SPE charges (pe) and scale by the channel gain."""
        p = self.cfg.spe
        u = self.rng.random(n)
        amp = np.where(
            u < p.p_tail,
            self.rng.exponential(p.tail_decay, size=n) + p.tail_cutoff,
            self.rng.normal(p.gain, p.sigma_gain, size=n),
        )
        # guard against pathological (negative-gauss) draws
        np.clip(amp, 1e-4, None, out=amp)
        return amp * channel_gain

    def _sample_times(self, n: int) -> np.ndarray:
        """Draw n hit times uniform over the physics window.

        Pulses within one pulse-length of the window edge would leak out of
        the readout; keep the full pulse inside by construction instead.
        """
        cfg = self.cfg
        lo = cfg.pulse_length_ns * cfg.sample_interval_ns
        hi = (cfg.n_samples - cfg.pulse_length_ns) * cfg.sample_interval_ns
        t = self.rng.uniform(lo, hi, size=n)
        if cfg.tts_sigma_ns > 0:
            t = t + self.rng.normal(0.0, cfg.tts_sigma_ns, size=n)
        return t

    # ------------------------------------------------------------------
    # forward model
    # ------------------------------------------------------------------
    def generate(
        self,
        channel_id: int,
        n_pe: Optional[int] = None,
        mean_pe: Optional[float] = None,
    ) -> WaveEvent:
        """Generate one channel readout.

        Exactly one of n_pe (fixed count) or mean_pe (Poisson mean) must be
        given.
        """
        cfg = self.cfg
        if (n_pe is None) == (mean_pe is None):
            raise ValueError("give exactly one of n_pe / mean_pe")

        if n_pe is None:
            n_pe = int(self.rng.poisson(mean_pe))

        # per-channel gain fluctuates once per event (channel calibration err)
        channel_gain = 1.0 + cfg.gain_spread * self.rng.normal()

        times = self._sample_times(n_pe)
        amplitudes = self._sample_amplitudes(n_pe, channel_gain)

        order = np.argsort(times)
        times, amplitudes = times[order], amplitudes[order]

        adc = self._synthesize(times, amplitudes)
        truth = [
            PulseTruth(t_hit_ns=float(t), amplitude_pe=float(a))
            for t, a in zip(times, amplitudes)
        ]
        return WaveEvent(channel_id=channel_id, adc=adc, truth=truth)

    def _synthesize(self, times: np.ndarray, amplitudes: np.ndarray) -> np.ndarray:
        """Sum shifted templates, digitize, quantize to ADC counts."""
        cfg = self.cfg
        n = cfg.n_samples
        ts = np.arange(n, dtype=np.float64) * cfg.sample_interval_ns

        # analog pulse train, in Volts (positive shape; sign applied below)
        wf = np.zeros(n, dtype=np.float64)
        peak = self.template_peak_index()
        tmpl = self._template
        for t, a in zip(times, amplitudes):
            # peak of this pulse lands at sample t / dt
            center = t / cfg.sample_interval_ns
            i0 = int(round(center)) - peak
            i1 = i0 + tmpl.size
            c0, c1 = max(i0, 0), min(i1, n)
            if c0 >= c1:
                continue
            wf[c0:c1] += a * tmpl[c0 - i0 : c1 - i0]

        # polarity: JUNO-style negative-going pulses on a positive baseline
        sig_v = cfg.polarity * wf

        noise_v = self.rng.normal(0.0, cfg.noise_sigma_mv * 1e-3, size=n)
        volts = cfg.baseline_frac * cfg.adc_range_v + sig_v + noise_v
        raw = volts / cfg.lsb_v
        adc = np.clip(np.round(raw), 0, (1 << cfg.adc_bits) - 1)
        return adc.astype(np.int32)

    # ------------------------------------------------------------------
    # convenience for benchmark datasets
    # ------------------------------------------------------------------
    def generate_batch(
        self,
        n_events: int,
        mean_pe: float,
        n_channels: int = 1,
        start_channel_id: int = 0,
    ) -> List[WaveEvent]:
        """Generate n_events events, each with a Poisson(mean_pe) pulse count."""
        events: List[WaveEvent] = []
        for _ in range(n_events):
            for ch in range(n_channels):
                events.append(
                    self.generate(
                        channel_id=start_channel_id + ch,
                        mean_pe=mean_pe,
                    )
                )
        return events
