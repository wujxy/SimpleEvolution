# JunoResBench Two-Tier Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship isolated generator, waveform-data, and evaluator artifacts for single-electron energy/vertex and IBD-like positron multisite-energy tasks.

**Architecture:** The private `world_generator/` implements the shared hidden world. It writes only files specified by JSON contracts. Each task owns a standalone evaluator which has its own sparse reader and scoring implementation; it never imports generator code.

**Tech Stack:** Python 3.11, NumPy, pytest, NPZ/JSON/NPY, bubblewrap.

## Global Constraints

- Active paths are `world_generator/`, `contract/`, and `tasks/<name>/{dataset,evaluator}` under `benchmarks/JunoResBench/`.
- Generator never imports task evaluators; evaluators never import `world_generator` or `juno_res_bench`.
- `contract/` is JSON/Markdown only; dataset directories have no Python files.
- Preserve frozen v1 artifacts; do not delete them.
- Both tasks use one hidden detector world. Electron output is `(E_rec,x_rec,y_rec,z_rec)`; positron output is `E_rec`.
- Both energy targets are `R_1MeV <= 0.030`. Electron also requires the frozen 1-MeV vertex RMS threshold.
- Use test-first development and test after each task.

---

## File Map

```text
benchmarks/JunoResBench/
  contract/{sparse_waveform_v1.json,task_truth_v1.json}
  world_generator/{authoritative/,sparse_writer.py,populations.py,build_task.py,oracle_vertex.py,validate_release.py}
  tasks/
    electron_single_site/{TASK.md,dataset/,evaluator/{sparse_reader.py,scoring.py,submission_api.py,submission_worker.py,evaluate.py,task_config.json}}
    ibd_positron_multisite/{TASK.md,dataset/,evaluator/{sparse_reader.py,scoring.py,submission_api.py,submission_worker.py,evaluate.py,task_config.json}}
  tests/{test_contract_isolation.py,test_electron_task.py,test_positron_task.py,test_two_tier_black_box.py}
```

`dataset/public/` holds geometry, calibration, development observations and disclosed development truth. `dataset/private/` holds final observations and final truth. Published bundles contain only `TASK.md`, `dataset/`, and `evaluator/`.

## Task 1: Data-only contract and import firewall

**Files:** Create `contract/sparse_waveform_v1.json`, `contract/task_truth_v1.json`, and `tests/test_contract_isolation.py`.

**Produces:** JSON describing `metadata.json`, `index.npz`, `segment_samples.npy`; required index arrays are `event_segment_offsets:int64[N+1]`, `segment_sample_offsets:int64[S+1]`, `segment_pmt_ids:int32[S]`, `segment_start_samples:int16[S]`, and samples are `int16[M]`.

- [ ] Write a failing test:

```python
def test_contract_is_data_only_and_cross_imports_are_absent():
    contract = ROOT / "contract"
    assert {p.suffix for p in contract.iterdir()} == {".json"}
    spec = json.loads((contract / "sparse_waveform_v1.json").read_text())
    assert spec["files"]["index.npz"]["event_segment_offsets"] == "int64[N+1]"
    assert "world_generator" not in evaluator_source_text()
    assert "/evaluator" not in generator_source_text()
```

- [ ] Run `python -m pytest benchmarks/JunoResBench/tests/test_contract_isolation.py -q`; expect failure because contract and active paths do not exist.
- [ ] Add JSON contracts plus source-discovery helpers. `task_truth_v1.json` allows electron final `evt_sample_role,evt_e_true,evt_e_vis,evt_vertex_m` and positron final `evt_sample_role,evt_e_true,evt_e_vis` only for evaluator scoring.
- [ ] Run the same test; expect PASS.
- [ ] Commit `feat: define JunoResBench data-only contracts`.

## Task 2: Isolated shared world generator

**Files:** Create `world_generator/authoritative/`, `world_generator/sparse_writer.py`, `world_generator/populations.py`, `world_generator/build_task.py`; modify `tests/test_two_tier_black_box.py`.

**Consumes:** JSON sparse contract manually. **Produces:** `build(task_name, output_root, seed, n_pmt, counts) -> None` and no Python files under its output root.

- [ ] Write failing tests:

