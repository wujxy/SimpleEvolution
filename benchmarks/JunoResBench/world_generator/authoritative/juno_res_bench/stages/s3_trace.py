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

from ..boundary_optics import (
    dielectric_step,
    medium_absorption_length,
    medium_group_index,
    medium_refractive_index,
)
from ..config import DetectorConfig
from ..detector_structures import structure_transmission
from ..geometry import nearest_pmt_indices
from ..optics_tables import (
    lambert_reflect,
    rayleigh_length_m,
    rayleigh_rotate,
    reemission_prob,
    REEMISSION_DELAY_NS,
    REEM_LAMBDA_MAX,
    ESR_REFLECTIVITY,
    sample_fluor_lambda,
)
from ..truth import S3Output

C_M_NS = 0.299792458


def _ray_sphere(pos, dirs, radius):
    """Smallest positive ray distance to a sphere, or infinity on a miss."""
    b = np.einsum("ij,ij->i", pos, dirs)
    c2 = np.einsum("ij,ij->i", pos, pos) - radius**2
    disc = b * b - c2
    root = np.sqrt(np.maximum(disc, 0.0))
    near, far = -b - root, -b + root
    eps = 1e-7
    distance = np.where(near > eps, near, np.where(far > eps, far, np.inf))
    return np.where(disc >= 0.0, distance, np.inf)


def _next_boundary(pos, dirs, medium, ls_radius, acrylic_radius, pmt_radius):
    """Return distance and interface code (0 LS/acrylic, 1 acrylic/water, 2 PMT)."""
    distances = np.full(len(pos), np.inf)
    boundary = np.full(len(pos), -1, np.int8)
    for code, radius, valid in (
        (0, ls_radius, medium <= 1),
        (1, acrylic_radius, medium >= 1),
        (2, pmt_radius, medium == 2),
    ):
        candidate = _ray_sphere(pos, dirs, radius)
        use = valid & (candidate < distances)
        distances[use] = candidate[use]
        boundary[use] = code
    return distances, boundary


