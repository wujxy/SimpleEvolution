"""Standalone scoring for the single-electron task."""

from dataclasses import dataclass

import numpy as np


ELECTRON_PROBE_MEV = np.arange(1.0, 11.0)
TARGET_R_1MEV = 0.03


@dataclass(frozen=True)
class ResolutionFit:
    a: float
    b: float
    c: float
    r_1mev: float


def parse_prediction(value):
    """Validate one `(E_rec, x_rec, y_rec, z_rec)` submission response."""
    values = tuple(value)
    if len(values) != 4 or not np.isfinite(values).all():
        raise ValueError("prediction must contain four finite scalars")
    return tuple(float(item) for item in values)


def _peak(values):
    values = np.asarray(values, dtype=float)
    if values.size < 100 or not np.isfinite(values).all():
        raise ValueError("each probe requires at least 100 finite events")
    selected = values
    for _ in range(3):
        mean = float(selected.mean())
        width = float(selected.std(ddof=1))
        if width <= 0 or not np.isfinite(width):
            raise ValueError("peak fit requires non-zero finite width")
        selected = values[np.abs(values - mean) <= 2.5 * width]
        if len(selected) < 100:
            raise ValueError("peak fit requires at least 100 in-window events")
    return float(selected.mean()), float(selected.std(ddof=1))


def _response_reasons(control_true, control_rec):
    truth = np.asarray(control_true, dtype=float)
    reconstructed = np.asarray(control_rec, dtype=float)
    if truth.ndim != 1 or reconstructed.shape != truth.shape or not np.isfinite(reconstructed).all():
        return ["continuous-control output is missing or non-finite"]
    edges = np.linspace(float(truth.min()), float(truth.max()), 65)
    bins = np.digitize(truth, edges[1:-1])
    if (np.bincount(bins, minlength=64) < 100).any():
        return ["each continuous-control bin requires at least 100 events"]
    mean_truth = np.array([truth[bins == index].mean() for index in range(64)])
    mean_rec = np.array([reconstructed[bins == index].mean() for index in range(64)])
    delta = np.diff(mean_rec)
    reasons = []
    if np.count_nonzero(delta > 0) < 60:
        reasons.append("continuous response is not sufficiently increasing")
    slopes = delta / np.diff(mean_truth)
    if np.count_nonzero((slopes < 0.5) | (slopes > 1.5)) >= 5:
        reasons.append("continuous response has too many implausible local slopes")
    slope, intercept = np.polyfit(mean_truth, mean_rec, 1)
    if not 0.9 <= slope <= 1.1:
        reasons.append("continuous response has an inconsistent global energy scale")
    if abs(intercept) > 0.1:
        reasons.append("continuous response has an inconsistent energy offset")
    return reasons


def _energy_score(probe_energy, energy_rec, control_energy, control_rec):
    reasons = _response_reasons(control_energy, control_rec)
    if reasons:
        return None, reasons
    probe_energy = np.asarray(probe_energy, dtype=float)
    energy_rec = np.asarray(energy_rec, dtype=float)
    if probe_energy.shape != energy_rec.shape or not np.isfinite(energy_rec).all():
        return None, ["probe energy output is missing or non-finite"]
    if not np.array_equal(np.unique(probe_energy), ELECTRON_PROBE_MEV):
        return None, ["probe energy grid does not match the task"]
    try:
        means, widths = zip(*[_peak(energy_rec[probe_energy == value]) for value in ELECTRON_PROBE_MEV])
        energy = np.asarray(means)
        width = np.asarray(widths)
        design = np.column_stack((1.0 / energy, np.ones_like(energy), 1.0 / energy**2))
        variance, *_ = np.linalg.lstsq(design, (width / energy) ** 2, rcond=None)
        if (variance < 0).any():
            raise ValueError("resolution fit has a negative variance component")
    except ValueError as error:
        return None, [str(error)]
    fit = ResolutionFit(*np.sqrt(variance), float(np.sqrt(variance.sum())))
    return fit, []


def score_electron(probe_energy, energy_rec, vertex_true, vertex_rec, control_energy, control_rec, vertex_threshold_m):
    """Score energy and the 1-MeV three-dimensional vertex RMS together."""
    fit, reasons = _energy_score(probe_energy, energy_rec, control_energy, control_rec)
    if reasons:
        return {"valid": False, "passed": False, "invalid_reasons": reasons}
    probe_energy = np.asarray(probe_energy, dtype=float)
    vertex_true = np.asarray(vertex_true, dtype=float)
    vertex_rec = np.asarray(vertex_rec, dtype=float)
    if vertex_true.shape != vertex_rec.shape or vertex_true.shape != (len(probe_energy), 3) or not np.isfinite(vertex_rec).all():
        return {"valid": False, "passed": False, "invalid_reasons": ["vertex output must be finite [event,3]"]}
    residual = vertex_rec[probe_energy == 1.0] - vertex_true[probe_energy == 1.0]
    if len(residual) < 100:
        return {"valid": False, "passed": False, "invalid_reasons": ["1 MeV vertex score requires at least 100 events"]}
    vertex_rms = float(np.sqrt(np.mean(np.sum(residual * residual, axis=1))))
    energy_passed = fit.r_1mev <= TARGET_R_1MEV
    vertex_passed = vertex_rms <= float(vertex_threshold_m)
    return {
        "valid": True,
        "passed": energy_passed and vertex_passed,
        "energy_passed": energy_passed,
        "vertex_passed": vertex_passed,
        "R_1MeV": fit.r_1mev,
        "vertex_rms_m": vertex_rms,
        "vertex_threshold_m": float(vertex_threshold_m),
    }
