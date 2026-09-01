# JunoResBench v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one hidden-world, waveform-only IBD prompt positron benchmark whose sole success condition is JUNO-defined \(R_{1\,\mathrm{MeV}}\le3.0\%\).

**Architecture:** Upgrade the existing NumPy forward chain in place from event-wide response factors to table-driven charged-particle steps, then reuse the existing photon trace and electronics stages. Add a v2-only sparse waveform container, dataset builder, energy-only submission contract, and an isolated resolution module; retain frozen v1 artifacts as archival data but do not expose them in the v2 package.

**Tech Stack:** Python 3.9+, NumPy, standard library, pytest; no new runtime dependency and no Geant4 execution during evaluation.

## Global Constraints

- The public task has one required output, `E_rec` in MeV, and one success target, \(R_{1\,\mathrm{MeV}}\le3.0\%\).
- The score is the full JUNO curve at 1 MeV, not coefficient `a` and not a single 1 MeV sample.
- Final events are positrons with kinetic-energy deposition plus two 511 keV annihilation gammas; true energy and vertex remain hidden.
- Quenching is local and stopping-power dependent; do not add an event-level empirical resolution smear.
- The authoritative generator and intermediate truth are absent from the public v2 package.
- Frozen v1 data remain untouched. Do not regenerate or silently reinterpret existing `blind_task_electron`, `blind_task_mixed`, or `whitebox_task_electron` artifacts.
- Preserve unrelated worktree changes, including `scripts/probe_charter_interview.py` if still present.

---

## File structure

- `juno_res_bench/stopping_power.py`: table interpolation, Birks response, and charged-step construction primitives.
- `juno_res_bench/truth.py`: deposition-step schema extended with kinetic energy, stopping power, and path length.
- `juno_res_bench/stages/s1_particles.py`: electron/positron tracks and gamma-secondary transport.
- `juno_res_bench/stages/s1_response.py`: dispatch only; no ad hoc low-energy response curve.
- `juno_res_bench/stages/s2_photons.py`: Poisson scintillation and path-length Cherenkov production from step truth.
- `juno_res_bench/resolution.py`: deterministic per-peak fit, curve fit, validity checks, and scalar score.
- `juno_res_bench/sparse_waveforms.py`: compact event-streamable waveform ROI encoding.
- `scripts/generate_v2_dataset.py`: calibration, fixed-probe, and continuous-control generation.
- `scripts/evaluate_v2.py`: energy-only v2 evaluator.
- `scripts/make_v2_benchmark.py`: public/private package assembly without generator leakage.
- `baselines/v2_charge.py`: minimal public charge/time/geometry baseline.
- `task_v2/TASK.md`: generated public problem statement.
- `tests/test_stopping_power.py`, `tests/test_resolution_v2.py`, `tests/test_sparse_waveforms.py`, `tests/test_v2_package.py`: focused v2 tests.
- Existing stage tests are updated only where the authoritative forward model intentionally changes.

---

### Task 1: Local stopping power and Birks response

**Files:**
- Create: `benchmarks/JunoResBench/juno_res_bench/stopping_power.py`
- Modify: `benchmarks/JunoResBench/juno_res_bench/config.py`
- Create: `benchmarks/JunoResBench/tests/test_stopping_power.py`

**Interfaces:**
- Produces: `electron_stopping_power_mev_cm(kinetic_mev) -> ndarray`, `birks_visible_mev(deposited_mev, dedx_mev_cm, kb_cm_mev) -> ndarray`, and `charged_steps(kinetic_mev, step_fraction, cut_mev) -> tuple[ndarray, ndarray, ndarray]`.
- Consumes: NumPy only.

- [ ] **Step 1: Write failing interpolation and quenching tests**

```python
def test_low_energy_stopping_power_causes_stronger_quenching():
    e = np.array([0.02, 0.10, 1.0, 5.0])
    dedx = electron_stopping_power_mev_cm(e)
    assert dedx[0] > dedx[1] > dedx[2]
    assert dedx[-1] > 0
    visible_fraction = birks_visible_mev(e, dedx, 0.012) / e
    assert visible_fraction[0] < visible_fraction[1] < visible_fraction[2]


def test_charged_steps_conserve_energy_and_resolve_track():
    d_e, e_mid, ds_cm = charged_steps(1.0, step_fraction=0.05, cut_mev=0.002)
    assert len(d_e) > 10
    assert np.isclose(d_e.sum(), 1.0, atol=1e-12)
    assert (d_e > 0).all() and (ds_cm > 0).all()
    assert np.all(np.diff(e_mid) < 0)
```