def _medium_values(medium, lam, function):
    out = np.empty(len(lam), float)
    for code, name in enumerate(("ls", "acrylic", "water")):
        mask = medium == code
        if mask.any():
            out[mask] = function(name, lam[mask])
    return out


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
    lam = photons.wavelength_nm.astype(np.float64).copy()
    if not np.isfinite(lam).all():
        raise ValueError("trace mode requires finite Stage-2 photon wavelengths")
    t = photons.t_emit_ns.astype(np.float64).copy()
    ptype = photons.photon_type.copy()
    src_idx = np.arange(n0)
    medium = np.zeros(n0, np.int8)  # 0 LS, 1 acrylic, 2 water

    ls_radius = cfg.ls_radius_m
    acrylic_radius = ls_radius + cfg.acrylic_thickness_m
    pmt_radius = layout.radius_m
    if not (0.0 < ls_radius < acrylic_radius < pmt_radius):
        raise ValueError("trace geometry requires LS < acrylic < PMT radii")
    if np.any(np.linalg.norm(pos, axis=1) >= ls_radius):
        raise ValueError("trace photons must originate inside the LS sphere")

    arrived = {"pmt_idx": [], "t_arrive": [], "t_tof": [],
               "photon_idx": [], "lam": [], "dir_at_pmt": []}
    from ..geometry import coverage_fraction
    det_scale = 1.0 / coverage_fraction(layout, cfg.pmt_diameter_m)

    # Cherenkov photons already carry a ray direction: same transport.
    active = np.ones(n0, bool)
    n_iter = 0
    while active.any() and n_iter < 40:
        n_iter += 1
        act_idx = np.where(active)[0]
        p, dd, lm = pos[act_idx], dirs[act_idx], lam[act_idx]
        med = medium[act_idx]
        t_act = t[act_idx]
        d_abs = rng.exponential(_medium_values(
            med, lm, medium_absorption_length
        ))
        d_sca = np.full(len(act_idx), np.inf)
        in_ls = med == 0
        d_sca[in_ls] = rng.exponential(rayleigh_length_m(lm[in_ls]))
        d_bnd, boundary = _next_boundary(
            p, dd, med, ls_radius, acrylic_radius, pmt_radius
        )

        d_abs[d_abs > 1e4] = 1e4     # cap: effectively transparent
        d_sca[d_sca > 1e4] = 1e4
        kind = np.argmin(np.stack([d_abs, d_sca, d_bnd]), axis=0)
        dmin = np.minimum(np.minimum(d_abs, d_sca), d_bnd)

        # advance (written back below)
        pos[act_idx] = p + dmin[:, None] * dd
        t[act_idx] = t_act + dmin * _medium_values(
            med, lm, medium_group_index
        ) / C_M_NS

        # absorbed (T1/T2)
        m_abs = kind == 0
        if m_abs.any():
            idx_abs = act_idx[m_abs]
            can_reemit = med[m_abs] == 0
            reem = np.zeros(int(m_abs.sum()), bool)
            reem[can_reemit] = (
                rng.random(int(can_reemit.sum()))
                < reemission_prob(lm[m_abs][can_reemit])
            )
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

        # boundary: ideal LS/acrylic/water dielectric interfaces, then PMT shell
        m_bnd = kind == 2
        if m_bnd.any():
            idx_bnd = act_idx[m_bnd]
            pb, db = pos[idx_bnd], dirs[idx_bnd]
            hit_dir = pb / np.linalg.norm(pb, axis=1, keepdims=True)
            bcode = boundary[m_bnd]

            dielectric = bcode < 2
            if dielectric.any():
                idx_d = idx_bnd[dielectric]
                radial = hit_dir[dielectric]
                med_from = medium[idx_d]
                outward = np.einsum(
                    "ij,ij->i", dirs[idx_d], radial
                ) > 0.0
                normals = np.where(outward[:, None], radial, -radial)
                med_to = np.where(outward, med_from + 1, med_from - 1)
                n_from = _medium_values(
                    med_from, lam[idx_d], medium_refractive_index
                )
                n_to = _medium_values(
                    med_to, lam[idx_d], medium_refractive_index
                )
                new_dir, reflected = dielectric_step(
                    dirs[idx_d], normals, n_from, n_to,
                    rng.random(len(idx_d)),
                )
                dirs[idx_d] = new_dir
                medium[idx_d[~reflected]] = med_to[~reflected]
                pos[idx_d] += new_dir * 1e-6

                crosses_structures = (
                    (~reflected) & outward & (med_from == 1) & (med_to == 2)
                )
                if crosses_structures.any():
                    idx_s = idx_d[crosses_structures]
                    transmission = structure_transmission(
                        pb[dielectric][crosses_structures], acrylic_radius,
                        enabled=cfg.fixed_structures,
                    )
                    blocked = rng.random(len(idx_s)) >= transmission
                    active[idx_s[blocked]] = False

            at_pmt = ~dielectric
            if at_pmt.any():
                idx_pmt = idx_bnd[at_pmt]
                pmt_hit_dir = hit_dir[at_pmt]
                pmt_arrival_dir = db[at_pmt]
                near = grid.lookup(pmt_hit_dir) if grid is not None \
                    else nearest_pmt_indices(layout, pmt_hit_dir)
                pmt_dir = layout.positions_m[near]
                pmt_dir /= np.linalg.norm(pmt_dir, axis=1, keepdims=True)
                hit = np.einsum("ij,ij->i", pmt_hit_dir, pmt_dir) >= np.cos(
                    np.arctan((cfg.pmt_diameter_m / 2.0) / layout.radius_m)
                )
                ia = idx_pmt[hit]
                arrived["pmt_idx"].append(near[hit].astype(np.int32))
                arrived["t_arrive"].append(t[ia])
                arrived["t_tof"].append(t[ia] - photons.t_emit_ns[ia])
                arrived["photon_idx"].append(ia)
                arrived["lam"].append(lam[ia])
                arrived["dir_at_pmt"].append(pmt_arrival_dir[hit])
                active[ia] = False
                ib = idx_pmt[~hit]
                refl = rng.random(int((~hit).sum())) < ESR_REFLECTIVITY
                active[ib[~refl]] = False
                if refl.any():
                    idx_r = ib[refl]
                    nrm = -pmt_hit_dir[~hit][refl]
                    dirs[idx_r] = lambert_reflect(dirs[idx_r], nrm, rng)
                    pos[idx_r] += dirs[idx_r] * 1e-6

    if not arrived["pmt_idx"]:
        return S3Output(
            n_arrived_pmt=np.zeros(layout.n_pmt, np.int64),
            pmt_idx=np.zeros(0, np.int32),
            t_arrive_ns=np.zeros(0, np.float32),
            t_tof_ns=np.zeros(0, np.float32),
            photon_idx=np.zeros(0, np.int64),
            det_scale=np.zeros(0),
            lam_nm=np.zeros(0),
            dir_at_pmt=np.zeros((0, 3), np.float32),
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
        dir_at_pmt=np.concatenate(arrived["dir_at_pmt"]).astype(np.float32),
    )


def _isotropic(rng, n):
    ct = rng.uniform(-1.0, 1.0, n)
    phi = rng.uniform(0.0, 2.0 * np.pi, n)
    st = np.sqrt(1.0 - ct * ct)
    return np.column_stack((st * np.cos(phi), st * np.sin(phi), ct))
