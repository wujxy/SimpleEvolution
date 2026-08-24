"""Configuration dataclasses for wavegen.

All defaults mirror the JUNO ElecSimV3 electronics simulation
(Simulation/ElecSimV3/ElecSimAlg/src/TriggerHandlerLpmtHelper.{h,cc},
TriggerHandlerLpmt.cc) so that data produced here is representative of a
real liquid-scintillator PMT readout chain, while staying fully generic.
"""

from dataclasses import dataclass, field
from enum import Enum


class PulseShape(Enum):
    """Single-photoelectron pulse shape family."""

    LOG_NORMAL = "log_normal"
    GAUSSIAN = "gaussian"


@dataclass(frozen=True)
class SPEParams:
    """Single-photoelectron charge spectrum (in pe units).

    The physical model: a PE avalanche has a Gaussian gain fluctuation with
    probability (1 - p_tail), and an exponential high-charge tail
    (pre-pulses / late avalanches) with probability p_tail. Note the tail is
    sampled as Exponential(scale=tail_decay) — i.e. tail_decay acts as the
    mean (2.2 pe), so tail PEs are on average *larger* than core PEs.

    Defaults follow the NNVT MCP PMT model in ElecSimV3 PulseGen_NNVT
    (exp decay 2.2, cutoff 0.1) but scaled to a narrow, generic spectrum.
    """

    gain: float = 1.0            # mean amplitude of the Gaussian core, pe
    sigma_gain: float = 0.30      # Gaussian core width, pe
    p_tail: float = 0.10          # probability of drawing from the tail
    tail_decay: float = 2.2       # exponential tail decay constant, pe
    tail_cutoff: float = 0.10     # minimum amplitude of a tail PE, pe

    def mean(self) -> float:
        """Mean charge in pe (used for diagnostics, not by the generator)."""
        core = (1.0 - self.p_tail) * self.gain
        tail = self.p_tail * (self.tail_decay + self.tail_cutoff)
        return core + tail


@dataclass(frozen=True)
class WaveGenConfig:
    """Forward-model constants (digitizer + shaping + noise)."""

    # ---- digitizer -----------------------------------------------------
    sample_interval_ns: float = 1.0   # sampling period (1 GSa/s)
    n_samples: int = 1000             # readout window (pre 300 + post 700 ns)
    adc_bits: int = 14
    adc_range_v: float = 1.0          # full-scale of the FADC input, V
    baseline_frac: float = 0.292      # DC baseline as fraction of full scale
    polarity: int = -1                # pulses go below baseline (JUNO GCU)

    # ---- single-PE pulse shape ------------------------------------------
    pulse_shape: PulseShape = PulseShape.LOG_NORMAL
    pulse_width_ns: float = 13.0      # log-normal width parameter
    pulse_mu: float = 0.43            # log-normal asymmetry parameter
    pulse_shift_ns: float = 6.0 - 13.0 / 1.5   # onset offset
    pulse_length_ns: int = 150        # pulse sampled over +- this span

    # ---- electronics noise ----------------------------------------------
    noise_sigma_mv: float = 0.35      # white Gaussian noise, mV RMS

    # ---- per-channel gain spread ----------------------------------------
    gain_spread: float = 0.15         # relative sigma of per-channel gain

    spe: SPEParams = field(default_factory=SPEParams)

    # ---- physics / bookkeeping -------------------------------------------
    dark_rate_hz: float = 0.0         # optional uncorrelated dark pulses
    tts_sigma_ns: float = 0.0         # transit-time spread applied to t_j

    @property
    def lsb_v(self) -> float:
        """Volts per ADC count."""
        return self.adc_range_v / (1 << self.adc_bits)

    @property
    def baseline_adc(self) -> int:
        """Baseline in ADC counts (where the waveform sits at rest)."""
        return int(round(self.baseline_frac * ((1 << self.adc_bits) - 1)))

    @property
    def pe_amplitude_v(self) -> float:
        """Peak amplitude of a 1-pe pulse, Volts.

        JUNO-measured waveform templates (WaveformTemplate.Runnumber.9487)
        have a single-PE peak of ~7 mV on a 1 V full-scale, 14-bit chain,
        which we adopt as the generic default.
        """
        return 7.0e-3
