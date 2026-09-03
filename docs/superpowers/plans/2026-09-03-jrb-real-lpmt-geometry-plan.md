# JunoResBench Real LPMT Geometry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the production Fibonacci sphere with the aligned JUNO J26.4.1 CD-LPMT position/type map while retaining an explicit small uniform layout for unit tests.

**Architecture:** `geometry.py` owns parsing and validation of the two official CSV files and exposes one immutable `PMTLayout` carrying copy numbers, positions, and PMT model codes. `build_task.py` selects `juno` geometry by default for production and writes the public identifiers beside positions. No official geometry file is copied into git; the generator records source paths, SHA-256 hashes, row counts, and model counts so a release is reproducible and auditable.

**Tech Stack:** Python 3, NumPy, pytest, JUNO J26.4.1 CVMFS geometry CSVs, NPZ release artifacts.

## Global Constraints

- The participant-facing success target remains only `R_1MeV <= 0.030`; this phase does not alter scoring.
- The generator, generated dataset, and evaluator remain executable-code-independent.
- Production defaults to real JUNO geometry; `uniform` is an explicit test/preflight option only.
- Do not commit CVMFS source files or generated waveform data.
- Do not generate a full release on the development machine.
- Preserve CopyNo order and fail closed on missing, duplicate, misaligned, or unknown PMT records.
- Use TDD and commit each independently testable task.

---

## File map

- Modify `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/geometry.py`: geometry schema, paired CSV parser, validation, provenance hashes.
- Modify `benchmarks/JunoResBench/world_generator/build_task.py`: CLI selection, production default, public geometry metadata.
- Modify `benchmarks/JunoResBench/tests/test_stage0.py`: unit coverage for the layout schema and paired parser.
- Modify `benchmarks/JunoResBench/tests/test_two_tier_black_box.py`: explicit uniform fixture generation and public-artifact assertions.
- Modify `benchmarks/JunoResBench/README.md`: production/preflight commands and CVMFS inputs.
- Modify `benchmarks/JunoResBench/docs/generator_physics_and_resolution_budget.md`: record the geometry upgrade and its physical purpose.

### Task 1: Add typed, aligned PMT geometry

**Files:**
- Modify: `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/geometry.py`
- Test: `benchmarks/JunoResBench/tests/test_stage0.py`

**Interfaces:**
- Consumes: position rows `CopyNo X Y Z Orientation_theta Orientation_phi`; type rows `CopyNo PMTType`.
- Produces: `PMTLayout(positions_m, copy_no, pmt_model)`, `PMTLayout.from_juno_csv(position_path, type_path)`, and `PMTLayout.provenance`.
- PMT model codes: `PMT_HAMAMATSU = 0`, `PMT_NNVT = 1`, `PMT_HIGHQE_NNVT = 2`, `PMT_GENERIC = -1`.

- [ ] **Step 1: Write failing paired-parser tests**

Add tests using `tmp_path` CSV fixtures whose type rows are deliberately out
of order. Assert that output is sorted by CopyNo, positions are converted from
mm to m, and types are aligned by CopyNo rather than row position:

```python
def test_juno_layout_aligns_position_and_type_by_copy_number(tmp_path):
    pos = tmp_path / "pos.csv"
    typ = tmp_path / "type.csv"
    pos.write_text(
        "# header\n2 0 0 -19365 180 0\n0 0 0 19365 0 0\n1 19365 0 0 90 0\n"
    )
    typ.write_text(
        "# header\n1 NNVT\n2 HighQENNVT\n0 Hamamatsu\n"
    )
    layout = PMTLayout.from_juno_csv(pos, typ)
    assert np.array_equal(layout.copy_no, [0, 1, 2])
    assert np.allclose(layout.positions_m[:, 2], [19.365, 0.0, -19.365])
    assert np.array_equal(
        layout.pmt_model,
        [PMT_HAMAMATSU, PMT_NNVT, PMT_HIGHQE_NNVT],
    )
```

Add separate failure assertions for duplicate CopyNo, missing type rows and an
unknown type string. Each must raise `ValueError` containing respectively
`duplicate CopyNo`, `position/type CopyNo mismatch`, or `unknown PMT type`.

- [ ] **Step 2: Run tests and confirm the interface is absent**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_stage0.py -k juno_layout -v
```

Expected: FAIL because the constants and two-file `from_juno_csv` interface do
not exist.

- [ ] **Step 3: Implement the minimum schema and parser**

In `geometry.py`, add the four integer constants and a private model map:

```python
PMT_GENERIC = -1
PMT_HAMAMATSU = 0
PMT_NNVT = 1
PMT_HIGHQE_NNVT = 2
_PMT_MODEL_CODE = {
    "Hamamatsu": PMT_HAMAMATSU,
    "NNVT": PMT_NNVT,
    "HighQENNVT": PMT_HIGHQE_NNVT,
}
```

Parse each non-comment row into a dictionary keyed by integer CopyNo. Reject
duplicates while parsing. Compare the two key sets before constructing arrays.
Sort CopyNo numerically and create aligned `float64` positions and `int8` model
codes.

Extend `PMTLayout` with:

```python
copy_no: np.ndarray
pmt_model: np.ndarray
source: str = "synthetic"
source_sha256: tuple = ()
```

Implement `__post_init__` to require shapes `(N, 3)`, `(N,)`, `(N,)`, finite
positions, unique CopyNo and known codes. `PMTLayout.uniform` creates
`copy_no=np.arange(n_pmt)` and fills `pmt_model` with `PMT_GENERIC`.

`from_juno_csv` computes SHA-256 for each input file and stores
`source="JUNO J26.4.1 CD-LPMT"` and the two digests. It accepts `str | Path`
arguments and has no fallback to a synthetic layout.

- [ ] **Step 4: Run geometry tests**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_stage0.py -k 'juno_layout or direction_grid' -v
```

