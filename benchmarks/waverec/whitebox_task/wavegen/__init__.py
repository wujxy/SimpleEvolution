"""wavegen — a generic PMT-like waveform generator (truth -> waveform).

Forward model (per channel, per event):

    adc(t) = baseline + Q(f(t)) + noise

    f(t)  = sum_j  a_j * spe(t - t_j)          (analog pulse train, Volts)
    Q(x)  = quantize_14bit(x + baseline_V)     (FADC digitization)

    spe(t)  = exp( -ln((t - shift)/width)^2 / (2*mu^2) )   (log-normal pulse)
    a_j     ~ SPE charge spectrum (Gaussian core + exponential tail), ~1 pe

Truth per pulse: (t_j, a_j) in ns and pe units.

The default parameter values are taken from the JUNO ElecSimV3 electronics
simulation (TriggerHandlerLpmtHelper), but nothing here is JUNO-specific:
every constant is exposed in WaveGenConfig and the generator works for any
PMT-like detector with the same shaping/digitization scheme.
"""

from .config import PulseShape, SPEParams, WaveGenConfig
from .generator import PulseTruth, WaveEvent, WaveformGenerator

__all__ = [
    "PulseShape",
    "SPEParams",
    "WaveGenConfig",
    "PulseTruth",
    "WaveEvent",
    "WaveformGenerator",
]
