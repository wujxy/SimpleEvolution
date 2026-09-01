"""Public online contract for IBD-like positron visible-energy reconstruction."""


class Submission:
    def prepare(self, calibration_path, geometry_path):
        raise NotImplementedError

    def predict(self, event):
        """Return one finite reconstructed visible energy in MeV."""
        raise NotImplementedError
