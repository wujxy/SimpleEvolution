"""Optical tables for the per-photon trace mode (toy parameterizations).

Provenance: shapes anchored to JUNO-SW J26.4.1 OpticalProperty.icc /
PMTSimParamSvc tables (see docs/trace_design.md); exact tabulated curves
are deliberately replaced by smooth analytic forms — the toy keeps the
physics (wavelength dependence, red shift, recycling) without the data
volume.
"""

import numpy as np

# ---- emission spectrum (scintillation + re-emission), nm ---------------
# Two-component toy: 70% bisMSB-shifted (430 nm) + 30% PPO-like UV
# (370 nm) — the UV component overlaps the fluor absorption band, so a
# fraction of the light is absorbed and re-emitted red-shifted (T2).
LAM_EMIT_CENTER = 430.0
LAM_EMIT_SIGMA = 28.0
LAM_UV_CENTER = 370.0
LAM_UV_SIGMA = 25.0
LAM_EMIT_MIN = 320.0    # short-λ cutoff (LAB absorption edge)
LAM_UV_FRACTION = 0.30


def _trunc_gauss(rng, n, center, sigma):
    lam = rng.normal(center, sigma, n)
    bad = lam < LAM_EMIT_MIN
    while bad.any():
        lam[bad] = rng.normal(center, sigma, int(bad.sum()))
        bad = lam < LAM_EMIT_MIN
    return lam


def sample_emission_lambda(rng, n):
    """Primary scintillation wavelength: 430 nm (70%) + 370 nm UV (30%)."""
    is_uv = rng.random(n) < LAM_UV_FRACTION
    n_uv = int(is_uv.sum())
    lam = np.empty(n)
    lam[is_uv] = _trunc_gauss(rng, n_uv, LAM_UV_CENTER, LAM_UV_SIGMA)
    lam[~is_uv] = _trunc_gauss(rng, n - n_uv, LAM_EMIT_CENTER, LAM_EMIT_SIGMA)
    return lam


def sample_fluor_lambda(rng, n):
    """Re-emission (fluor) spectrum: red-shifted, 430 nm only (T2)."""
    return _trunc_gauss(rng, n, LAM_EMIT_CENTER, LAM_EMIT_SIGMA)


# ---- bulk LS absorption (T1), m ----------------------------------------
ABS_LEN_PLATEAU = 100.0   # m, long-λ plateau (>420 nm)
ABS_LEN_UV = 2.0          # m, deep in the fluor absorption band (~350 nm)
ABS_UV_CENTER = 350.0
ABS_UV_WIDTH = 35.0


def abs_length_m(lam_nm):
    """Effective LS absorption length: long plateau, fluor band in UV."""
    lam_nm = np.asarray(lam_nm, float)
    uv = 1.0 / (1.0 + np.exp((lam_nm - ABS_UV_CENTER) / ABS_UV_WIDTH))
    # plateau -> ABS_LEN_UV across the UV band, smooth logistic blend
    return ABS_LEN_PLATEAU + (ABS_LEN_UV - ABS_LEN_PLATEAU) * uv


# ---- Rayleigh scattering (T3), m ---------------------------------------
RAYLEIGH_430 = 42.0       # m, JUNO value at 430 nm


def rayleigh_length_m(lam_nm):
    lam_nm = np.asarray(lam_nm, float)
    return RAYLEIGH_430 * (430.0 / lam_nm) ** 4


# ---- re-emission (T2) ---------------------------------------------------
REEMISSION_PROB = 0.80    # absorbed UV photon re-emits (JUNO plateau)
REEMISSION_DELAY_NS = 1.5
REEM_LAMBDA_MAX = 420.0   # only fluor-band absorption re-emits


def reemission_prob(lam_nm):
    lam_nm = np.asarray(lam_nm, float)
    return np.where(lam_nm < REEM_LAMBDA_MAX, REEMISSION_PROB, 0.0)


# ---- ESR reflector (T5) -------------------------------------------------
ESR_REFLECTIVITY = 0.96


def rayleigh_rotate(dirs, rng):
    """Elastic Rayleigh deflection: phase function (1 + cos^2 theta)/2.

    Sampled by rejection over cos_theta; azimuth uniform. dirs is (N, 3)
    unit vectors; returns rotated unit vectors.
    """
    n = len(dirs)
    c = rng.uniform(-1.0, 1.0, n)
    ok = rng.uniform(0.0, 1.0, n) < (1.0 + c * c) / 2.0
    while not ok.all():
        m = ~ok
        c[m] = rng.uniform(-1.0, 1.0, int(m.sum()))
        ok[m] = rng.uniform(0.0, 1.0, int(m.sum())) < (1.0 + c[m] ** 2) / 2.0
    phi = rng.uniform(0.0, 2.0 * np.pi, n)
    st = np.sqrt(1.0 - c * c)
    # build local frame per photon
    ref = np.where(
        np.abs(dirs[:, 2:3]) < 0.9,
        np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0]),
    )
    u = np.cross(dirs, ref)
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    v = np.cross(dirs, u)
    out = (
        c[:, None] * dirs
        + (st * np.cos(phi))[:, None] * u
        + (st * np.sin(phi))[:, None] * v
    )
    return out / np.linalg.norm(out, axis=1, keepdims=True)


def lambert_reflect(dirs, normals, rng):
    """Diffuse (Lambert) reflection at the ESR surface.

    dirs point outward (incident); normals are the local surface normals
    pointing inward. Returns reflected unit directions (into the sphere).
    """
    n = len(dirs)
    ct = rng.uniform(0.0, 1.0, n)          # cos(theta) from the normal
    phi = rng.uniform(0.0, 2.0 * np.pi, n)
    st = np.sqrt(1.0 - ct * ct)
    ref = np.where(
        np.abs(normals[:, 2:3]) < 0.9,
        np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0]),
    )
    u = np.cross(normals, ref)
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    v = np.cross(normals, u)
    out = (
        ct[:, None] * normals
        + (st * np.cos(phi))[:, None] * u
        + (st * np.sin(phi))[:, None] * v
    )
    return out / np.linalg.norm(out, axis=1, keepdims=True)


# ---- QE wavelength shape (relative, detection side) ---------------------
QE_SHAPE_CENTER = 390.0   # nm, NNVT QE peak
QE_SHAPE_SIGMA_LO = 45.0  # nm, red-side width
QE_SHAPE_SIGMA_HI = 30.0  # nm, blue-side width


def qe_relative(lam_nm):
    """QE(lambda)/QE(peak): asymmetric Gaussian around the 390 nm peak."""
    lam_nm = np.asarray(lam_nm, float)
    sig = np.where(lam_nm < QE_SHAPE_CENTER, QE_SHAPE_SIGMA_HI, QE_SHAPE_SIGMA_LO)
    return np.exp(-0.5 * ((lam_nm - QE_SHAPE_CENTER) / sig) ** 2)


# Mean QE shape over the emission spectrum: normalizes p_det(lambda) so the
# calibrated total yield is preserved (mean factor = 1 over emitted photons).
QE_NORM = float(np.mean(
    qe_relative(sample_emission_lambda(np.random.default_rng(42), 200000))
))
