"""Stage 4 — photon detection: arrived photon -> photoelectron.

Per-photon binomial thinning (D4) with detection efficiency
    eff = p_det(r) * CE(θ_inc) * (1 + pde_delta_i) / <CE*(1+δ)>
where p_det(r) carries the calibrated radial light-collection factor
(C1/C2/C6/C9 folded), CE(θ) the angular collection efficiency (D2, NNVT
table) and pde_delta_i the per-PMT PDE offset (D3). The event-wise
normalization by <CE*(1+δ)> keeps the calibrated total pe yield anchored
while introducing per-PMT / angular variation. Independent per-photon
thinning is jointly identical to per-PMT Binomial counts.

Stream-isolation note: random draws are taken per photon type (scintillation
first, then Cherenkov), so enabling/disabling the Cherenkov chain never
perturbs the scintillation detection draws.
"""

import numpy as np

from ..config import DetectorConfig
from ..truth import DetectorCalibration, S3Output, S4Output


def ce_factor(cfg: DetectorConfig, cos_inc: np.ndarray) -> np.ndarray:
    """CE(θ_inc) interpolated from the NNVT table (D2). θ in degrees."""
    theta_deg = np.degrees(np.arccos(np.clip(cos_inc, -1.0, 1.0)))
    return np.interp(
        theta_deg, np.asarray(cfg.ce_theta_deg), np.asarray(cfg.ce_eff)
    )


def _base_detection_probability(s3, event, cfg, photon_pos):
    """Detection scale before angular, per-PMT and wavelength response."""
    if cfg.optics_mode == "trace":
        p_det = cfg.p_det_center
    elif photon_pos is not None:
        pos_ph = np.asarray(photon_pos, np.float64)[s3.photon_idx]
        r_ph = np.linalg.norm(pos_ph, axis=1)
        p_det = cfg.p_det_center * cfg.mu_pe_ratio(np.maximum(r_ph, 1e-6))
    else:
        r = float(np.linalg.norm(event.vertex_m))
        p_det = cfg.p_det_center * cfg.mu_pe_ratio(max(r, 1e-6))
    scale = s3.det_scale if s3.det_scale is not None else np.ones(len(s3.pmt_idx))
    return p_det * scale


def _arrival_cosine(s3, event, layout, pos_ph, arrived_type, photon_dir):
    """Cosine of PMT incidence angle, preferring the final traced ray."""
    n_in = layout.inward_normals[s3.pmt_idx]
    if s3.dir_at_pmt is not None:
        direction = np.asarray(s3.dir_at_pmt, np.float64).copy()
        direction /= np.linalg.norm(direction, axis=1, keepdims=True)
        return -np.einsum("ij,ij->i", direction, n_in)

    if pos_ph is not None:
        direction = layout.positions_m[s3.pmt_idx] - pos_ph
    else:
        direction = layout.positions_m[s3.pmt_idx] - event.vertex_m[None, :]
    direction /= np.linalg.norm(direction, axis=1, keepdims=True)
    cos_inc = -np.einsum("ij,ij->i", direction, n_in)
    if photon_dir is not None:
        emitted = np.asarray(photon_dir, np.float64)[s3.photon_idx]
        emitted /= np.linalg.norm(emitted, axis=1, keepdims=True)
        cher_cos = -np.einsum("ij,ij->i", emitted, n_in)
        cos_inc = np.where(arrived_type == 1, cher_cos, cos_inc)
    return cos_inc


def run_s4(
    s3: S3Output,
    event,
    cfg: DetectorConfig,
    calib: DetectorCalibration,
    rng: np.random.Generator,
    photon_type: np.ndarray = None,
    photon_dir: np.ndarray = None,
    layout=None,
    photon_pos: np.ndarray = None,
) -> S4Output:
    pos_ph = (np.asarray(photon_pos, np.float64)[s3.photon_idx]
              if photon_pos is not None else None)
    p_det_ph = _base_detection_probability(s3, event, cfg, photon_pos)

    n_arr = len(s3.pmt_idx)
    n_pmt = len(s3.n_arrived_pmt)
    if n_arr == 0 or not (p_det_ph > 0).any():
        empty = np.zeros(0, np.int32)
        return S4Output(
            pmt_idx=empty,
            t_hit_ns=np.zeros(0, np.float32),
            pe_type=np.zeros(0, np.int8),
            n_pe_pmt=np.zeros(n_pmt, np.int64),
            arrived_idx=np.zeros(0, np.int64),
        )

    if photon_type is None:
        photon_type = np.zeros(n_arr, np.int8)
    arrived_type = photon_type[s3.photon_idx]

    # ---- incidence angle and CE(θ) (D2) ---------------------------------
    cos_inc = _arrival_cosine(
        s3, event, layout, pos_ph, arrived_type, photon_dir
    )
    ce = ce_factor(cfg, cos_inc)

    # ---- per-PMT PDE offset (D3) -----------------------------------------
    pde = 1.0 + calib.pde_delta[s3.pmt_idx]

    # ---- wavelength-dependent QE (trace mode, T1 complement) --------------
    # trace_det_norm compensates the explicit absorption losses (the fast
    # mode folds them into p_det) so the center-pe anchor holds in both modes
    lam_factor = 1.0
    if getattr(s3, "lam_nm", None) is not None and len(s3.lam_nm):
        from ..optics_tables import QE_NORM, qe_relative
        lam_factor = (cfg.trace_det_norm * qe_relative(s3.lam_nm) / QE_NORM)

    # CE(0 deg)=1 at the center vertex keeps the calibrated yield anchored;
    # off-center events get the physical average-CE suppression (no event-wise
    # normalization — that would erase the real angular light loss)
    p_det_ph = np.clip(p_det_ph * ce * pde * lam_factor, 0.0, 1.0)

    # ---- per-type thinning blocks (stream isolation) ----------------------
    det_mask = np.zeros(n_arr, bool)
    generic_response = np.all(calib.pmt_model == -1)
    tts_residual_all = np.zeros(n_arr)
    for ptype in (0, 1):
        m = arrived_type == ptype
        n_blk = int(m.sum())
        if n_blk == 0:
            continue
        det_mask[m] = rng.random(n_blk) < p_det_ph[m]
        if generic_response:
            tts_residual_all[m] = (
                rng.normal(0.0, 1.0, n_blk)
                * calib.tts_sigma_ns[s3.pmt_idx[m]]
            )
        else:
            from ..pmt_response import sample_transit_time
            tts_residual_all[m] = sample_transit_time(
                calib, s3.pmt_idx[m], rng
            )

    idx = np.where(det_mask)[0]
    pmt_idx = s3.pmt_idx[idx]
    n_det = len(idx)
    t_arr = s3.t_arrive_ns[idx].astype(np.float64)

    # TTS (D5) + static per-PMT transit time offset (E2)
    t_hit = t_arr + tts_residual_all[idx] + calib.time_offset_ns[pmt_idx]

    n_pe_pmt = np.bincount(pmt_idx, minlength=n_pmt).astype(np.int64)
    pe_type = photon_type[s3.photon_idx[idx]].astype(np.int8)
    return S4Output(
        pmt_idx=pmt_idx,
        t_hit_ns=t_hit.astype(np.float32),
        pe_type=pe_type,
        n_pe_pmt=n_pe_pmt,
        arrived_idx=idx.astype(np.int64),
    )
