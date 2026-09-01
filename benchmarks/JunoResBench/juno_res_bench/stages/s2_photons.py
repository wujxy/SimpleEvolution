"""Stage 2 — photon generation: deposition steps -> PhotonSoA.

Scintillation branch (B1/B2/B5): per-step Poisson photon count, isotropic
directions, 4-exponential emission times shifted by the
step deposition time (o-Ps-delayed light for annihilation steps).
Cherenkov branch (B4, default ON): per-step path-integrated Frank-Tamm yield
with beta from the charged kinetic energy at the step midpoint, photons on the cone
cos(theta_C) = 1/(n*beta) around the step's charged-particle direction,
prompt at the step time.

RNG draw order:
  scint      poisson(counts) -> uniform(ct) -> uniform(phi) -> choice -> exp
  cherenkov  poisson(counts, above-threshold steps only) -> uniform(phi)

Duck-typed S1 stubs without `steps` (unit tests) fall back to a single
vertex step.
"""

import numpy as np

from ..config import DetectorConfig
from ..stopping_power import electron_stopping_power_mev_cm
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


def _step_view(s1, event):
    """Return aligned local-step arrays; duck-typed stubs become one step."""
    steps = getattr(s1, "steps", None)
    if steps is not None:
        return (steps.e_vis_mev, steps.pos_m, steps.t_ns,
                steps.e_dep_mev, steps.dir, steps.kinetic_mev,
                steps.step_length_m)
    e_vis = np.asarray([float(s1.e_vis_mev)])
    pos = np.asarray(event.vertex_m, np.float64).reshape(1, 3)
    t = np.zeros(1)
    e_dep = np.asarray([float(getattr(s1, "e_dep_mev", s1.e_vis_mev))])
    d = np.asarray([event.direction], np.float64)
    kinetic = 0.5 * e_dep
    length_m = e_dep / electron_stopping_power_mev_cm(kinetic) / 100.0
    return e_vis, pos, t, e_dep, d, kinetic, length_m


def run_s2_scint(s1: S1Output, event, cfg: DetectorConfig, rng: np.random.Generator) -> PhotonSoA:
    e_vis, pos, t_step, _e_dep, _d, _kinetic, _length = _step_view(s1, event)
    mu_gamma = e_vis * cfg.ly_photons_mev
    n_per_step = rng.poisson(np.clip(mu_gamma, 0.0, None)).astype(np.int64)
    n_gamma = int(n_per_step.sum())
    if n_gamma == 0:
        return PhotonSoA.empty()

    # isotropic directions
    ct = rng.uniform(-1.0, 1.0, n_gamma)
    phi = rng.uniform(0.0, 2.0 * np.pi, n_gamma)
    st = np.sqrt(1.0 - ct * ct)
    dirs = np.column_stack(
        (st * np.cos(phi), st * np.sin(phi), ct)
    ).astype(np.float32)

    # 4-exponential emission time mixture, shifted by the deposition time
    taus = np.asarray([t for t, _ in cfg.scint_taus_ns])
    wts = np.asarray([w for _, w in cfg.scint_taus_ns])
    wts = wts / wts.sum()
    comp = rng.choice(len(wts), size=n_gamma, p=wts)
    t_emit = rng.exponential(taus[comp])
    t_emit = (t_emit + np.repeat(t_step, n_per_step)).astype(np.float32)

    return PhotonSoA(
        photon_type=np.zeros(n_gamma, np.int8),
        pos_m=np.repeat(pos.astype(np.float32), n_per_step, axis=0),
        dir=dirs,
        t_emit_ns=t_emit,
        step_idx=np.repeat(np.arange(len(n_per_step), dtype=np.int32), n_per_step),
    )


def run_s2_cherenkov(s1: S1Output, event, cfg: DetectorConfig, rng: np.random.Generator) -> PhotonSoA:
    """Cherenkov photons (B4): per-step cones around the step direction."""
    if (cfg.cherenkov_photons_per_m is None
            or cfg.cherenkov_photons_per_m <= 0):
        return PhotonSoA.empty()

    _e_vis, pos, t_step, _e_dep, d_step, kinetic, length_m = _step_view(
        s1, event
    )
    beta = np.asarray([beta_from_kinetic(float(e)) for e in kinetic])
    with np.errstate(divide="ignore", invalid="ignore"):
        cos_tc = 1.0 / (cfg.ls_refractive_index * beta)
    above = np.isfinite(cos_tc) & (cos_tc < 1.0)
    if not above.any():                  # below threshold: no RNG consumed
        return PhotonSoA.empty()

    idx = np.where(above)[0]
    ct_k = cos_tc[idx]
    st_k = np.sqrt(1.0 - ct_k**2)
    lam = (length_m[idx] * cfg.cherenkov_photons_per_m
           * np.maximum(0.0, 1.0 - ct_k**2))
    n_per = rng.poisson(lam).astype(np.int64)
    n_c = int(n_per.sum())
    if n_c == 0:
        return PhotonSoA.empty()

    phi = rng.uniform(0.0, 2.0 * np.pi, n_c)

    # per-step cone directions (loop over steps, vectorized in phi)
    out_dirs, out_pos, out_t, out_step, off = [], [], [], [], 0
    for j, k in enumerate(idx):
        nj = int(n_per[j])
        if nj == 0:
            continue
        d = np.asarray(d_step[k], np.float64)
        d = d / np.linalg.norm(d)
        u, v = _orthonormal_basis(d)
        ph = phi[off:off + nj]
        out_dirs.append(
            (ct_k[j] * d[None, :]
             + st_k[j] * (np.cos(ph)[:, None] * u[None, :]
                          + np.sin(ph)[:, None] * v[None, :])).astype(np.float32)
        )
        out_pos.append(np.tile(pos[k].astype(np.float32), (nj, 1)))
        out_t.append(np.full(nj, float(t_step[k]), np.float32))
        out_step.append(np.full(nj, k, np.int32))
        off += nj

    return PhotonSoA(
        photon_type=np.ones(n_c, np.int8),
        pos_m=np.concatenate(out_pos),
        dir=np.concatenate(out_dirs),
        t_emit_ns=np.concatenate(out_t),
        step_idx=np.concatenate(out_step),
    )
