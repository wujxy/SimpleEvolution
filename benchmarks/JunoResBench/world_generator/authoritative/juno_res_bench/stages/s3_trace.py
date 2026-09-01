"""Stage 3 trace mode: per-photon optical transport (vectorized).

Replaces the analytic scintillation weights with explicit propagation:
wavelength-dependent absorption / re-emission / Rayleigh scattering in the
bulk, PMT-disc hit or ESR diffuse reflection at the boundary (see
docs/trace_design.md). All photons of one event are advanced in vectorized
"generations" until arrived or dead.

Everything the fast mode folded into eps(r)/sigma_scatter/single-wavelength
emerges here: attenuation + ESR light recycling (uniformity), Rayleigh path
randomization (timing spread), re-emission delay + red shift (timing +
spectrum).
"""

import numpy as np

from ..config import DetectorConfig
from ..geometry import nearest_pmt_indices
from ..optics_tables import (
    abs_length_m,
    lambert_reflect,
    qe_relative,
    rayleigh_length_m,
    rayleigh_rotate,
    reemission_prob,
    REEMISSION_DELAY_NS,
    REEM_LAMBDA_MAX,
    ESR_REFLECTIVITY,
    sample_emission_lambda,
    sample_fluor_lambda,
)
from ..truth import S3Output

C_M_NS = 0.299792458


def _ray_sphere(pos, dirs, radius):
    """Distance to the next boundary crossing along each ray."""
    b = np.einsum("ij,ij->i", pos, dirs)
    c2 = np.einsum("ij,ij->i", pos, pos) - radius**2
    return -b + np.sqrt(np.maximum(b * b - c2, 0.0))


def _pmt_hit_mask(layout, hit_dirs, cfg):
    """Whether boundary hit directions land within a PMT angular disc."""
    near = nearest_pmt_indices(layout, hit_dirs)
    pmt_dir = layout.positions_m[near]
    pmt_dir /= np.linalg.norm(pmt_dir, axis=1, keepdims=True)
    cos_ang = np.einsum("ij,ij->i", hit_dirs, pmt_dir)
    cos_acc = np.cos(np.arctan((cfg.pmt_diameter_m / 2.0) / layout.radius_m))
    return near, cos_ang >= cos_acc


