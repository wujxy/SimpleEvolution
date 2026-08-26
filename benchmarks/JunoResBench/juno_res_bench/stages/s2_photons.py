"""Stage 2 — photon generation: E_vis -> PhotonSoA.

Scintillation branch (B1/B2/B5): fluctuating photon count, isotropic
directions, 4-exponential emission times.
Cherenkov branch (B4, default ON): Frank-Tamm yield with the electron
beta from the deposited kinetic energy, photons on the cone
cos(theta_C) = 1/(n*beta) around the particle direction, prompt emission.
Note: for v0 e-like events the input direction is a placeholder unless the
benchmark provides one; the cone structure itself is real.
"""

import numpy as np

from ..config import DetectorConfig
from ..truth import PhotonSoA, S1Output

ELECTRON_MASS_MEV = 0.510999


def beta_from_kinetic(e_mev: float) -> float:
    """Electron beta for a given kinetic energy (MeV)."""
    gamma = 1.0 + max(e_mev, 0.0) / ELECTRON_MASS_MEV
    return float(np.sqrt(1.0 - 1.0 / gamma**2))


def _orthonormal_basis(d: np.ndarray):
    """Two unit vectors u, v orthogonal to the unit vector d."""
    a = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(d, a)
    u /= np.linalg.norm(u)
    v = np.cross(d, u)
    return u, v


def run_s2_scint(s1: S1Output, event, cfg: DetectorConfig, rng: np.random.Generator) -> PhotonSoA:
    mu_gamma = s1.e_vis_mev * cfg.ly_photons_mev
    n_gamma = int(max(0.0, round(rng.normal(mu_gamma, np.sqrt(mu_gamma)))))
    if n_gamma == 0:
        return PhotonSoA.empty()

    # isotropic directions
    ct = rng.uniform(-1.0, 1.0, n_gamma)
    phi = rng.uniform(0.0, 2.0 * np.pi, n_gamma)
    st = np.sqrt(1.0 - ct * ct)
    dirs = np.column_stack(
        (st * np.cos(phi), st * np.sin(phi), ct)
    ).astype(np.float32)

    # 4-exponential emission time mixture
    taus = np.asarray([t for t, _ in cfg.scint_taus_ns])
    wts = np.asarray([w for _, w in cfg.scint_taus_ns])
    wts = wts / wts.sum()
    comp = rng.choice(len(wts), size=n_gamma, p=wts)
    t_emit = rng.exponential(taus[comp]).astype(np.float32)

    return PhotonSoA(
        photon_type=np.zeros(n_gamma, np.int8),
        pos_m=np.tile(event.vertex_m.astype(np.float32), (n_gamma, 1)),
        dir=dirs,
        t_emit_ns=t_emit,
    )


def run_s2_cherenkov(s1: S1Output, event, cfg: DetectorConfig, rng: np.random.Generator) -> PhotonSoA:
    """Cherenkov photons (B4): cone around event.direction, prompt (t=0)."""
    if cfg.ly_cherenkov is None or cfg.ly_cherenkov <= 0:
        return PhotonSoA.empty()

    beta = beta_from_kinetic(s1.e_dep_mev)
    cos_tc = 1.0 / (cfg.ls_refractive_index * beta)
    if cos_tc >= 1.0:          # below Cherenkov threshold
        return PhotonSoA.empty()
    sin_tc = np.sqrt(1.0 - cos_tc**2)

    # Frank-Tamm yield, Poisson fluctuation
    n_c = int(rng.poisson(s1.e_dep_mev * cfg.ly_cherenkov * (1.0 - cos_tc**2)))
    if n_c == 0:
        return PhotonSoA.empty()

    d = np.asarray(event.direction, dtype=np.float64)
    d = d / np.linalg.norm(d)
    u, v = _orthonormal_basis(d)
    phi = rng.uniform(0.0, 2.0 * np.pi, n_c)
    dirs = (
        cos_tc * d[None, :]
        + sin_tc * (np.cos(phi)[:, None] * u[None, :] + np.sin(phi)[:, None] * v[None, :])
    ).astype(np.float32)

    return PhotonSoA(
        photon_type=np.ones(n_c, np.int8),
        pos_m=np.tile(event.vertex_m.astype(np.float32), (n_c, 1)),
        dir=dirs,
        t_emit_ns=np.zeros(n_c, np.float32),
    )
