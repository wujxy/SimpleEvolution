"""JUNO-style positron energy-resolution scoring for JunoResBench v2."""

from dataclasses import dataclass

import numpy as np


PROBE_KINETIC_MEV = np.array([0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 11.0])
TARGET_R_1MEV = 0.03
RESPONSE_SLOPE_RANGE = (0.9, 1.1)
MAX_RESPONSE_INTERCEPT_MEV = 0.1


@dataclass(frozen=True)
class ResolutionFit:
    """Three JUNO resolution components, stored as fractions."""

    a: float
    b: float
    c: float
    r_1mev: float


def fit_peak(values):
    """Fit a peak by three deterministic 2.5-sigma clipping iterations."""
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size < 100:
        raise ValueError("peak fit requires at least 100 finite events")

    selected = finite
    for _ in range(3):
        mean = float(np.mean(selected))
        sigma = float(np.std(selected, ddof=1))
        if not np.isfinite(sigma) or sigma <= 0:
            raise ValueError("peak fit requires non-zero finite width")
        selected = finite[np.abs(finite - mean) <= 2.5 * sigma]
        if selected.size < 100:
            raise ValueError("peak fit requires at least 100 in-window events")
    return float(np.mean(selected)), float(np.std(selected, ddof=1))


def fit_resolution_curve(e_vis, sigma):
    """Fit sigma/E = sqrt(a^2/E + b^2 + c^2/E^2)."""
    energy = np.asarray(e_vis, dtype=float)
    width = np.asarray(sigma, dtype=float)
    if energy.ndim != 1 or width.shape != energy.shape or energy.size < 3:
        raise ValueError("resolution fit requires aligned one-dimensional arrays")
    if not (np.isfinite(energy).all() and np.isfinite(width).all()):
        raise ValueError("resolution fit inputs must be finite")
    if (energy <= 0).any() or (width <= 0).any():
        raise ValueError("resolution fit inputs must be positive")

    design = np.column_stack(
        (1.0 / energy, np.ones_like(energy), 1.0 / energy**2)
    )
    coefficients, *_ = np.linalg.lstsq(
        design, (width / energy) ** 2, rcond=None
    )
    if (coefficients < 0).any():
        raise ValueError("resolution fit has a negative variance component")
    a, b, c = np.sqrt(coefficients)
    return ResolutionFit(
        a=float(a),
        b=float(b),
        c=float(c),
        r_1mev=float(np.sqrt(coefficients.sum())),
    )


def validate_response(control_true, control_rec):
    """Return reasons that a continuous reconstructed response is invalid."""
    truth = np.asarray(control_true, dtype=float)
    reconstructed = np.asarray(control_rec, dtype=float)
    if truth.ndim != 1 or reconstructed.ndim != 1 or truth.shape != reconstructed.shape:
        return ["continuous-control output length mismatch"]
    if truth.size == 0 or not np.isfinite(truth).all():
        return ["continuous-control truth is missing or non-finite"]
    if not np.isfinite(reconstructed).all():
        return ["E_rec is missing or non-finite"]
    if float(np.ptp(truth)) <= 0:
        return ["continuous-control truth has no energy range"]

    edges = np.linspace(float(truth.min()), float(truth.max()), 65)
    bin_index = np.digitize(truth, edges[1:-1], right=False)
    counts = np.bincount(bin_index, minlength=64)
    if (counts < 100).any():
        return ["each of 64 continuous-control bins requires at least 100 events"]

    mean_truth = np.array([np.mean(truth[bin_index == i]) for i in range(64)])
    mean_rec = np.array([
        np.mean(reconstructed[bin_index == i]) for i in range(64)
    ])
    delta_rec = np.diff(mean_rec)
    reasons = []
    if np.count_nonzero(delta_rec > 0) < 60:
        reasons.append("continuous response is not sufficiently increasing")
    slopes = delta_rec / np.diff(mean_truth)
    if np.count_nonzero((slopes < 0.5) | (slopes > 1.5)) >= 5:
        reasons.append("continuous response has too many implausible local slopes")
    global_slope, global_intercept = np.polyfit(mean_truth, mean_rec, 1)
    if not RESPONSE_SLOPE_RANGE[0] <= global_slope <= RESPONSE_SLOPE_RANGE[1]:
        reasons.append("continuous response has an inconsistent global energy scale")
    if abs(global_intercept) > MAX_RESPONSE_INTERCEPT_MEV:
        reasons.append("continuous response has an inconsistent energy offset")
    return reasons


def score_v2(probe_kinetic, probe_rec, control_true, control_rec):
    """Compute the sole v2 score after the estimator-validity gate."""
    reasons = validate_response(control_true, control_rec)
    if reasons:
        return {
            "valid": False,
            "passed": False,
            "target": TARGET_R_1MEV,
            "invalid_reasons": reasons,
        }

    try:
        kinetic = np.asarray(probe_kinetic, dtype=float)
        reconstructed = np.asarray(probe_rec, dtype=float)
        if kinetic.ndim != 1 or reconstructed.shape != kinetic.shape:
            raise ValueError(
                "probe truth and E_rec must be aligned one-dimensional arrays"
            )
        if not (np.isfinite(kinetic).all() and np.isfinite(reconstructed).all()):
            raise ValueError("probe truth and E_rec must be finite")
        if not np.array_equal(np.unique(kinetic), PROBE_KINETIC_MEV):
            raise ValueError("probe kinetic energies do not match the v2 grid")

        points = []
        means = []
        widths = []
        for value in PROBE_KINETIC_MEV:
            mean, width = fit_peak(reconstructed[kinetic == value])
            means.append(mean)
            widths.append(width)
            points.append({
                "kinetic_mev": float(value),
                "E_vis": mean,
                "sigma": width,
                "resolution": width / mean,
            })
        fit = fit_resolution_curve(means, widths)
    except ValueError as error:
        return {
            "valid": False,
            "passed": False,
            "target": TARGET_R_1MEV,
            "invalid_reasons": [str(error)],
        }
    return {
        "valid": True,
        "passed": fit.r_1mev <= TARGET_R_1MEV,
        "target": TARGET_R_1MEV,
        "points": points,
        "a": fit.a,
        "b": fit.b,
        "c": fit.c,
        "R_1MeV": fit.r_1mev,
    }


__all__ = [
    "PROBE_KINETIC_MEV",
    "TARGET_R_1MEV",
    "RESPONSE_SLOPE_RANGE",
    "MAX_RESPONSE_INTERCEPT_MEV",
    "ResolutionFit",
    "fit_peak",
    "fit_resolution_curve",
    "score_v2",
    "validate_response",
]