- [ ] **Step 2: Run the tests and confirm the old model cannot satisfy them**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_stopping_power.py -q`

Expected: collection fails because `juno_res_bench.stopping_power` does not exist.

- [ ] **Step 3: Add the table-driven implementation**

Use a documented synthetic-LAB electron table, log-log interpolation, and deterministic energy subdivision:

```python
ENERGY_MEV = np.array([0.005, 0.01, 0.02, 0.05, 0.10, 0.20,
                       0.50, 1.0, 2.0, 5.0, 10.0, 20.0])
DEDX_MEV_CM = np.array([31.0, 16.5, 8.8, 4.2, 2.65, 1.90,
                        1.52, 1.48, 1.55, 1.75, 1.98, 2.28])


def electron_stopping_power_mev_cm(kinetic_mev):
    e = np.clip(np.asarray(kinetic_mev, float), ENERGY_MEV[0], ENERGY_MEV[-1])
    return np.exp(np.interp(np.log(e), np.log(ENERGY_MEV), np.log(DEDX_MEV_CM)))


def birks_visible_mev(deposited_mev, dedx_mev_cm, kb_cm_mev):
    d_e = np.asarray(deposited_mev, float)
    dedx = np.asarray(dedx_mev_cm, float)
    return d_e / (1.0 + float(kb_cm_mev) * dedx)


def charged_steps(kinetic_mev, step_fraction=0.05, cut_mev=0.002):
    remaining = float(kinetic_mev)
    deposits, midpoints = [], []
    while remaining > cut_mev:
        d_e = min(remaining - cut_mev, max(cut_mev, step_fraction * remaining))
        deposits.append(d_e)
        midpoints.append(remaining - 0.5 * d_e)
        remaining -= d_e
    if remaining > 0:
        deposits.append(remaining)
        midpoints.append(0.5 * remaining)
    d_e = np.asarray(deposits)
    e_mid = np.asarray(midpoints)
    ds_cm = d_e / electron_stopping_power_mev_cm(e_mid)
    return d_e, e_mid, ds_cm
```

In `DetectorConfig`, replace use of `birks_kB_ddx`, `nl_amp`, and `nl_scale_mev` in the authoritative path with:

```python
birks_kb_cm_per_mev: float = 0.012
charged_step_fraction: float = 0.05
charged_transport_cut_mev: float = 0.002
```

Keep deprecated fields only if a frozen-v1 loader still imports them; mark them as legacy and do not read them from v2 generation.

- [ ] **Step 4: Record provenance and limitations in the module docstring**

State that the table is a synthetic LAB-like continuous-slowing-down table shaped against public ESTAR organic-material curves, that it is not a real JUNO material database, and that positron/electron stopping-power differences are intentionally below v2's retained-effect threshold.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_stopping_power.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/JunoResBench/juno_res_bench/stopping_power.py benchmarks/JunoResBench/juno_res_bench/config.py benchmarks/JunoResBench/tests/test_stopping_power.py
git commit -m "feat(jrb): add local stopping-power response"
```

---

### Task 2: Multi-step charged-particle transport

**Files:**
- Modify: `benchmarks/JunoResBench/juno_res_bench/truth.py`
- Modify: `benchmarks/JunoResBench/juno_res_bench/stages/s1_particles.py`
- Modify: `benchmarks/JunoResBench/juno_res_bench/stages/s1_response.py`
- Modify: `benchmarks/JunoResBench/tests/test_stage1.py`

**Interfaces:**
- Consumes: Task 1 functions and `DetectorConfig` fields.
- Produces: every `DepositionSteps` instance has `kinetic_mev`, `dedx_mev_cm`, and `step_length_m` arrays aligned with `e_dep_mev`.

- [ ] **Step 1: Extend the deposition contract with a failing schema test**

