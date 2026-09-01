"""JunoResBench: JUNO-like energy/vertex resolution toy-MC benchmark.

Wraps a frozen snapshot of the waverec waveform generator (SPE -> digitized
waveform, see _vendor/PROVENANCE.md) and adds the upstream detector physics:
photon production, position-dependent light collection, scintillation
timing, TOF, TTS and dark noise.
"""

from ._vendor.wavegen_v1 import SPEParams, WaveGenConfig  # noqa: F401
from ._vendor.wavegen_v1.generator import (  # noqa: F401
    PulseTruth,
    WaveEvent,
    WaveformGenerator,
)

__all__ = [
    "PulseTruth",
    "WaveEvent",
    "WaveGenConfig",
    "WaveformGenerator",
    "SPEParams",
]
