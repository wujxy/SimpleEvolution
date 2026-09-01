"""Stage 1 — particle transport and local visible-energy response."""

from ..config import DetectorConfig
from ..truth import EventInput, ParticleType, S1Output
from . import s1_particles


def run_s1(event: EventInput, cfg: DetectorConfig, rng=None) -> S1Output:
    pt = event.particle_type
    if pt is ParticleType.ELECTRON:
        return s1_particles.run_s1_electron(event, cfg, rng)
    if rng is None:
        raise ValueError(f"particle_type {pt} needs an rng (the s1_response stream)")
    if pt is ParticleType.GAMMA:
        return s1_particles.run_s1_gamma(event, cfg, rng)
    if pt is ParticleType.POSITRON:
        return s1_particles.run_s1_positron(event, cfg, rng)
    raise NotImplementedError(f"unknown particle_type {pt}")
