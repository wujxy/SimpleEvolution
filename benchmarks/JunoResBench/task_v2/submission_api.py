"""Public submission contract for JunoResBench v2.

A submission is a reconstruction program that the evaluator runs online:
``prepare`` is called exactly once with the public calibration and geometry
paths, then ``predict`` is called exactly once per streamed hidden event.
``predict`` must return one finite reconstructed visible energy in MeV for
every event; event rejection is not part of the task, and the submission
never receives the dataset path or the event collection as a whole.
"""


class Submission:
    """Energy-only online reconstruction entry point."""

    def prepare(self, calibration_path, geometry_path):
        """Calibrate from the public calibration split and geometry.

        Called exactly once, before any ``predict`` call. ``calibration_path``
        is a sparse split directory containing ``labels.npz`` with the
        documented source energies and deployment positions; ``geometry_path``
        is the ``detector_geometry.npz`` file.
        """
        raise NotImplementedError

    def predict(self, event):
        """Return one finite reconstructed visible energy in MeV.

        ``event`` is one :class:`juno_res_bench.sparse_waveforms.SparseEvent`
        holding the merged waveform regions of a single event. The return
        value must be a finite scalar. The same instance persists, so causal
        state from already-scored events is allowed; future or batch access is
        not.
        """
        raise NotImplementedError