```python
def test_generator_writes_data_only(tmp_path):
    subprocess.run(generator_cmd("electron_single_site", tmp_path), check=True)
    assert (tmp_path / "public/calibration/index.npz").is_file()
    assert (tmp_path / "private/truth.npz").is_file()
    assert not list(tmp_path.rglob("*.py"))

def test_tasks_share_geometry_and_differ_in_particle_topology(tmp_path):
    build_small("electron_single_site", tmp_path / "e")
    build_small("ibd_positron_multisite", tmp_path / "p")
    assert np.array_equal(load_geometry(tmp_path / "e"), load_geometry(tmp_path / "p"))
    assert set(load_truth(tmp_path / "e")["evt_particle_type"]) == {0}
    assert set(load_truth(tmp_path / "p")["evt_particle_type"]) == {2}
```

- [ ] Run `python -m pytest benchmarks/JunoResBench/tests/test_two_tier_black_box.py -q`; expect failure because `build_task.py` is absent.
- [ ] Move/copy the existing authoritative detector stack under `world_generator/authoritative/` and rewrite only its imports to be relative. Copy only the waveform encoder/streaming writer into `world_generator/sparse_writer.py`; do not include any reader. Implement one hidden `DetectorConfig(optics_mode="trace", full_readout=True, three_gamma_frac=0.0)` and one deterministic layout per seed.
- [ ] Implement populations: electron probes/controls in 1--10 MeV with volume-uniform vertices; positron probes `[0,.5,1,2,3,4,5,6,8,11]` and 64-stratum 0--11 MeV controls. Both use public labeled calibration sources.
- [ ] Re-run test; expect PASS. Commit `feat: add isolated shared JunoResBench generator`.

## Task 3: Standalone electron evaluator

**Files:** Create all files in `tasks/electron_single_site/evaluator/`; create `tests/test_electron_task.py`.

**Produces:** `Submission.prepare(calibration_path, geometry_path)`, `Submission.predict(event)->tuple[float,float,float,float]`, `score_electron(...) -> dict`.

- [ ] Write failing tests:

```python
def test_electron_score_requires_energy_and_vertex_targets():
    score = score_electron(probe_grid(), good_energy(), np.zeros((4000,3)),
                           np.full((4000,3), .20), controls(), controls(), .10)
    assert score["energy_passed"] and not score["vertex_passed"]
    assert not score["passed"]

def test_electron_prediction_requires_four_finite_scalars():
    assert parse_prediction((1., 0., 0., 0.)) == (1., 0., 0., 0.)
    with pytest.raises(ValueError, match="four finite"):
        parse_prediction((1., np.nan, 0.))
```

- [ ] Run `python -m pytest benchmarks/JunoResBench/tests/test_electron_task.py -q`; expect failure due to missing modules.
- [ ] Implement an independent `SparseEvent`/`SparseSplit` reader from the JSON contract. Implement deterministic peak/curve energy fit, 64-bin energy validity check, and `vertex_rms_m=sqrt(mean(sum((r_rec-r_true)**2,axis=1)))` evaluated only on 1-MeV probes. `passed` is energy AND vertex success.
- [ ] Implement a bubblewrap worker returning four IEEE doubles per event, `RLIMIT_AS=8GiB`, `RLIMIT_CPU=3600`, `RLIMIT_FSIZE=16MiB`, one-hour parent deadline, and finite-output validation.
- [ ] Re-run test; expect PASS. Commit `feat: add standalone electron evaluator`.

## Task 4: Private electron oracle threshold

**Files:** Create `world_generator/oracle_vertex.py`; modify `world_generator/build_task.py`; extend `tests/test_electron_task.py`.

**Produces:** `freeze_threshold(oracle_rms_m) -> float`, rounded up to 0.1 cm after multiplying the oracle RMS by 1.15; private `electron_oracle.json`; evaluator-local `task_config.json` containing only the numeric target.

- [ ] Write failing test:

```python
def test_vertex_threshold_is_one_point_fifteen_times_oracle_rounded_up():
    assert freeze_threshold(.08101) == .094
    assert freeze_threshold(.08000) == .092
```

- [ ] Run the one test; expect failure because `freeze_threshold` is absent.
- [ ] Implement the private charge/time likelihood oracle against the hidden PMT model for 1-MeV electron probes. Write its RMS and threshold privately; copy only `{"vertex_threshold_m": value, "energy_target_r_1mev": .03}` to the standalone evaluator configuration.
- [ ] Run `python -m pytest benchmarks/JunoResBench/tests/test_electron_task.py -q`; expect PASS. Commit `feat: derive electron vertex acceptance from oracle`.

