#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PKG=benchmarks/electron_single_site
KEY=$(sha256sum src/solve.py | cut -d' ' -f1)
PRED="/scratch/jrb-electron-$KEY.npz"

if [ ! -f "$PRED" ]; then
    python3 src/solve.py --data "$PKG/data/dev" \
        --calibration "$PKG/data/calibration" --out "$PRED" >&2
fi
python3 - "$PRED" "$PKG/data/dev/truth.npz" <<'PY'
import sys
import numpy as np

with np.load(sys.argv[1], allow_pickle=False) as pred, \
     np.load(sys.argv[2], allow_pickle=False) as truth:
    need = {"E_rec", "x_rec", "y_rec", "z_rec"}
    assert need <= set(pred.files), f"missing arrays: {sorted(need - set(pred.files))}"
    arrays = [np.asarray(pred[name], dtype=float) for name in sorted(need)]
    n = len(truth["evt_e_true"])
    assert all(array.shape == (n,) for array in arrays), "prediction length mismatch"
    assert all(np.isfinite(array).all() for array in arrays), "non-finite prediction"
    energy = np.asarray(pred["E_rec"], dtype=float)
    xyz = np.column_stack((pred["x_rec"], pred["y_rec"], pred["z_rec"]))
    assert np.all((energy > 0) & (energy < 100)), "energy outside (0,100) MeV"
    assert np.all(np.linalg.norm(xyz, axis=1) < 30), "vertex outside 30 m"
print("verify: PASS")
PY
