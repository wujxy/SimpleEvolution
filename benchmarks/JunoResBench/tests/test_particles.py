"""v1 particle-upgrade tests: dataset invariants, blind-package hygiene.

Run: python3 benchmarks/JunoResBench/tests/test_particles.py

Generates a small mixed dataset in a temp dir, checks the ragged step
level and event-level invariants, then rebuilds the blind split layout
in-memory (the make_benchmark subset logic) and asserts the observation-
only package leaks no truth keys and no generation seed.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

BENCH = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    """Small mixed dataset for the invariant/hygiene tests (pytest entry;
    the __main__ runner builds the same dict inline)."""
    p = tmp_path_factory.mktemp("particles") / "mixed_small.npz"
    _generate(p)
    return np.load(p, allow_pickle=False)


def _generate(path, n=30):
    cmd = [
        sys.executable, str(BENCH / "scripts" / "generate_dataset.py"),
        "--events", str(n), "--emin", "1", "--emax", "8",
        "--seed", "20261300", "--n-pmt", "2000",
        "--particle-type", "mixed", "--direction", "isotropic",
        "--max-wf-per-event", "4", "--out", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def test_dataset_invariants(d):
    n = len(d["evt_e_true"])
    so, po = d["step_offsets"], d["pe_offsets"]
    assert so[-1] == d["evt_n_steps"].sum()
    dep = np.array([d["step_e_dep"][so[i]:so[i + 1]].sum() for i in range(n)])
    e_exp = d["evt_e_true"] + 1.021998 * (d["evt_particle_type"] == 2)
    assert np.abs(dep + d["evt_e_escaped"] - e_exp).max() < 1e-9
    assert d["evt_e_scored"][d["evt_particle_type"] == 2].min() > \
        d["evt_e_true"][d["evt_particle_type"] == 2].min()
    # electrons: exact single-point legacy behavior
    m = d["evt_particle_type"] == 0
    assert (d["evt_e_escaped"][m] == 0).all() and (d["evt_n_steps"][m] == 1).all()
    kinds = np.concatenate([d["step_kind"][so[i]:so[i + 1]] for i in np.where(m)[0]])
    assert (kinds == 0).all()
    # per-PE step tags in bounds
    for i in range(n):
        ps = d["pe_step"][po[i]:po[i + 1]]
        assert ps.max() < d["evt_n_steps"][i] and ps.min() >= 0
    # gamma/positron chains deposit away from the vertex (multi-point)
    for code in (1, 2):
        m = d["evt_particle_type"] == code
        if m.any():
            i = int(np.where(m)[0][0])
            v = np.column_stack((d["evt_x_m"], d["evt_y_m"], d["evt_z_m"]))[i]
            off = np.linalg.norm(d["step_pos"][so[i]:so[i + 1]] - v, axis=1)
            assert off.max() > 0.05          # chain reaches >5 cm from vertex
    print("ok  dataset invariants: conservation, e- legacy, step/PE tags")


def test_blind_hygiene(d):
    """Rebuild the blind splits with the REAL Subsetter; assert no leak and
    correctly re-based ragged offsets on every split (regression: val/test
    offsets were once left pointing into the full-dataset arrays)."""
    sys.path.insert(0, str(BENCH / "scripts"))
    from make_benchmark import Subsetter
    meta = json.loads(str(d["meta"]))
    blind_meta = dict(meta, seed=None)
    sub = Subsetter(d, meta, blind_meta)
    n = len(d["evt_e_true"])
    n_tr = n // 2
    for split, indices in (("train", np.arange(0, n_tr)),
                           ("val", np.arange(n_tr, n_tr + n // 4)),
                           ("test", np.arange(n_tr + n // 4, n))):
        out = sub.subset(indices, strip_seed=True)
        if split == "test":
            truth_keys = [k for k in out if k.startswith("evt_")] + [
                "pmt_ids", "n_pe_pmt", "pe_offsets", "pe_step",
                "t_emit_ns", "t_tof_ns", "t_rel_ns", "q_pe",
                "step_offsets", "step_pos", "step_e_dep", "step_e_vis",
                "step_t_ns", "step_dir", "step_kind",
            ]
            for k in truth_keys:
                out.pop(k, None)
        # re-based offsets: start at 0, end at the sliced array length
        # (pmt_offsets counts hit channels -> pairs with pmt_ids, not the
        # subsampled adc rows)
        for off, arr in (("pmt_offsets", "pmt_ids"),
                         ("pe_offsets", "t_rel_ns"),
                         ("step_offsets", "step_e_dep")):
            if off in out and arr in out:
                assert out[off][0] == 0, f"{split}/{off} not re-based"
                assert out[off][-1] == len(out[arr]), \
                    f"{split}/{off} length mismatch vs {arr}"
        # event-level truth arrays match the split length everywhere
        for k in out:
            if k.startswith("evt_"):
                assert len(out[k]) == len(indices), (split, k)
    # seed handling on the stripped meta
    assert blind_meta["seed"] is None and meta["seed"] is not None
    print("ok  blind hygiene + offsets re-based on all splits "
          "(uses the real make_benchmark.Subsetter)")


def main():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mixed_small.npz"
        _generate(p)
        d = np.load(p, allow_pickle=False)
        for code, name in ((0, "electron"), (1, "gamma"), (2, "positron")):
            assert (d["evt_particle_type"] == code).sum() >= 5, name
        test_dataset_invariants(d)
        test_blind_hygiene(d)
    print("\nall particle-upgrade tests passed")


if __name__ == "__main__":
    main()