Expected: all selected tests PASS; existing uniform-layout direction-grid test
continues to pass.

- [ ] **Step 5: Commit typed geometry**

```bash
git add benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/geometry.py benchmarks/JunoResBench/tests/test_stage0.py
git commit -m "feat(jrb): load aligned JUNO LPMT geometry and types"
```

### Task 2: Make real geometry the production default

**Files:**
- Modify: `benchmarks/JunoResBench/world_generator/build_task.py`
- Test: `benchmarks/JunoResBench/tests/test_two_tier_black_box.py`

**Interfaces:**
- Consumes: `PMTLayout.from_juno_csv(position_path, type_path)` from Task 1.
- Produces: `select_layout(mode, n_pmt, position_csv, type_csv) -> PMTLayout` and CLI flags `--geometry-mode`, `--juno-position-csv`, `--juno-type-csv`.

- [ ] **Step 1: Write failing layout-selection tests**

Import `select_layout` into `test_two_tier_black_box.py`. Add a small paired
fixture and assert:

```python
layout = select_layout("juno", None, pos, typ)
assert layout.n_pmt == 3
assert set(layout.pmt_model) == {0, 1, 2}

uniform = select_layout("uniform", 16, None, None)
assert uniform.n_pmt == 16
assert np.all(uniform.pmt_model == PMT_GENERIC)
```

Assert that `select_layout("juno", ...)` propagates `FileNotFoundError` and
never silently constructs a uniform sphere.

- [ ] **Step 2: Run the selection tests and confirm failure**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_two_tier_black_box.py -k select_layout -v
```

Expected: FAIL because `select_layout` is undefined.

- [ ] **Step 3: Implement selection and CLI wiring**

Add constants for the J26.4.1 position and type CSV paths. Implement:

```python
def select_layout(mode, n_pmt, position_csv, type_csv):
    if mode == "uniform":
        if n_pmt is None:
            raise ValueError("uniform geometry requires n_pmt")
        return PMTLayout.uniform(n_pmt, DetectorConfig().detector_radius_m)
    if mode == "juno":
        return PMTLayout.from_juno_csv(position_csv, type_csv)
    raise ValueError(f"unknown geometry mode: {mode}")
```

Change `build` to receive a `PMTLayout` rather than constructing one
internally. Set CLI `--geometry-mode` default to `juno`; retain `--n-pmt` only
for explicit `uniform` mode. The two CVMFS flags default to the exact J26.4.1
paths inspected in the design session.

Update black-box `_build` to pass `--geometry-mode uniform --n-pmt 16`, so
unit fixtures remain fast and do not depend on CVMFS.

- [ ] **Step 4: Run selection and black-box collection tests**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_two_tier_black_box.py -v
```

Expected: selection tests PASS; generation tests are SKIPPED unless
`JRB_RUN_GENERATION=1`.

- [ ] **Step 5: Commit production selection**

```bash
git add benchmarks/JunoResBench/world_generator/build_task.py benchmarks/JunoResBench/tests/test_two_tier_black_box.py
git commit -m "feat(jrb): default production to real LPMT geometry"
```

### Task 3: Publish geometry identity without private response constants

**Files:**
- Modify: `benchmarks/JunoResBench/world_generator/build_task.py`
- Modify: `benchmarks/JunoResBench/tests/test_two_tier_black_box.py`

**Interfaces:**
- Consumes: `PMTLayout.copy_no`, `pmt_model`, `source`, and `source_sha256`.
- Produces: public `detector_geometry.npz` arrays `pmt_positions_m`, `pmt_copy_no`, `pmt_model`; metadata keys `layout`, `geometry_source`, `geometry_sha256`, `pmt_model_counts`.

- [ ] **Step 1: Write failing public-artifact assertions**

Extend `_geometry` to return all three arrays and add assertions after a tiny
uniform build:

```python
with np.load(root / "public" / "detector_geometry.npz") as data:
    assert set(data.files) == {
        "pmt_positions_m", "pmt_copy_no", "pmt_model"
    }
    assert np.array_equal(data["pmt_copy_no"], np.arange(16))
    assert np.all(data["pmt_model"] == PMT_GENERIC)
```

Read split metadata and assert its geometry fields agree with the NPZ. Also
assert that no `gain`, `pde_delta`, `time_offset` or `tts_sigma` array is
present in the public geometry file.

