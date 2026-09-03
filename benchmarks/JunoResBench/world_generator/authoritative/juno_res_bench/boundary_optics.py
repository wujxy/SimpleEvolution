"""Vectorized optical properties and ideal dielectric boundary operations."""

import numpy as np

from .optics_tables import abs_length_m, ls_group_index, ls_refractive_index


_CAUCHY = {
    # Synthetic smooth dispersions anchored to representative visible values.
    # LS keeps the Stage-2 curve; acrylic and water are deliberately not copied
    # from JUNOSW private/performance tables.
    "acrylic": (1.49, 0.0055),
    "water": (1.34, 0.0030),
}


def _cauchy(name, lam_nm, group=False):
    n_430, b = _CAUCHY[name]
    lam_um = np.asarray(lam_nm, float) * 1e-3
    a = n_430 - b / 0.43**2
    return a + (3.0 if group else 1.0) * b / lam_um**2


def medium_refractive_index(name, lam_nm):
    """Phase refractive index for one of the three trace media."""
    if name == "ls":
        return ls_refractive_index(lam_nm)
    if name not in _CAUCHY:
        raise ValueError(f"unknown optical medium: {name}")
    return _cauchy(name, lam_nm)


def medium_group_index(name, lam_nm):
    """Group refractive index for photon time of flight."""
    if name == "ls":
        return ls_group_index(lam_nm)
    if name not in _CAUCHY:
        raise ValueError(f"unknown optical medium: {name}")
    return _cauchy(name, lam_nm, group=True)


def medium_absorption_length(name, lam_nm):
    """Synthetic wavelength-dependent bulk absorption length in metres."""
    if name == "ls":
        return abs_length_m(lam_nm)
    lam_nm = np.asarray(lam_nm, float)
    if name == "acrylic":
        plateau, uv, center, width = 50.0, 5.0, 355.0, 30.0
    elif name == "water":
        plateau, uv, center, width = 100.0, 20.0, 365.0, 35.0
    else:
        raise ValueError(f"unknown optical medium: {name}")
    shortwave = 1.0 / (1.0 + np.exp((lam_nm - center) / width))
    return plateau + (uv - plateau) * shortwave


def fresnel_unpolarized(cos_i, n_from, n_to):
    """Return (reflectance, transmitted cosine, TIR) at an ideal interface."""
    cos_i = np.clip(np.asarray(cos_i, float), 0.0, 1.0)
    n_from = np.asarray(n_from, float)
    n_to = np.asarray(n_to, float)
    sin_t2 = (n_from / n_to) ** 2 * np.maximum(0.0, 1.0 - cos_i**2)
    tir = sin_t2 > 1.0
    cos_t = np.sqrt(np.maximum(0.0, 1.0 - sin_t2))
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ((n_from * cos_i - n_to * cos_t)
              / (n_from * cos_i + n_to * cos_t)) ** 2
        rp = ((n_from * cos_t - n_to * cos_i)
              / (n_from * cos_t + n_to * cos_i)) ** 2
    reflectance = np.where(tir, 1.0, 0.5 * (rs + rp))
    return np.clip(reflectance, 0.0, 1.0), cos_t, tir


def dielectric_step(directions, normals, n_from, n_to, uniforms):
    """Sample ideal specular reflection/refraction for outward normals.

    ``normals`` point from the incident medium into the destination medium and
    each incident direction points toward that interface.
    """
    directions = np.asarray(directions, float)
    normals = np.asarray(normals, float)
    cos_i = np.einsum("ij,ij->i", directions, normals)
    if np.any(cos_i < -1e-12):
        raise ValueError("interface normals must point toward destination medium")
    cos_i = np.clip(cos_i, 0.0, 1.0)
    n_from = np.asarray(n_from, float)
    n_to = np.asarray(n_to, float)
    reflectance, cos_t, tir = fresnel_unpolarized(cos_i, n_from, n_to)
    reflected = tir | (np.asarray(uniforms, float) < reflectance)

    out = np.empty_like(directions)
    if reflected.any():
        out[reflected] = (directions[reflected]
                          - 2.0 * cos_i[reflected, None] * normals[reflected])
    transmitted = ~reflected
    if transmitted.any():
        eta = n_from[transmitted] / n_to[transmitted]
        tangent = (directions[transmitted]
                   - cos_i[transmitted, None] * normals[transmitted])
        out[transmitted] = (eta[:, None] * tangent
                            + cos_t[transmitted, None] * normals[transmitted])
    out /= np.linalg.norm(out, axis=1, keepdims=True)
    return out, reflected
