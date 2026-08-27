"""Stage 1 — particle response: E_true -> E_dep -> E_vis.

Dispatch on particle_type:
  electron  point-like, fully-contained deposition (A1), Birks quenching
            with constant e-like dE/dx (B3), low-energy nonlinearity hook
            (B7). Deterministic — consumes no RNG.
  gamma     Compton chain with escape (A3/A5) — stages/s1_particles.py.
  positron  prompt kinetic deposit + annihilation (A4) — s1_particles.py.

The per-step visible energy uses the same formula everywhere:
e_vis_step = quench(e_dep_step) * nl_corr(e_dep_step); for electrons the
single step reproduces the v0 anchor bit-exactly, while gamma/positron
chains get the physically-correct stronger suppression of low-energy
(secondary-electron) steps.
"""

from ..config import DetectorConfig
from ..truth import DepositionSteps, EventInput, ParticleType, S1Output
from . import s1_particles


def nl_corr(e_mev: float, cfg: DetectorConfig) -> float:
    """Low-energy nonlinearity correction factor (B7), default ON."""
    return cfg.nl_correction(e_mev)


def run_s1(event: EventInput, cfg: DetectorConfig, rng=None) -> S1Output:
    pt = event.particle_type
    if pt is ParticleType.ELECTRON:
        e_dep = event.e_true_mev                    # point-like, fully contained
        e_vis = cfg.quench(e_dep) * nl_corr(e_dep, cfg)
        steps = DepositionSteps.single(
            event.vertex_m, e_dep, e_vis, event.direction
        )
        return S1Output(e_dep_mev=float(e_dep), e_vis_mev=float(e_vis),
                        steps=steps, e_escape_mev=0.0,
                        particle_type=ParticleType.ELECTRON)
    if rng is None:
        raise ValueError(f"particle_type {pt} needs an rng (the s1_response stream)")
    if pt is ParticleType.GAMMA:
        return s1_particles.run_s1_gamma(event, cfg, rng)
    if pt is ParticleType.POSITRON:
        return s1_particles.run_s1_positron(event, cfg, rng)
    raise NotImplementedError(f"unknown particle_type {pt}")
