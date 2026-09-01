"""Reviewed waveform-only reference for JunoResBench v2.

The benchmark owner's in-house reference, used only by the release gate to
show the stated target is reachable from waveforms alone. It is not part
of the public package and sets no prescribed method.

Same per-event charge and charge-centroid radius as the public baseline,
plus corrections refined on the public development truth: one linear
least-squares fit of ``E_vis`` on ``[Q, Q*s, Q*s^2, Q^2]`` where ``Q`` is
the merged ROI charge and ``s`` the squared normalized centroid radius.
No learned model beyond that fit and no generator constant.
"""

from pathlib import Path

import numpy as np

try:
    from juno_res_bench.sparse_waveforms import SparseSplit
    from submission_api import Submission
except ImportError:  # repository-side direct use
    from benchmarks.JunoResBench.juno_res_bench.sparse_waveforms import SparseSplit
    from benchmarks.JunoResBench.task_v2.submission_api import Submission


def event_summary(event, positions):
    """Total ROI charge and charge-centroid radius for one streamed event."""
    if event.segment_pmt_ids.size == 0:
        return 0.0, 0.0
    starts = event.segment_sample_offsets[:-1]
    pulses = np.clip(-event.samples.astype(np.float64), 0.0, None)
    charges = np.add.reduceat(pulses, starts)
    total = float(charges.sum())
    if total <= 0.0:
        return 0.0, 0.0
    pmt_ids, inverse = np.unique(event.segment_pmt_ids, return_inverse=True)
    per_pmt = np.zeros(len(pmt_ids))
    np.add.at(per_pmt, inverse, charges)
    centroid = per_pmt @ positions[pmt_ids]
    return total, float(np.linalg.norm(centroid / total))


def _features(charge, shape):
    return np.column_stack((charge, charge * shape, charge * shape**2, charge**2))


class ReferenceSubmission(Submission):
    """Charge energy with corrections refined on the public dev truth."""

    def __init__(self):
        self._positions = None
        self._radius_scale = 1.0
        self._coefficients = None

    def prepare(self, calibration_path, geometry_path):
        with np.load(geometry_path) as geometry:
            self._positions = np.asarray(geometry["pmt_positions_m"], dtype=float)
        self._radius_scale = float(
            np.mean(np.linalg.norm(self._positions, axis=1))
        )

        dev = Path(calibration_path).parent / "dev"
        charge = []
        shape = []
        for event in SparseSplit(dev).iter_events():
            total, radius = event_summary(event, self._positions)
            charge.append(total)
            shape.append((radius / self._radius_scale) ** 2)
        charge = np.asarray(charge)
        shape = np.asarray(shape)
        with np.load(dev / "truth.npz") as truth:
            visible = np.asarray(truth["evt_e_vis"], dtype=float)

        keep = (charge > 0) & (visible > 0)
        self._coefficients = np.linalg.lstsq(
            _features(charge[keep], shape[keep]), visible[keep], rcond=None
        )[0]

    def predict(self, event):
        total, radius = event_summary(event, self._positions)
        if total <= 0:
            return 0.0
        shape = (radius / self._radius_scale) ** 2
        value = float(_features(np.array([total]), np.array([shape])) @ self._coefficients)
        return value if np.isfinite(value) else 0.0


Submission = ReferenceSubmission
