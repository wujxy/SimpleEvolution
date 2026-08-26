"""Per-stage RNG streams.

One SeedSequence spawn per stage so that:
  - each stage is independently reproducible in unit tests;
  - adding an effect inside one stage does not change the random draws of
    any other stage (regression tests stay local);
  - the Cherenkov branch has its own stream, so enabling/disabling it does
    not perturb the scintillation chain.

The extra `calibration` stream builds the per-detector DetectorCalibration
once at DetectorSim construction; it is decoupled from all physics stages.
"""

from typing import Dict

import numpy as np

STAGE_KEYS = (
    "s1_response",
    "s2_scint",
    "s2_cherenkov",
    "s3_optics",
    "s4_detection",
    "s5_electronics",
    "calibration",
)


def make_rngs(seed: int) -> Dict[str, np.random.Generator]:
    children = np.random.SeedSequence(seed).spawn(len(STAGE_KEYS))
    return {
        key: np.random.default_rng(child)
        for key, child in zip(STAGE_KEYS, children)
    }