- [ ] **Step 2: Run the generation test and confirm failure**

Run:

```bash
JRB_RUN_GENERATION=1 python -m pytest benchmarks/JunoResBench/tests/test_two_tier_black_box.py -k generator_writes_only_data -v
```

Expected: FAIL because only `pmt_positions_m` is currently serialized.

- [ ] **Step 3: Serialize identity and provenance**

Write the three public arrays with `np.savez_compressed`. Update `_metadata`
to derive model counts with `np.unique` and serialize hashes as a JSON-safe
list. Keep `DetectorCalibration` and all sampled response arrays private and
absent from public files.

For a uniform fixture, metadata must say `layout="uniform"` and have an empty
hash list. For a JUNO layout, it must say `layout="juno_j26_4_1_cd_lpmt"` and
contain both source hashes.

- [ ] **Step 4: Run the process-level tests**

Run:

```bash
JRB_RUN_GENERATION=1 python -m pytest benchmarks/JunoResBench/tests/test_two_tier_black_box.py -v
```

Expected: all tests PASS and neither generated task tree contains Python code.

- [ ] **Step 5: Commit public geometry metadata**

```bash
git add benchmarks/JunoResBench/world_generator/build_task.py benchmarks/JunoResBench/tests/test_two_tier_black_box.py
git commit -m "feat(jrb): publish LPMT geometry identity and provenance"
```

### Task 4: Document and verify the phase boundary

**Files:**
- Modify: `benchmarks/JunoResBench/README.md`
- Modify: `benchmarks/JunoResBench/docs/generator_physics_and_resolution_budget.md`
- Test: `benchmarks/JunoResBench/tests/test_stage0.py`

**Interfaces:**
- Consumes: the CLI and metadata contract from Tasks 1–3.
- Produces: an operator-visible production command and one CVMFS integration check.

- [ ] **Step 1: Add an opt-in real-file integration test**

Add a test marked with `pytest.mark.skipif` unless
`JRB_RUN_JUNO_GEOMETRY=1`. Load the exact J26.4.1 pair and assert:

```python
assert layout.n_pmt == 17612
assert np.unique(layout.copy_no).size == 17612
assert (layout.pmt_model == PMT_HAMAMATSU).sum() == 4955
assert (layout.pmt_model == PMT_NNVT).sum() == 2738
assert (layout.pmt_model == PMT_HIGHQE_NNVT).sum() == 9919
assert 19.0 < layout.radius_m < 19.5
```

- [ ] **Step 2: Run the real geometry integration test**

Run:

```bash
JRB_RUN_JUNO_GEOMETRY=1 python -m pytest benchmarks/JunoResBench/tests/test_stage0.py -k installed_juno_geometry -v
```

Expected: PASS with J26.4.1 mounted; otherwise the ordinary test suite leaves
the test skipped.

- [ ] **Step 3: Update operator and physics documentation**

Document the production default and the explicit lightweight preflight:

```bash
python benchmarks/JunoResBench/world_generator/build_task.py \
  --task ibd_positron_multisite \
  --out /path/to/candidate \
  --seed 20260903

python benchmarks/JunoResBench/world_generator/build_task.py \
  --geometry-mode uniform --n-pmt 128 \
  --task ibd_positron_multisite \
  --out /tmp/jrb-preflight \
  --seed 20260903 \
  --calibration-events-per-point 1 \
  --probe-events-per-point 1 --controls 64
```

Explain that real coordinates and type identities break the exact synthetic
rotational symmetry but do not alone establish benchmark difficulty. Record
the expected 4,955 Hamamatsu, 2,738 NNVT and 9,919 HighQENNVT counts and state
that response differences are implemented in the next phase, not silently
assumed here.

- [ ] **Step 4: Run the focused regression suite**

Run:

```bash
python -m pytest \
  benchmarks/JunoResBench/tests/test_stage0.py \
  benchmarks/JunoResBench/tests/test_two_tier_black_box.py -v
```

Expected: ordinary tests PASS; cluster-only generation tests and opt-in real
geometry integration test SKIP unless enabled.

- [ ] **Step 5: Inspect the diff and commit documentation**

Run:

```bash
git diff --check
git status --short
```

Confirm that no CVMFS CSV, NPZ waveform release, evaluator environment or
unrelated working-tree file is staged. Then commit:

```bash
git add benchmarks/JunoResBench/README.md benchmarks/JunoResBench/docs/generator_physics_and_resolution_budget.md benchmarks/JunoResBench/tests/test_stage0.py
git commit -m "docs(jrb): define real geometry production provenance"
```

## Completion criteria

- Default CLI production fails closed unless both official J26.4.1 geometry
  inputs are readable and aligned.
- A generated public task exposes positions, stable CopyNo values and PMT
  model codes but no private sampled detector response constants.
- Uniform geometry remains available only by explicit selection for fast
  tests and bounded preflight.
- The real source pair is hash-recorded and the observed 17,612-row PMT model
  composition is tested.
- Existing generator/data/evaluator isolation remains intact.
- No full waveform release is generated or copied during this phase.