```python
def test_positron_primary_is_a_local_track():
    event = EventInput(0, 0, 0, 1.0, particle_type=ParticleType.POSITRON)
    s1 = run_s1(event, DetectorConfig(), np.random.default_rng(7))
    primary = s1.steps.kind == DEPOSITION_KINDS["primary"]
    assert primary.sum() > 10
    assert np.isclose(s1.steps.e_dep_mev[primary].sum(), 1.0)
    assert (s1.steps.dedx_mev_cm[primary] > 0).all()
    assert (s1.steps.step_length_m[primary] > 0).all()
    assert np.ptp(s1.steps.e_vis_mev[primary] / s1.steps.e_dep_mev[primary]) > 0
```

- [ ] **Step 2: Run the new test and verify failure**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_stage1.py::test_positron_primary_is_a_local_track -q`

Expected: FAIL because the primary positron is one point and the new arrays do not exist.

- [ ] **Step 3: Extend `DepositionSteps` and `_Acc`**

Add aligned fields:

```python
kinetic_mev: np.ndarray
dedx_mev_cm: np.ndarray
step_length_m: np.ndarray
```

Change `_Acc.deposit` to accept these three values explicitly. Add `_Acc.deposit_charged_track(pos, direction, kinetic_mev, t_ns, kind, cfg, rng)`, which starts from:

```python
d_e, e_mid, ds_cm = charged_steps(
    kinetic_mev, cfg.charged_step_fraction, cfg.charged_transport_cut_mev
)
dedx = electron_stopping_power_mev_cm(e_mid)
e_vis = birks_visible_mev(d_e, dedx, cfg.birks_kb_cm_per_mev)
```

Advance position by each `ds_cm / 100`, advance time using electron beta at `e_mid`, and apply a small zero-mean angular diffusion drawn from the existing stage-1 RNG. Clamp a step at the LS boundary and add any energy carried out to `e_escape_mev` rather than depositing it outside the detector.

- [ ] **Step 4: Route every charged secondary through the track function**

- Positron kinetic energy: replace its single `acc.deposit(...)` call with `deposit_charged_track(...)`.
- Compton recoil: pass `e_rec` and momentum-transfer direction to `deposit_charged_track(...)`.
- Photoelectric and sub-cutoff absorption: create an electron track with the absorbed gamma energy.
- Remove `cfg.quench(...) * cfg.nl_correction(...)` from `_Acc.deposit` and delete the v2 `nl_corr` hook from `s1_response.py`.

- [ ] **Step 5: Update conservation and local-quenching assertions**

```python
ref = birks_visible_mev(
    s1.steps.e_dep_mev,
    s1.steps.dedx_mev_cm,
    cfg.birks_kb_cm_per_mev,
)
assert np.allclose(s1.steps.e_vis_mev, ref, rtol=0, atol=1e-12)
assert np.isclose(
    s1.steps.e_dep_mev.sum() + s1.e_escape_mev,
    expected_total,
    atol=1e-9,
)
```

Also assert that the mean visible fraction below 50 keV is smaller than the mean visible fraction from 0.5--2 MeV steps.

- [ ] **Step 6: Run stage-1 tests**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_stopping_power.py benchmarks/JunoResBench/tests/test_stage1.py -q`

Expected: all tests pass; legacy assertions that require a one-point electron are intentionally replaced.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/JunoResBench/juno_res_bench/truth.py benchmarks/JunoResBench/juno_res_bench/stages/s1_particles.py benchmarks/JunoResBench/juno_res_bench/stages/s1_response.py benchmarks/JunoResBench/tests/test_stage1.py
git commit -m "feat(jrb): transport charged deposits as local steps"
```

---

### Task 3: Step-correct scintillation and Cherenkov generation

**Files:**
- Modify: `benchmarks/JunoResBench/juno_res_bench/stages/s2_photons.py`
- Modify: `benchmarks/JunoResBench/tests/test_stage2.py`
- Modify: `benchmarks/JunoResBench/tests/test_stage0.py`

**Interfaces:**
- Consumes: extended `DepositionSteps` from Task 2.
- Produces: photons whose count, position, time, direction, and `step_idx` derive from the charged step that emitted them.

- [ ] **Step 1: Write failing photon-production tests**

```python
def test_scintillation_is_poisson_about_local_birks_sum():
    means = []
    for seed in range(400):
        p = run_s2_scint(s1_fixture, event_fixture, cfg, np.random.default_rng(seed))
        means.append(len(p))
    expected = s1_fixture.steps.e_vis_mev.sum() * cfg.ly_photons_mev
    assert abs(np.mean(means) - expected) < 5 * np.sqrt(expected / len(means))
    assert abs(np.var(means, ddof=1) / expected - 1.0) < 0.15


