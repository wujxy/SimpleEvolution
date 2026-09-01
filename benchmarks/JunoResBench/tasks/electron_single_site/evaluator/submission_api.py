"""Public online contract for the single-electron task."""


class Submission:
    def prepare(self, calibration_path, geometry_path):
        raise NotImplementedError

    def predict(self, event):
        """Return `(E_rec, x_rec, y_rec, z_rec)` as finite floats."""
        raise NotImplementedError
