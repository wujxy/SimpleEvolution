"""Detector-level configuration for the JunoResBench toy MC.

All physics constants trace back to JUNO-SW J26.4.1 DetSimV2 (see
docs/effects.md and docs/differences.md for provenance and rationale);
the optical transport is deliberately collapsed into analytic functions.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np


# e-like scintillation time profile, JUNO-SW GammaCONSTANT
# (dbdata/offline-data/Simulation/DetSim/Material/LS/GammaCONSTANT)
SCINT_TAUS_NS_DEFAULT = (
    (4.6, 0.707),
    (15.1, 0.205),
    (76.1, 0.060),
    (397.0, 0.028),
)


@dataclass(frozen=True)
class DetectorConfig:
    """Forward-model constants for E_true -> per-PMT photoelectrons."""

    # ---- stage-3 optics mode ----------------------------------------------
    # "fast":  analytic weights (Multinomial) + parameterized scatter timing
    # "trace": per-photon transport (absorption/re-emission/Rayleigh/ESR,
    #          wavelength-resolved; see docs/trace_design.md)
    optics_mode: str = "fast"
    # trace-mode detection normalization: compensates the explicit absorption
    # losses so the calibrated center-pe anchor holds (calibrated against the
    # fast mode with the current optical tables; re-calibrate if tables change)
    trace_det_norm: float = 0.96

    # ---- scintillation ---------------------------------------------------
    # intrinsic photon production
    ly_photons_mev: float = 10168.0     # DetSimV2 LS ConstantProperty
    # effective *detected* pe yield at the detector center (folds geometric
    # coverage, QE, CE and average attenuation into one number;
    # JUNO ~1300-1600 pe/MeV). Detection probability per photon:
    #   p_det = mu_pe_per_mev_center / ly_photons_mev  (=0.1475)
    mu_pe_per_mev_center: float = 1500.0

    # Birks quenching: E_vis = E/(1 + kB*dE/dx). For e-like events dE/dx is
    # nearly constant, so we expose the product directly. Default ON.
    # JUNO-SW: kB(e) = 12.05e-3 g/cm2/MeV, dE/dx(e in LS) ~ 2 MeV/cm -> ~2.4%
    birks_kB_ddx: Optional[float] = 0.0241

    # Low-energy nonlinearity (B7): nl_corr(E) = 1 - nl_amp*exp(-E/nl_scale).
    # Parameterized toy shape: ~-0.7% at 1 MeV, ->0 above a few MeV.
    nl_amp: float = 0.02
    nl_scale_mev: float = 1.0

    # ---- light-collection nonuniformity ----------------------------------
    # mu_pe(r) = mu_pe(0) * (1 + k2*(r/R)^2 + k4*(r/R)^4)
    # folds attenuation + geometric coverage; tuned so the full-scale edge
    # differs from the center by a few percent (JUNO-like nonuniformity)
    nonuniform_radius_m: float = 17.7
    nonuniformity_k2: float = -0.15
    nonuniformity_k4: float = 0.10

    # ---- timing ----------------------------------------------------------
    scint_taus_ns: tuple = SCINT_TAUS_NS_DEFAULT   # (tau, weight) pairs
    ls_refractive_index: float = 1.49
    tts_sigma_ns: float = 4.0               # LPMT per-PMT mean TTS
    # Rayleigh + re-emission equivalent timing spread (C2/C3); ns per meter
    # of path. Calibrated so the total timing residual is TTS (+~0.5 ns at
    # center-scale paths).
    a_scatter_ns_per_m: float = 0.03

    # ---- noise -----------------------------------------------------------
    dark_rate_hz: float = 24000.0           # LPMT per-PMT DCR
    # Afterpulses (E3, default ON): per-PE probability + exponential delay
    # (JUNO MCP AP total prob ~1.6%, multi-component µs-scale delays; toy
    # uses one Exp component, only in-window APs enter the waveform)
    afterpulse_prob: float = 0.016
    afterpulse_tau_ns: float = 500.0

    # ---- per-PMT calibration (D3/E2) --------------------------------------
    pde_sigma: float = 0.08                 # rel. sigma of per-PMT PDE spread
    time_offset_sigma_ns: float = 1.0       # static per-PMT time offset (toy)

    # ---- gamma / positron chains (v1; effects.md A3/A4/A5) -----------------
    # Compton mean free path: lambda(E) = 1/(n_e * sigma_KN(E)) with the
    # analytic Klein-Nishina total cross section. 2.80e29 /m3 reproduces the
    # NIST-like mass attenuation of LAB-based LS (rho ~ 0.86 g/cm3, Z/A ~
    # 0.54): lambda(1 MeV) ~ 0.17 m, i.e. mu ~ 0.06 /cm.
    ls_electron_density_per_m3: float = 2.80e29
    # photoelectric-vs-Compton toy split: P_PE / sigma_KN = (E_x / E)^3 with
    # E_x the crossover energy where both are equal (PE dominates below).
    gamma_pe_crossover_kev: float = 40.0
    # photons below this energy are absorbed at their next interaction point
    gamma_abs_cut_kev: float = 20.0
    # hard cap on chain iterations (safety, never reached physically)
    gamma_max_steps: int = 256
    # positronium (A4): o-Ps formation fraction + lifetime; three_gamma_frac
    # is the absolute 3-gamma fraction of all annihilations (effects.md).
    ops_fraction: float = 0.545
    ops_tau_ns: float = 3.08
    three_gamma_frac: float = 0.022

    # ---- Cherenkov (B4; default ON) ---------------------------------------
    # N_C ~ Poisson(E_dep * ly_cherenkov * (1 - 1/(n*beta)^2)) with beta from
    # the electron kinetic energy; ly_cherenkov is calibrated so that
    # N_C/N_scint ~ 2.5% at 1 MeV (JUNO-SW CherenkovYieldFactor=0.517 scale).
    # None disables.
    ly_cherenkov: Optional[float] = 500.0

    # ---- angular collection efficiency (D2; enabled with stage-4 physics) -
    # NNVT MCP CE(θ) table, incidence angle in degrees (PMTSimParamSvc)
    ce_theta_deg: tuple = (0.0, 14.0, 30.0, 42.5, 55.0, 67.0, 77.5, 85.0, 90.0)
    ce_eff: tuple = (1.0, 1.0, 0.9453, 0.9105, 0.8931, 0.9255, 0.9274, 0.8841, 0.734)

    # ---- geometry --------------------------------------------------------
    detector_radius_m: float = 19.365       # PMT sphere radius (JUNO CD LPMT)
    pmt_diameter_m: float = 0.508           # 20-inch PMT
    # geometric coverage self-check anchor:
    #   N_pmt * pi*(d/2)^2 / (4 pi R^2) = 17612 * ... / (4 pi 19.365^2)
    #                                   = 0.749  (~75%, JUNO-like)

    @property
    def p_det_center(self) -> float:
        """Per-photon detection probability at the center."""
        return self.mu_pe_per_mev_center / self.ly_photons_mev

    # ---- readout: trigger-defined window (trigger architecture v1) --------
    # global trigger on the PE rate: 1-ns histogram of all hit times
    # (physics + dark), sliding sum over trigger_window_ns, first crossing
    # of trigger_threshold_pe within [t0-search_pre, t0+search_post] is
    # t_trigger; readout window starts pre_trigger_ns before t_trig and is
    # window_ns long in TOTAL (1000 ns = 300 pre + 700 post-trigger).
    # Threshold sits >20 sigma above the pure-dark window sum (total dark
    # ~422 pe/us over 17612 PMTs -> ~42 pe per 100 ns window).
    pre_trigger_ns: float = 300.0
    window_ns: float = 1000.0
    trigger_window_ns: float = 100.0
    trigger_threshold_pe: float = 200.0
    trigger_search_pre_ns: float = 500.0
    trigger_search_post_ns: float = 500.0
    dark_span_pre_ns: float = 800.0     # >= search_pre + pre_trigger
    dark_span_post_ns: float = 1500.0   # >= search_post + window tail

    def mu_pe_ratio(self, r_m: float) -> float:
        """Relative light collection vs radius (1.0 at center)."""
        u = r_m / self.nonuniform_radius_m
        return 1.0 + self.nonuniformity_k2 * u**2 + self.nonuniformity_k4 * u**4

    def quench(self, e_true: float) -> float:
        """Visible energy after Birks quenching."""
        if self.birks_kB_ddx is None:
            return e_true
        return e_true / (1.0 + self.birks_kB_ddx)

    def nl_correction(self, e_mev: float) -> float:
        """Low-energy nonlinearity factor (B7), 1.0 at high energy."""
        return 1.0 - self.nl_amp * np.exp(-max(e_mev, 0.0) / self.nl_scale_mev)