def test_cherenkov_uses_track_length_and_midstep_beta():
    photons = run_s2_cherenkov(s1_fixture, event_fixture, cfg, np.random.default_rng(3))
    assert len(photons) > 0
    assert set(np.unique(photons.step_idx)).issubset(set(range(s1_fixture.steps.n_steps)))
```

- [ ] **Step 2: Verify the variance test fails under Gaussian rounded counts**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_stage2.py -q`

Expected: at least the new scintillation-statistics test fails.

- [ ] **Step 3: Make scintillation counts Poisson and Cherenkov path based**

Replace rounded normal scintillation counts with:

```python
mu_gamma = np.clip(e_vis * cfg.ly_photons_mev, 0.0, None)
n_per_step = rng.poisson(mu_gamma).astype(np.int64)
```

Replace Cherenkov mean `e_dep * ly_cherenkov * (...)` with:

```python
lam = (
    step_length_m[idx]
    * cfg.cherenkov_photons_per_m
    * np.maximum(0.0, 1.0 - ct_k**2)
)
```

Use `steps.kinetic_mev` for beta and retain the existing cone-direction and prompt-time construction. Rename the configuration field to `cherenkov_photons_per_m`; do not retain two active yield knobs.

- [ ] **Step 4: Update the golden-contract scope**

Modify `test_stage0.py` so golden digests cover reproducibility of the new v2 chain, not bit identity with the obsolete point-deposit electron chain. Generate the fixed expected digests once from reviewed code and store the literal digests in the test.

- [ ] **Step 5: Run stages 0--3 tests**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_stage0.py benchmarks/JunoResBench/tests/test_stage1.py benchmarks/JunoResBench/tests/test_stage2.py benchmarks/JunoResBench/tests/test_stage3.py -q`

Expected: all tests pass and two identical seeds produce byte-identical truth arrays.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/JunoResBench/juno_res_bench/config.py benchmarks/JunoResBench/juno_res_bench/stages/s2_photons.py benchmarks/JunoResBench/tests/test_stage0.py benchmarks/JunoResBench/tests/test_stage2.py
git commit -m "feat(jrb): generate light from charged transport steps"
```

---

### Task 4: JUNO curve scorer and estimator-validity checks

**Files:**
- Create: `benchmarks/JunoResBench/juno_res_bench/resolution.py`
- Create: `benchmarks/JunoResBench/scripts/evaluate_v2.py`
- Create: `benchmarks/JunoResBench/tests/test_resolution_v2.py`

**Interfaces:**
- Produces: `fit_peak(values) -> (mean, sigma)`, `fit_resolution_curve(e_vis, sigma) -> ResolutionFit`, `validate_response(control_true, control_rec) -> list[str]`, and `score_v2(probe_kinetic, probe_rec, control_true, control_rec) -> dict`.
- Consumes: prediction key `E_rec`; private truth keys `evt_sample_role`, `evt_e_true`, and `evt_e_vis`.

- [ ] **Step 1: Write scorer tests using synthetic peaks**

```python
def test_curve_fit_recovers_full_one_mev_resolution():
    e = np.array([1.02, 1.5, 2, 3, 4, 5, 6, 8, 10, 12])
    a, b, c = 0.026, 0.006, 0.012
    r = np.sqrt(a*a/e + b*b + c*c/(e*e))
    fit = fit_resolution_curve(e, r * e)
    assert np.isclose(fit.a, a, rtol=2e-3)
    assert np.isclose(fit.b, b, rtol=2e-3)
    assert np.isclose(fit.c, c, rtol=2e-3)
    assert np.isclose(fit.r_1mev, np.sqrt(a*a + b*b + c*c), rtol=2e-3)


def test_invalid_outputs_are_rejected():
    truth = np.linspace(1.022, 12.022, 6400)
    assert validate_response(truth, np.full_like(truth, 3.0))
    assert validate_response(truth, np.round(truth * 2) / 2)
    assert validate_response(truth, truth) == []
```