## Task 5: Standalone positron multisite evaluator

**Files:** Create all files in `tasks/ibd_positron_multisite/evaluator/`; create `tests/test_positron_task.py`.

**Produces:** `Submission.predict(event)->float`, `score_positron(probe_kinetic, probe_rec, control_visible, control_rec)->dict`, and its own sandboxed `run_online`.

- [ ] Write failing tests:

```python
def test_positron_rejects_affine_energy_scale_loophole():
    truth = np.linspace(1.022, 12.022, 6400)
    score = score_positron(probes(), good_predictions(), truth, 1.25 * truth)
    assert not score["valid"]
    assert "global energy scale" in score["invalid_reasons"]

def test_positron_worker_rejects_nan(tmp_path):
    with pytest.raises(RuntimeError, match="non-finite"):
        run_online(write_submission(tmp_path, "return float('nan')"), data(), calibration(), geometry())
```

- [ ] Run `python -m pytest benchmarks/JunoResBench/tests/test_positron_task.py -q`; expect failure due to missing modules.
- [ ] Independently implement reader, scorer, worker, and evaluator. Require 10 fixed probes, 3-pass 2.5-sigma peak fitting, `R_1MeV`, 64-bin continuity, at least 60 increasing adjacent means, fewer than five local slopes outside `[.5,1.5]`, global slope `[.9,1.1]`, and intercept `+-.1 MeV`. Do not import electron evaluator modules at runtime.
- [ ] Re-run test; expect PASS. Commit `feat: add standalone positron evaluator`.

## Task 6: Publish bundles and private release gates

**Files:** Create task `TASK.md` files, `world_generator/validate_release.py`, generator-side baseline/reference submissions; modify `README.md`, `MANIFEST.md`, and black-box tests.

**Produces:** `validate(task_name,dataset_root,baseline,reference,evaluator_path)->dict`; published bundle contains exactly `TASK.md`, `dataset/`, `evaluator/`.

- [ ] Write failing tests:

```python
def test_published_bundle_exposes_only_task_data_and_evaluator(tmp_path):
    bundle = publish_small_bundle("electron_single_site", tmp_path)
    assert {p.name for p in bundle.iterdir()} == {"TASK.md", "dataset", "evaluator"}
    assert not list(bundle.rglob("world_generator"))

def test_release_needs_reference_pass_and_baseline_fail(tmp_path):
    report = validate_release_small("ibd_positron_multisite", tmp_path)
    assert report["reference_passes"] and not report["baseline_passes"]
```

- [ ] Run black-box tests; expect failure due to absent publisher/validator.
- [ ] Implement validator as a subprocess caller of evaluator `evaluate.py`, never an import. Require energy conservation, low-energy local quenching, two 511-keV gamma topology, recursive allowlists, waveform/truth reproducibility hashes, reference succeeds, baseline fails, bootstrap stability, and development baseline runtime under 120 seconds. Keep reviewed reference private; include public baseline only as an example.
- [ ] Re-run black-box tests; expect PASS. Commit `feat: publish isolated two-tier tasks`.

## Task 7: Make two-tier layout active and verify

**Files:** Modify `README.md`, `MANIFEST.md`, `docs/stage_design.md`, `docs/effects.md`; extend `test_contract_isolation.py`.

- [ ] Write failing test:

```python
def test_active_readme_names_two_tier_tasks_not_coupled_v2_scripts():
    text = (ROOT / "README.md").read_text()
    assert "tasks/electron_single_site" in text
    assert "tasks/ibd_positron_multisite" in text
    assert "scripts/evaluate_v2.py" not in active_section(text)
```

- [ ] Run it; expect failure because v2 scripts are currently active.
- [ ] Mark old coupled v2 entry points archival without deleting them; document the shared private world and standalone task contracts. Generate tiny data for each task, execute each standalone evaluator against its own private stream, and validate both releases.
- [ ] Run `python -m pytest benchmarks/JunoResBench/tests -q`; expect PASS. Commit `docs: make isolated two-tier tasks active`.

## Plan Self-Review

- Tasks 1--2 implement data-only and shared-world boundaries; Tasks 3--4 implement electron energy/vertex plus oracle threshold; Task 5 implements positron scoring; Task 6 makes privacy/release validation external; Task 7 makes the layout active while retaining v1.
- Interfaces use one sparse contract and never a shared Python utility.
- No task depends on unimplemented runtime imports from another task; all cross-boundary validation is subprocess/file based.
