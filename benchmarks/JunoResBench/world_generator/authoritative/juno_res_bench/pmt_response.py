"""Synthetic JUNO-like LPMT response, constrained by public measurements.

Only PMT positions and type labels come from the installed JUNO geometry.
No per-tube JUNOSW performance/calibration database is read.  The detector
truth below is sampled reproducibly from type-level public anchors.
"""

from dataclasses import dataclass

import numpy as np

from .geometry import PMT_HAMAMATSU, PMT_HIGHQE_NNVT, PMT_NNVT


@dataclass(frozen=True)
class PMTTypeProfile:
    pde_mean: float
    pde_rel_sigma: float
    dcr_mean_hz: float
    dcr_rel_sigma: float
    tts_sigma_ns: float
    spe_charge_resolution: float
    gain_rel_sigma: float
    time_offset_sigma_ns: float


# Means are from JUNO's published mass-characterization results. Widths of
# per-tube populations and the time-offset/gain residuals are explicit model
# choices because the underlying tube database is not public.
TYPE_PROFILES = {
    PMT_HAMAMATSU: PMTTypeProfile(0.285, 0.035, 15_300.0, 0.30, 1.3, 0.279, 0.04, 0.8),
    PMT_NNVT: PMTTypeProfile(0.273, 0.045, 31_000.0, 0.35, 7.0, 0.332, 0.04, 1.2),
    PMT_HIGHQE_NNVT: PMTTypeProfile(0.313, 0.045, 31_000.0, 0.35, 7.0, 0.332, 0.04, 1.2),
}


def _positive_population(rng, mean, rel_sigma, size):
    """Log-normal population parameterized by arithmetic mean and CV."""
    sigma = np.sqrt(np.log1p(rel_sigma * rel_sigma))
    mu = np.log(mean) - 0.5 * sigma * sigma
    return rng.lognormal(mu, sigma, size)


def build_type_aware_response(cfg, layout, rng):
    """Return synthetic per-PMT response arrays for a typed layout."""
    model = np.asarray(layout.pmt_model, dtype=np.int8)
    n = layout.n_pmt
    raw_pde = np.empty(n)
    gain = np.empty(n)
    offset = np.empty(n)
    dcr = np.empty(n)
    tts = np.empty(n)
    spe_resolution = np.empty(n)

    for code, profile in TYPE_PROFILES.items():
        mask = model == code
        count = int(mask.sum())
        if not count:
            continue
        raw_pde[mask] = _positive_population(
            rng, profile.pde_mean, profile.pde_rel_sigma, count
        )
        gain[mask] = np.clip(
            rng.normal(1.0, profile.gain_rel_sigma, count), 0.81, 1.19
        )
        offset[mask] = rng.normal(0.0, profile.time_offset_sigma_ns, count)
        dcr[mask] = _positive_population(
            rng, profile.dcr_mean_hz, profile.dcr_rel_sigma, count
        )
        tts[mask] = _positive_population(rng, profile.tts_sigma_ns, 0.05, count)
        spe_resolution[mask] = _positive_population(
            rng, profile.spe_charge_resolution, 0.04, count
        )

    # mu_pe_per_mev_center is the whole-detector average response anchor.
    pde_factor = raw_pde / raw_pde.mean()
    nnvt = (model == PMT_NNVT) | (model == PMT_HIGHQE_NNVT)
    satellite_prob = np.where(nnvt, 0.25, 0.0)
    core_sigma = np.where(nnvt, 0.50 * tts, tts)
    satellite_sigma = np.where(nnvt, 0.30 * tts, 0.0)
    # Symmetric satellite peaks preserve zero mean and the target RMS TTS.
    numerator = tts**2 - (1.0 - satellite_prob) * core_sigma**2 \
        - satellite_prob * satellite_sigma**2
    satellite_offset = np.sqrt(
        np.divide(
            np.maximum(numerator, 0.0), satellite_prob,
            out=np.zeros_like(numerator), where=satellite_prob > 0,
        )
    )
    return {
        "pde_delta": pde_factor - 1.0,
        "gain": gain,
        "time_offset_ns": offset,
        "tts_sigma_ns": tts,
        "dark_rate_hz": dcr,
        "pmt_model": model.copy(),
        "spe_charge_resolution": spe_resolution,
        "tts_core_sigma_ns": core_sigma,
        "tts_satellite_prob": satellite_prob,
        "tts_satellite_offset_ns": satellite_offset,
        "tts_satellite_sigma_ns": satellite_sigma,
    }


def sample_transit_time(calib, pmt_idx, rng):
    """Sample HPK Gaussian or NNVT multi-component transit residuals."""
    pmt_idx = np.asarray(pmt_idx, dtype=np.int64)
    n = len(pmt_idx)
    residual = rng.normal(0.0, calib.tts_core_sigma_ns[pmt_idx], n)
    satellite = rng.random(n) < calib.tts_satellite_prob[pmt_idx]
    count = int(satellite.sum())
    if count:
        ids = pmt_idx[satellite]
        sign = np.where(rng.random(count) < 0.5, -1.0, 1.0)
        residual[satellite] = rng.normal(
            sign * calib.tts_satellite_offset_ns[ids],
            calib.tts_satellite_sigma_ns[ids],
        )
    return residual


def sample_spe_charge(calib, pmt_idx, rng):
    """Sample unit-mean SPE charge with type-dependent distribution shape."""
    pmt_idx = np.asarray(pmt_idx, dtype=np.int64)
    model = calib.pmt_model[pmt_idx]
    resolution = calib.spe_charge_resolution[pmt_idx]
    out = np.empty(len(pmt_idx))
    hpk = model == PMT_HAMAMATSU
    if hpk.any():
        out[hpk] = rng.normal(1.0, resolution[hpk])
    nnvt = ~hpk
    if nnvt.any():
        sigma = np.sqrt(np.log1p(resolution[nnvt] ** 2))
        out[nnvt] = rng.lognormal(-0.5 * sigma**2, sigma)
    np.clip(out, 1e-4, None, out=out)
    return out
