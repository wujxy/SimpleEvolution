#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PKG=benchmarks/electron_single_site
KEY=$(sha256sum src/solve.py | cut -d' ' -f1)
PRED="/scratch/jrb-electron-$KEY.npz"

if [ ! -f "$PRED" ]; then
    bash scripts/check_verify.sh >/dev/null
fi
python3 - "$PRED" "$PKG/data/dev/truth.npz" \
    "$PKG/data/evaluation_config.json" "$PKG/evaluator" <<'PY'
import json
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, sys.argv[4])
from evaluate import score_predictions

with np.load(sys.argv[1], allow_pickle=False) as pred, \
     np.load(sys.argv[2], allow_pickle=False) as truth:
    prediction = np.column_stack(
        (pred["E_rec"], pred["x_rec"], pred["y_rec"], pred["z_rec"])
    )
    config = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    score = score_predictions(truth, prediction, config)
if not score.get("valid"):
    raise SystemExit("invalid prediction: " + "; ".join(score["invalid_reasons"]))
print(f"R_1MeV_percent={100.0 * score['R_1MeV']:.6f}")
print(f"vertex_rms_m={score['vertex_rms_m']:.6f}")
print("passed=" + str(score["passed"]).lower())
PY