- [ ] **Step 2: Run tests and verify missing-module failure**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_resolution_v2.py -q`

Expected: collection fails because `resolution.py` does not exist.

- [ ] **Step 3: Implement deterministic peak and curve fits**

`fit_peak` performs three iterations of mean/std estimation inside a fixed \(\pm2.5\sigma\) window, rejects groups with fewer than 100 finite events, and uses `ddof=1`. `fit_resolution_curve` solves:

```python
x = np.column_stack((1.0 / e_vis, np.ones_like(e_vis), 1.0 / e_vis**2))
coef, *_ = np.linalg.lstsq(x, (sigma / e_vis)**2, rcond=None)
if (coef < 0).any():
    raise ValueError("resolution fit has a negative variance component")
a, b, c = np.sqrt(coef)
r_1mev = float(np.sqrt(coef.sum()))
```

Return fractions internally and percentages only in JSON presentation.

- [ ] **Step 4: Implement continuous-response validity deterministically**

Split controls into 64 equal-width truth-energy bins, each with at least 100 events. Compute mean true visible energy and mean reconstructed energy in every bin. Reject the submission when any output is absent/non-finite, when fewer than 60 of 63 adjacent reconstructed means increase, or when five or more adjacent-bin slope ratios

\[
(\Delta\bar E_{rec})/(\Delta\bar E_{vis}^{truth})
\]

fall outside `[0.5, 1.5]`. This rejects constant and coarse-grid outputs while remaining a loose estimator-validity check rather than a competing accuracy target.

- [ ] **Step 5: Implement the energy-only CLI**

`evaluate_v2.py` loads one private truth split and a prediction NPZ containing exactly `E_rec`, partitions rows by `evt_sample_role` (`0=probe`, `1=continuous_control`), calls `score_v2`, prints per-point results, `a`, `b`, `c`, `R_1MeV`, and `passed = valid and R_1MeV <= 0.03`.

- [ ] **Step 6: Test malformed, constant, quantized, and valid predictions**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_resolution_v2.py -q`

Expected: all tests pass; only the smooth finite prediction reaches curve fitting.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/JunoResBench/juno_res_bench/resolution.py benchmarks/JunoResBench/scripts/evaluate_v2.py benchmarks/JunoResBench/tests/test_resolution_v2.py
git commit -m "feat(jrb): score JUNO positron resolution curve"
```

---

### Task 5: Sparse waveform event format

**Files:**
- Create: `benchmarks/JunoResBench/juno_res_bench/sparse_waveforms.py`
- Modify: `benchmarks/JunoResBench/juno_res_bench/split_io.py`
- Create: `benchmarks/JunoResBench/tests/test_sparse_waveforms.py`

**Interfaces:**
- Produces: `encode_event(adc, pmt_ids, baseline, threshold_adc, pre, post) -> SparseEvent`, `write_sparse_split(path, meta, observations, truth=None)`, and streaming `SparseSplit.iter_events()`.
- Consumes: stage-5 `uint16 [channel, sample]` waveforms and PMT ids.

- [ ] **Step 1: Write loss and size-bound tests**

```python
def test_sparse_roi_preserves_all_threshold_crossings():
    sparse = encode_event(adc, pmt_ids, baseline=16000,
                          threshold_adc=6, pre=16, post=48)
    dense = sparse.to_dense(fill=16000)
    active = (16000 - adc) >= 6
    assert np.array_equal(dense[active], adc[active])


def test_sparse_roi_reduces_quiet_waveforms():
    sparse = encode_event(adc_fixture, pmt_ids_fixture, 16000, 6, 16, 48)
    assert sparse.samples.nbytes < 0.25 * adc_fixture.nbytes
```

- [ ] **Step 2: Confirm tests fail before the codec exists**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_sparse_waveforms.py -q`

Expected: collection fails because `sparse_waveforms.py` does not exist.

- [ ] **Step 3: Implement merged ROI encoding**

For each channel, find samples satisfying `baseline - adc >= threshold_adc`, expand each connected region by `pre` and `post`, merge overlaps, and store:

