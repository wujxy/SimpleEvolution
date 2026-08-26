"""Stage 3 — optical response: photon -> arrived at which PMT, when.

Pure optics: geometric acceptance (C4/C5), effective attenuation folded
into the calibrated radial factor applied at detection (C1/C2/C6/C9), and
scattering/re-emission timing spread (C2/C3). No QE/PDE/CE here.

Two transport paths:
  scintillation (isotropic): analytic per-PMT weights w_i ∝ A_proj/d²,
      Multinomial assignment — every photon is assigned, the ~25% geometric
      miss is folded into the detection probability (stage 4);
  Cherenkov (directional cone): per-photon ray-sphere intersection, the hit
      point is matched to the nearest PMT and must land within the PMT's
      angular radius — the geometric miss is EXPLICIT here, so arrived
      photons carry det_scale = 1/coverage to keep the two paths
      statistically consistent (stage 4 divides it out).
"""

import numpy as np

from ..config import DetectorConfig
from ..geometry import coverage_fraction, nearest_pmt_indices, tof_ns
from ..truth import PhotonSoA, S1Output, S3Output

C_MM_NS = 299.792458
M_PER_NS = 0.299792458   # c in m/ns; path_m = tof_ns * M_PER_NS / n_ls


def _path_m(tof_ns_arr: np.ndarray, n_ls: float) -> np.ndarray:
    return np.asarray(tof_ns_arr) * 0.299792458 / n_ls


def scint_weights(layout, vertex_m: np.ndarray, cfg: DetectorConfig) -> np.ndarray:
    """Normalized per-PMT arrival weights w_i ∝ A_proj(cosθ_inc)/d².

    Pure geometry: the 20-inch disc foreshortened by the incidence angle.
    A vertex inside the sphere always illuminates PMT fronts, so
    cosθ_inc = -(photon direction . inward normal) > 0.
    """
    pos = layout.positions_m
    rel = pos - vertex_m
    d = np.linalg.norm(rel, axis=1)
    cos_inc = -np.einsum("ij,ij->i", rel, layout.inward_normals) / d
    a_proj = np.pi * (cfg.pmt_diameter_m / 2.0) ** 2
    w = np.clip(cos_inc, 0.0, None) * a_proj / np.maximum(d**2, 1e-9)
    return w / w.sum()


def _scatter_spread(d_path_m: np.ndarray, cfg: DetectorConfig, rng) -> np.ndarray:
    """Rayleigh + re-emission equivalent timing spread (C2/C3)."""
    if cfg.a_scatter_ns_per_m <= 0.0:
        return 0.0
    return rng.normal(0.0, cfg.a_scatter_ns_per_m * d_path_m)


def _run_scint(photons, cfg, layout, rng, vtx):
    scint = photons.photon_type == 0
    n_scint = int(scint.sum())
    if n_scint == 0:
        return None
    w = scint_weights(layout, vtx, cfg)
    n_arrived = rng.multinomial(n_scint, w)
    pmt_idx = np.repeat(np.arange(layout.n_pmt, dtype=np.int32), n_arrived)
    tofs = tof_ns(layout.positions_m, vtx, cfg.ls_refractive_index)
    tof_ph = tofs[pmt_idx]
    t_arrive = photons.t_emit_ns[scint] + tof_ph.astype(np.float32) + _scatter_spread(
        _path_m(tof_ph, cfg.ls_refractive_index), cfg, rng
    )
    return {
        "pmt_idx": pmt_idx,
        "t_arrive_ns": t_arrive.astype(np.float32),
        "t_tof_ns": tof_ph.astype(np.float32),
        "photon_idx": np.where(scint)[0].astype(np.int64),
        "det_scale": np.ones(len(pmt_idx)),
    }


def _run_cherenkov(photons, cfg, layout, rng, vtx):
    """Ray-sphere intersection path (directional photons)."""
    cher = photons.photon_type == 1
    n_cher = int(cher.sum())
    if n_cher == 0:
        return None
    d = photons.dir[cher].astype(np.float64)
    d /= np.linalg.norm(d, axis=1, keepdims=True)

    r_vtx = np.linalg.norm(vtx)
    b = d @ vtx
    disc = b * b + (layout.radius_m**2 - r_vtx**2)   # c2 = |vtx|^2 - R^2 < 0
    t_path = -b + np.sqrt(np.maximum(disc, 0.0))      # vertex -> hit point, m
    hit = vtx[None, :] + t_path[:, None] * d
    hit_dir = hit / np.linalg.norm(hit, axis=1, keepdims=True)

    near = nearest_pmt_indices(layout, hit_dir)
    pmt_dir = layout.positions_m[near]
    pmt_dir /= np.linalg.norm(pmt_dir, axis=1, keepdims=True)
    cos_ang = np.einsum("ij,ij->i", hit_dir, pmt_dir)
    cos_acc = np.cos(np.arctan((cfg.pmt_diameter_m / 2.0) / layout.radius_m))
    arrived = (disc > 0) & (cos_ang >= cos_acc)

    # explicit geometric miss -> compensate the coverage folded into p_det
    det_scale = np.full(n_cher, 1.0 / coverage_fraction(layout, cfg.pmt_diameter_m))

    tof = t_path / (C_MM_NS / 1000.0 / cfg.ls_refractive_index)  # m -> ns
    t_arrive = photons.t_emit_ns[cher] + tof + _scatter_spread(
        _path_m(tof, cfg.ls_refractive_index), cfg, rng
    )

    return {
        "pmt_idx": near[arrived].astype(np.int32),
        "t_arrive_ns": t_arrive[arrived].astype(np.float32),
        "t_tof_ns": tof[arrived].astype(np.float32),
        "photon_idx": np.where(cher)[0][arrived].astype(np.int64),
        "det_scale": det_scale[arrived],
    }


def run_s3(
    photons: PhotonSoA,
    s1: S1Output,
    event,
    cfg: DetectorConfig,
    layout,
    rng: np.random.Generator,
) -> S3Output:
    vtx = event.vertex_m
    # rng order note: scintillation draws first, Cherenkov second — enabling
    # Cherenkov never perturbs the scintillation stream.
    parts = [
        p
        for p in (
            _run_scint(photons, cfg, layout, rng, vtx),
            _run_cherenkov(photons, cfg, layout, rng, vtx),
        )
        if p is not None
    ]
    if not parts:
        return S3Output(
            n_arrived_pmt=np.zeros(layout.n_pmt, np.int64),
            pmt_idx=np.zeros(0, np.int32),
            t_arrive_ns=np.zeros(0, np.float32),
            t_tof_ns=np.zeros(0, np.float32),
            photon_idx=np.zeros(0, np.int64),
            det_scale=np.zeros(0),
        )

    pmt_idx = np.concatenate([p["pmt_idx"] for p in parts])
    n_arrived_pmt = np.bincount(pmt_idx, minlength=layout.n_pmt).astype(np.int64)
    return S3Output(
        n_arrived_pmt=n_arrived_pmt,
        pmt_idx=pmt_idx,
        t_arrive_ns=np.concatenate([p["t_arrive_ns"] for p in parts]),
        t_tof_ns=np.concatenate([p["t_tof_ns"] for p in parts]),
        photon_idx=np.concatenate([p["photon_idx"] for p in parts]),
        det_scale=np.concatenate([p["det_scale"] for p in parts]),
    )
