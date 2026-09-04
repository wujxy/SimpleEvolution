"""Synthetic type-level LPMT surface and photocathode response families."""

import numpy as np

from .geometry import PMT_HAMAMATSU, PMT_HIGHQE_NNVT, PMT_NNVT


def pmt_surface_reflectance(model, wavelength_nm, cos_incidence):
    """Unconditional water-facing PMT reflectance before PE conversion."""
    model = np.asarray(model)
    wavelength_nm = np.asarray(wavelength_nm, float)
    cos_incidence = np.clip(np.asarray(cos_incidence, float), 0.0, 1.0)
    base = np.select(
        [model == PMT_HAMAMATSU, model == PMT_NNVT,
         model == PMT_HIGHQE_NNVT],
        [0.07, 0.13, 0.12], default=0.10,
    )
    spectral = 0.025 * ((wavelength_nm - 430.0) / 130.0) ** 2
    grazing = 0.45 * (1.0 - cos_incidence) ** 2
    return np.clip(base + spectral + grazing, 0.02, 0.80)


def photocathode_collection_factor(model, radius_fraction, azimuth_rad):
    """Relative collection at a normalized photocathode landing position."""
    model = np.asarray(model)
    rho = np.clip(np.asarray(radius_fraction, float), 0.0, 1.0)
    phi = np.asarray(azimuth_rad, float)
    radial = np.select(
        [model == PMT_HAMAMATSU, model == PMT_NNVT,
         model == PMT_HIGHQE_NNVT],
        [0.15, 0.27, 0.23], default=0.20,
    )
    az_amp = np.select(
        [model == PMT_HAMAMATSU, model == PMT_NNVT,
         model == PMT_HIGHQE_NNVT],
        [0.02, 0.06, 0.05], default=0.0,
    )
    harmonic = np.where(model == PMT_HAMAMATSU, 2.0, 4.0)
    return np.clip(
        1.0 - radial * rho**2 + az_amp * rho**2 * np.cos(harmonic * phi),
        0.55, 1.05,
    )