```python
event_segment_offsets: int64[N_event + 1]
segment_sample_offsets: int64[N_segment + 1]
segment_pmt_ids: int32[N_segment]
segment_start_samples: int16[N_segment]
segment_samples: int16[N_kept_sample]  # adc - baseline
```

Store `baseline`, `n_samples`, threshold, pre, and post in public metadata. Dark-noise regions pass through the same threshold and receive no label.

- [ ] **Step 4: Add streaming directory IO**

Use `metadata.json`, compressed `index.npz`, and `segment_samples.npy` with memory mapping. `SparseSplit.iter_events()` yields one event object without loading all waveform samples. Truth, when present privately, lives in a separate `truth.npz` and is never required by the observation loader.

- [ ] **Step 5: Run round-trip and split tests**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_sparse_waveforms.py -q`

Expected: all active waveform regions round-trip and the fixture is at least 4x smaller than dense storage.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/JunoResBench/juno_res_bench/sparse_waveforms.py benchmarks/JunoResBench/juno_res_bench/split_io.py benchmarks/JunoResBench/tests/test_sparse_waveforms.py
git commit -m "feat(jrb): add streamable sparse waveform storage"
```

---

### Task 6: v2 calibration, probe, and control datasets

**Files:**
- Create: `benchmarks/JunoResBench/scripts/generate_v2_dataset.py`
- Create: `benchmarks/JunoResBench/scripts/make_v2_benchmark.py`
- Create: `benchmarks/JunoResBench/tests/test_v2_package.py`

**Interfaces:**
- Consumes: `DetectorSim`, sparse writer from Task 5, and score role codes from Task 4.
- Produces: public `task_v2/calibration`, public `task_v2/dev`, private `blind_truth_v2/final`, and a public metadata manifest.

- [ ] **Step 1: Write package hygiene and population tests**

```python
def test_v2_package_is_positron_only_and_truth_clean(v2_package):
    public = load_public(v2_package / "task_v2" / "final_observations")
    assert "evt_e_true" not in public.keys()
    assert "evt_e_vis" not in public.keys()
    assert "evt_x_m" not in public.keys()
    private = np.load(v2_package / "blind_truth_v2" / "truth.npz")
    assert set(private["evt_sample_role"]) == {0, 1}
    assert (private["evt_particle_type"] == 2).all()


def test_probe_grid_and_uniform_volume(v2_package):
    truth = np.load(v2_package / "blind_truth_v2" / "truth.npz")
    probe = truth["evt_sample_role"] == 0
    assert np.array_equal(np.unique(truth["evt_e_true"][probe]),
                          np.array([0, .5, 1, 2, 3, 4, 5, 6, 8, 11.]))
    r3 = np.linalg.norm(truth["evt_vertex_m"], axis=1) ** 3
    assert abs(np.mean(r3) / truth["fiducial_radius_m"][0]**3 - 0.5) < 0.05
```

- [ ] **Step 2: Run the package tests and verify missing-script failure**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_v2_package.py -q`

Expected: fixture setup fails because the v2 builder does not exist.

- [ ] **Step 3: Implement explicit generation modes**

`generate_v2_dataset.py` supports:

- `calibration`: known source energy and position; positions are center plus ±x/±y/±z at radii 8 m and 14 m; source energies are 0.511, 1.022, 2.223, 4.44, and 8.0 MeV equivalents.
- `probes`: equal counts at the ten approved positron kinetic energies.
- `controls`: positron kinetic energy uniform on `[0, 11]` MeV.

All modes use isotropic initial directions, uniform-in-volume vertices for physics events, `optics_mode="trace"`, full detector readout before sparse ROI encoding, and deterministic independent RNG streams. Randomize row order after roles are combined so role and energy are not inferable from ordering.

- [ ] **Step 4: Implement the public/private builder**

`make_v2_benchmark.py` invokes generation into a private temporary parent, then writes:

```text
task_v2/
  TASK.md
  detector_geometry.npz
  calibration/{metadata.json,index.npz,segment_samples.npy,labels.npz}
  dev/{metadata.json,index.npz,segment_samples.npy,truth.npz}
  evaluate.py
blind_truth_v2/
  final_observations/{metadata.json,index.npz,segment_samples.npy}
  truth.npz