def trace_photons(photons, event, cfg: DetectorConfig, layout, rng, grid=None):
    """Vectorized per-photon transport. Returns per-arrived arrays.

    grid: optional DirectionGrid for the boundary-hit PMT lookup (O(N));
    falls back to exact chunked nearest-PMT search when None.
    """
    n0 = len(photons)
    pos = photons.pos_m.astype(np.float64).copy()
    dirs = photons.dir.astype(np.float64).copy()
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    lam = sample_emission_lambda(rng, n0)
    t = photons.t_emit_ns.astype(np.float64).copy()
    ptype = photons.photon_type.copy()
    src_idx = np.arange(n0)

    v_over_c = 1.0 / (C_M_NS / cfg.ls_refractive_index)   # ns per meter

    arrived = {"pmt_idx": [], "t_arrive": [], "t_tof": [],
               "photon_idx": [], "lam": [], "cos_inc": []}
    from ..geometry import coverage_fraction
    det_scale = 1.0 / coverage_fraction(layout, cfg.pmt_diameter_m)

    # Cherenkov photons already carry a ray direction: same transport.
    active = np.ones(n0, bool)
    n_iter = 0
    while active.any() and n_iter < 20:
        n_iter += 1
        act_idx = np.where(active)[0]
        p, dd, lm = pos[act_idx], dirs[act_idx], lam[act_idx]
        t_act = t[act_idx]
        d_abs = rng.exponential(abs_length_m(lm))
        d_sca = rng.exponential(rayleigh_length_m(lm))
        d_bnd = _ray_sphere(p, dd, layout.radius_m)

        d_abs[d_abs > 1e4] = 1e4     # cap: effectively transparent
        d_sca[d_sca > 1e4] = 1e4
        kind = np.argmin(np.stack([d_abs, d_sca, d_bnd]), axis=0)
        dmin = np.minimum(np.minimum(d_abs, d_sca), d_bnd)

        # advance (written back below)
        pos[act_idx] = p + dmin[:, None] * dd
        t[act_idx] = t_act + dmin * v_over_c

        # absorbed (T1/T2)
        m_abs = kind == 0
        if m_abs.any():
            reem = rng.random(int(m_abs.sum())) < reemission_prob(lm[m_abs])
            idx_abs = act_idx[m_abs]
            active[idx_abs[~reem]] = False
            if reem.any():                       # re-emit: new dir/lambda, delay
                idx_re = idx_abs[reem]
                dirs[idx_re] = _isotropic(rng, int(reem.sum()))
                lam[idx_re] = sample_fluor_lambda(rng, int(reem.sum()))
                t[idx_re] += REEMISSION_DELAY_NS

        # Rayleigh (T3)
        m_sca = kind == 1
        if m_sca.any():
            idx_sca = act_idx[m_sca]
            dirs[idx_sca] = rayleigh_rotate(dirs[idx_sca], rng)

        # boundary (T4/T5/T6)
        m_bnd = kind == 2
        if m_bnd.any():
            idx_bnd = act_idx[m_bnd]
            pb, db = pos[idx_bnd], dirs[idx_bnd]
            hit_dir = pb / np.linalg.norm(pb, axis=1, keepdims=True)
            near = grid.lookup(hit_dir) if grid is not None \
                else nearest_pmt_indices(layout, hit_dir)
            pmt_dir = layout.positions_m[near]
            pmt_dir /= np.linalg.norm(pmt_dir, axis=1, keepdims=True)
            hit = np.einsum("ij,ij->i", hit_dir, pmt_dir) >= np.cos(
                np.arctan((cfg.pmt_diameter_m / 2.0) / layout.radius_m)
            )
            # arrived at a PMT
            ia = idx_bnd[hit]
            arrived["pmt_idx"].append(near[hit].astype(np.int32))
            arrived["t_arrive"].append(t[ia])
            arrived["t_tof"].append(t[ia] - photons.t_emit_ns[ia])
            arrived["photon_idx"].append(ia)
            arrived["lam"].append(lam[ia])
            arrived["cos_inc"].append(np.einsum("ij,ij->i", db[hit], hit_dir[hit]))
            active[ia] = False
            # ESR reflection (T5) / absorption (T6)
            ib = idx_bnd[~hit]
            refl = rng.random(int((~hit).sum())) < ESR_REFLECTIVITY
            active[ib[~refl]] = False
            if refl.any():
                idx_r = ib[refl]
                nrm = -hit_dir[~hit][refl]        # inward normal
                dirs[idx_r] = lambert_reflect(dirs[idx_r], nrm, rng)
                pos[idx_r] = pb[~hit][refl] - nrm * 1e-6   # step inside

    if not arrived["pmt_idx"]:
        return S3Output(
            n_arrived_pmt=np.zeros(layout.n_pmt, np.int64),
            pmt_idx=np.zeros(0, np.int32),
            t_arrive_ns=np.zeros(0, np.float32),
            t_tof_ns=np.zeros(0, np.float32),
            photon_idx=np.zeros(0, np.int64),
            det_scale=np.zeros(0),
            lam_nm=np.zeros(0),
        )

    pmt_idx = np.concatenate(arrived["pmt_idx"])
    return S3Output(
        n_arrived_pmt=np.bincount(pmt_idx, minlength=layout.n_pmt).astype(np.int64),
        pmt_idx=pmt_idx,
        t_arrive_ns=np.concatenate(arrived["t_arrive"]).astype(np.float32),
        t_tof_ns=np.concatenate(arrived["t_tof"]).astype(np.float32),
        photon_idx=np.concatenate(arrived["photon_idx"]).astype(np.int64),
        det_scale=np.full(len(pmt_idx), det_scale),
        lam_nm=np.concatenate(arrived["lam"]),
    )


def _isotropic(rng, n):
    ct = rng.uniform(-1.0, 1.0, n)
    phi = rng.uniform(0.0, 2.0 * np.pi, n)
    st = np.sqrt(1.0 - ct * ct)
    return np.column_stack((st * np.cos(phi), st * np.sin(phi), ct))
