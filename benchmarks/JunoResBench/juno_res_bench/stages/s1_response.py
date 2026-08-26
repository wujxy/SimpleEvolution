"""Stage 1 — particle response: E_true -> E_dep -> E_vis.

v0 electron branch: point-like, fully contained deposition (A1), Birks
quenching with constant e-like dE/dx (B3), low-energy nonlinearity hook
(B7). gamma/positron branches are v1 (A3/A4/A5).
"""

from ..config import DetectorConfig
from ..truth import EventInput, ParticleType, S1Output


def nl_corr(e_mev: float, cfg: DetectorConfig) -> float:
    """Low-energy nonlinearity correction factor (B7), default ON."""
    return cfg.nl_correction(e_mev)


def run_s1(event: EventInput, cfg: DetectorConfig) -> S1Output:
    if event.particle_type is not ParticleType.ELECTRON:
        raise NotImplementedError(
            f"particle_type {event.particle_type} lands in v1 "
            "(gamma Compton chain / positron o-Ps)"
        )
    e_dep = event.e_true_mev                    # point-like, fully contained
    e_vis = cfg.quench(e_dep) * nl_corr(e_dep, cfg)
    return S1Output(e_dep_mev=float(e_dep), e_vis_mev=float(e_vis))