```

The public calibration labels contain only documented source energy and deployment position. The final observation directory contains no truth arrays, seed, detector configuration, role, or energy grid. The generator package itself is not copied.

- [ ] **Step 5: Run hygiene tests on a 20-event-per-role fixture**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_v2_package.py -q`

Expected: all population, offset, streaming, and truth-leak tests pass.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/JunoResBench/scripts/generate_v2_dataset.py benchmarks/JunoResBench/scripts/make_v2_benchmark.py benchmarks/JunoResBench/tests/test_v2_package.py
git commit -m "feat(jrb): build hidden-world positron packages"
```

---

### Task 7: Public task, executable contract, and minimal baseline

**Files:**
- Create: `benchmarks/JunoResBench/task_v2/TASK.md`
- Create: `benchmarks/JunoResBench/task_v2/submission_api.py`
- Create: `benchmarks/JunoResBench/baselines/v2_charge.py`
- Modify: `benchmarks/JunoResBench/README.md`
- Modify: `benchmarks/JunoResBench/MANIFEST.md`
- Modify: `benchmarks/JunoResBench/tests/test_v2_package.py`

**Interfaces:**
- Produces: `Submission.prepare(calibration_path, geometry_path)` and `Submission.predict(event) -> float`.
- Consumes: `SparseSplit.iter_events()`.

- [ ] **Step 1: Write an online-contract test**

```python
def test_submission_runs_one_event_at_a_time(v2_package):
    submission = ChargeSubmission()
    submission.prepare(v2_package / "task_v2/calibration",
                       v2_package / "task_v2/detector_geometry.npz")
    for event in SparseSplit(v2_package / "task_v2/dev").iter_events():
        value = submission.predict(event)
        assert np.ndim(value) == 0 and np.isfinite(value)
```

- [ ] **Step 2: Implement the minimal charge baseline**

For each sparse segment, integrate negative baseline-relative samples, merge segments belonging to the same PMT, construct charge and first-hit-time summaries, estimate a charge centroid from PMT positions, and fit only the necessary radial light-collection and energy-scale corrections from calibration labels. Do not add a learned model or expose generator constants.

- [ ] **Step 3: Write the public task in opening-report form**

`TASK.md` states only:

- synthetic JUNO-like IBD prompt energy-reconstruction background;
- supplied calibration, geometry, and sparse-waveform schemas;
- hidden true energy and vertex;
- one finite `E_rec` per event, no event rejection;
- the Gaussian peak procedure, resolution formula, diagnostic outputs, and sole 3.0% target;
- online executable protocol, runtime/memory limits, and invalid-output behavior.

Do not describe Birks parameters, stopping-power tables, photon transport internals, oracle floors, or a preferred algorithm.

- [ ] **Step 4: Make the evaluator invoke the executable online**

Load `Submission`, call `prepare` once, then call `predict` once per streamed hidden event. Do not pass a dataset path or the full event collection to `predict`. Write predictions to a temporary array owned by the evaluator and call `score_v2` after the stream ends.

- [ ] **Step 5: Mark v1 artifacts archival in repository docs**

Update `README.md` and `MANIFEST.md` so v2 is the active single task and the electron/mixed/white-box packages are explicitly labeled frozen v1 evidence. Do not delete their large data files in this change.

- [ ] **Step 6: Run the contract test and a tiny end-to-end evaluation**

Run: `python -m pytest benchmarks/JunoResBench/tests/test_v2_package.py -q`

Run: `python benchmarks/JunoResBench/scripts/evaluate_v2.py --data benchmarks/JunoResBench/blind_truth_v2 --submission benchmarks/JunoResBench/baselines/v2_charge.py`

Expected: the submission produces one finite energy per event; the evaluator prints one scalar `R_1MeV` and one boolean `passed`, with no vertex/time score.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/JunoResBench/task_v2 benchmarks/JunoResBench/baselines/v2_charge.py benchmarks/JunoResBench/README.md benchmarks/JunoResBench/MANIFEST.md benchmarks/JunoResBench/tests/test_v2_package.py benchmarks/JunoResBench/scripts/evaluate_v2.py
git commit -m "feat(jrb): publish the single-goal v2 task"
```

---

### Task 8: World validation, statistics, and release gate

