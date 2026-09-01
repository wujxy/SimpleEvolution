"""Minimal public baseline for JunoResBench v2: charge-only energy.

The reference any v2 agent must beat. Deliberately simple:

  charge   - per event, integrate the negative baseline-relative samples of
             every stored segment and merge segments per PMT;
  position - charge-weighted centroid of hit-PMT positions, used only for
             its radius;
  energy   - total charge divided by a radial light-collection and
             energy-scale correction fitted from the public calibration
             labels with one linear least-squares fit.

No learned model and no generator constant appears here.

Usage (through the online evaluator):
  python3 scripts/evaluate_v2.py --data <private-or-dev-root> \
      --submission baselines/v2_charge.py
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


class ChargeSubmission(Submission):
    """Sparse-charge energy with a fitted radial light-collection curve."""

    def __init__(self):
        self._positions = None
        self._radius_scale = 1.0
        self._scale = None

    def prepare(self, calibration_path, geometry_path):
        with np.load(geometry_path) as geometry:
            self._positions = np.asarray(geometry["pmt_positions_m"], dtype=float)
        self._radius_scale = float(
            np.mean(np.linalg.norm(self._positions, axis=1))
        )

        with np.load(Path(calibration_path) / "labels.npz") as labels:
            energy = np.asarray(labels["source_energy_mev"], dtype=float)
            radius = np.linalg.norm(
                np.asarray(labels["deployment_position_m"], dtype=float), axis=1
            )
        charge = np.array([
            event_summary(event, self._positions)[0]
            for event in SparseSplit(calibration_path).iter_events()
        ])
        shape = (radius / self._radius_scale) ** 2
        self._scale = np.linalg.lstsq(
            np.column_stack((energy, energy * shape)), charge, rcond=None
        )[0]

    def predict(self, event):
        total, radius = event_summary(event, self._positions)
        shape = (radius / self._radius_scale) ** 2
        denominator = float(self._scale[0] + self._scale[1] * shape)
        if denominator <= 0.0:
            return 0.0
        return total / denominator


Submission = ChargeSubmission