**Files:**
- Create: `benchmarks/JunoResBench/scripts/validate_v2_world.py`
- Create: `benchmarks/JunoResBench/tests/test_v2_validation.py`
- Modify: `benchmarks/JunoResBench/docs/effects.md`
- Modify: `benchmarks/JunoResBench/docs/differences.md`
- Modify: `benchmarks/JunoResBench/docs/stage_design.md`

**Interfaces:**
- Consumes: private v2 truth, a baseline or reference submission, and `score_v2`.
- Produces: a machine-readable validation JSON and a nonzero exit code when release conditions fail.

- [ ] **Step 1: Write release-gate tests**

```python
def test_release_gate_rejects_unstable_and_unreachable_fixture(tmp_path):
    report = validate_world(private_fixture, constant_submission,
                            bootstrap_seed=17, bootstrap_replicates=200)
    assert not report["release_ready"]
    assert "reference_does_not_reach_target" in report["failures"]


def test_bootstrap_boundary_is_stable(reference_fixture):
    report = validate_world(reference_fixture.data, reference_fixture.submission,
                            bootstrap_seed=17, bootstrap_replicates=200)
    assert report["score_bootstrap_std_pct_point"] <= 0.03
```

- [ ] **Step 2: Implement physical and statistical checks**

The validator records and gates:

- maximum event energy-conservation error below `1e-8 MeV`;
- lower mean visible fraction for charged steps below 50 keV than for steps in 0.5--2 MeV;
- nonzero annihilation-origin spatial extent and preserved 1.021998 MeV annihilation energy accounting;
- identical hashes for duplicate seeded generation;
- absence of private keys in the public package;
- 200 deterministic event-bootstrap refits with `R_1MeV` standard deviation no larger than `0.03` percentage point;
- at least one waveform-only reference submission with `R_1MeV <= 3.0%`;
- public charge baseline score greater than 3.0%.

The last two values are recorded internally but not copied into `TASK.md`.

- [ ] **Step 3: Profile and freeze event counts**

Start with 1,000 development events per probe energy and 10,000 final events per probe energy plus 6,400 controls. Reduce final counts only if the bootstrap standard-deviation gate still passes. Record generation seconds/event, compressed bytes/event, baseline evaluation time, peak-fit uncertainty, and the chosen counts in validation JSON. Require the public development baseline evaluation to finish within 120 seconds on the recorded reference machine.

- [ ] **Step 4: Update internal physics documentation**

Document retained mechanisms, excluded mechanisms, units, table provenance, and v1-to-v2 differences in internal repository docs. Keep these files out of the generated public `task_v2` directory.

- [ ] **Step 5: Run the full test and validation suite**

Run: `python -m pytest benchmarks/JunoResBench/tests -q`

Run: `python benchmarks/JunoResBench/scripts/validate_v2_world.py --private benchmarks/JunoResBench/blind_truth_v2 --baseline benchmarks/JunoResBench/baselines/v2_charge.py --reference reviewed_waveform_reference.py --out benchmarks/JunoResBench/blind_truth_v2/validation.json`

Expected: all tests pass; validation exits zero only when the target is statistically stable, a waveform-only reference reaches 3.0%, and the simple public baseline does not.

- [ ] **Step 6: Review the generated public package manually**

Inspect `task_v2/TASK.md`, metadata, array keys, and submission API from an agent's perspective. Confirm the task can be executed without private documentation and that no generator parameter or truth field leaks.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/JunoResBench/scripts/validate_v2_world.py benchmarks/JunoResBench/tests/test_v2_validation.py benchmarks/JunoResBench/docs/effects.md benchmarks/JunoResBench/docs/differences.md benchmarks/JunoResBench/docs/stage_design.md
git commit -m "test(jrb): gate v2 world and score stability"
```

---

## Final verification

- [ ] Run `python -m pytest benchmarks/JunoResBench/tests -q` and retain the complete passing output.
- [ ] Run a fresh tiny v2 package build in a temporary directory and verify public/private key separation.
- [ ] Run the public charge baseline and the reviewed waveform reference through the same online evaluator.
- [ ] Confirm the only public success decision is `R_1MeV <= 3.0%`.
- [ ] Confirm `git diff --check` is clean and unrelated worktree changes remain untouched.
