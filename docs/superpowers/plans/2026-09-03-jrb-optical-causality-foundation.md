# JunoResBench Optical Causality Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make photon wavelength and final propagation direction authoritative from emission through PMT detection, add LS dispersion, and remove the hand radial response from trace-mode detection.

**Architecture:** Extend the in-memory photon and arrival structures instead of changing the public dataset contract. Stage 2 owns source spectra, trace-mode Stage 3 owns propagation direction and wavelength evolution, and Stage 4 consumes the final arrival state without reconstructing it from the source vertex. Fast mode remains a compatibility path while production trace mode gains the corrected physics.

**Tech Stack:** Python 3, NumPy, dataclasses, pytest.

## Global Constraints

- Do not use JUNOSW runtime performance or calibration databases; public geometry and public measurement-shaped synthetic tables only.
- Do not change `.env`, secrets, production configuration, or external release data.
- Do not generate the full dataset on this machine.
- Preserve generator/dataset/evaluator source isolation.
- Preserve seeded reproducibility and the separate RNG streams for scintillation and Cherenkov light.
- The center PE yield may use one global normalization, but trace mode must not apply per-event, per-radius, or per-direction normalization.
- Do not touch unrelated modified solver/spec/chat files in the working tree.

---

## File Structure

- `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/truth.py`: in-memory photon and PMT-arrival state contracts.
- `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/optics_tables.py`: synthetic public-measurement-shaped spectra and LS dispersion functions.
- `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/stages/s2_photons.py`: assign source wavelength and wavelength-dependent Cherenkov cone direction.
- `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/stages/s3_trace.py`: propagate the Stage-2 wavelength, use group velocity, and export final PMT direction.
- `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/stages/s4_detection.py`: consume final arrival direction and skip the hand radial factor in trace mode.
- `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/detector.py`: pass only compatibility inputs required by fast mode.
- `benchmarks/JunoResBench/tests/test_stage2.py`: source spectrum and Cherenkov dispersion tests.
- `benchmarks/JunoResBench/tests/test_trace.py`: wavelength continuity and group-delay tests.
- `benchmarks/JunoResBench/tests/test_stage4.py`: final-direction authority and trace radial-factor tests.
- `benchmarks/JunoResBench/docs/generator_physics_and_resolution_budget.md`: document the corrected causal ownership and approximation.

### Task 1: Extend Photon and Arrival Contracts

**Files:**
- Modify: `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/truth.py`
- Test: `benchmarks/JunoResBench/tests/test_stage2.py`
- Test: `benchmarks/JunoResBench/tests/test_trace.py`

**Interfaces:**
- Produces: `PhotonSoA.wavelength_nm: np.ndarray` with shape `(N,)`.
- Produces: `S3Output.dir_at_pmt: np.ndarray | None` with shape `(N_arrived, 3)`.
- Preserves: `PhotonSoA.empty()` and `PhotonSoA.concatenate(parts)`.

- [ ] **Step 1: Write failing schema tests**

Add to `test_stage2.py`:

```python
def test_photon_wavelength_contract_survives_concatenation():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import PhotonSoA

    a = PhotonSoA(
        photon_type=np.array([0], np.int8),
        pos_m=np.zeros((1, 3), np.float32),
        dir=np.array([[0, 0, 1]], np.float32),
        t_emit_ns=np.zeros(1, np.float32),
        step_idx=np.zeros(1, np.int32),
        wavelength_nm=np.array([430.0]),
    )
    b = PhotonSoA(
        photon_type=np.array([1], np.int8),
        pos_m=np.zeros((1, 3), np.float32),
        dir=np.array([[0, 1, 0]], np.float32),
        t_emit_ns=np.zeros(1, np.float32),
        step_idx=np.zeros(1, np.int32),
        wavelength_nm=np.array([350.0]),
    )
    out = PhotonSoA.concatenate([a, b])
    assert np.array_equal(out.wavelength_nm, [430.0, 350.0])
    assert PhotonSoA.empty().wavelength_nm.shape == (0,)
```

Add to `test_trace.py`:

```python
def test_arrival_direction_contract_accepts_aligned_array():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import S3Output

    out = S3Output(
        n_arrived_pmt=np.array([1]),
        pmt_idx=np.array([0], np.int32),
        t_arrive_ns=np.array([10.0], np.float32),
        dir_at_pmt=np.array([[0.0, 0.0, 1.0]], np.float32),
    )
    assert out.dir_at_pmt.shape == (1, 3)
```

- [ ] **Step 2: Run tests and verify contract failures**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_stage2.py::test_photon_wavelength_contract_survives_concatenation benchmarks/JunoResBench/tests/test_trace.py::test_arrival_direction_contract_is_unit_length -q
```

Expected: FAIL because `PhotonSoA` has no `wavelength_nm` and `S3Output` has no `dir_at_pmt`.

- [ ] **Step 3: Add the minimal schema fields**

Implement in `truth.py`:

```python
@dataclass
class PhotonSoA:
    photon_type: np.ndarray
    pos_m: np.ndarray
    dir: np.ndarray
    t_emit_ns: np.ndarray
    step_idx: np.ndarray = None
    wavelength_nm: np.ndarray = None

    def __post_init__(self):
        n = len(self.photon_type)
        if self.step_idx is None:
            self.step_idx = np.zeros(n, np.int32)
        if self.wavelength_nm is None:
            self.wavelength_nm = np.full(n, np.nan, np.float64)
        if len(self.wavelength_nm) != n:
            raise ValueError("wavelength_nm must align with photon_type")
```

Update `empty()` with `wavelength_nm=np.zeros(0, np.float64)` and
`concatenate()` with `wavelength_nm=np.concatenate([p.wavelength_nm for p in parts])`.

Append to `S3Output`:

```python
dir_at_pmt: np.ndarray = None  # (N_arrived, 3), final propagation direction
```

- [ ] **Step 4: Run the schema tests**

Run the command from Step 2.

Expected: both schema tests PASS.

- [ ] **Step 5: Commit the schema contract**

```bash
git add benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/truth.py benchmarks/JunoResBench/tests/test_stage2.py benchmarks/JunoResBench/tests/test_trace.py
git commit -m "feat(jrb): carry photon wavelength and arrival direction"
```

### Task 2: Generate Distinct Scintillation and Cherenkov Spectra

**Files:**
- Modify: `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/optics_tables.py`
- Modify: `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/stages/s2_photons.py`
- Test: `benchmarks/JunoResBench/tests/test_stage2.py`

**Interfaces:**
- Produces: `ls_refractive_index(wavelength_nm: np.ndarray) -> np.ndarray`.
- Produces: `ls_group_index(wavelength_nm: np.ndarray) -> np.ndarray`.
- Produces: `sample_scintillation_lambda(rng, n: int) -> np.ndarray`.
- Produces: `sample_cherenkov_lambda(rng, beta: np.ndarray) -> np.ndarray`.
- Preserves: `sample_emission_lambda` as an alias for old diagnostics.

- [ ] **Step 1: Write failing spectrum and dispersion tests**

Add to `test_stage2.py`:

```python
def test_scintillation_and_cherenkov_have_distinct_spectra():
    cfg = DetectorConfig(ly_photons_mev=2000.0)
    ev = EventInput(0, 0, 0, 3.0)
    s1 = run_s1(ev, cfg)
    scint = run_s2_scint(s1, ev, cfg, np.random.default_rng(41))
    cher = run_s2_cherenkov(s1, ev, cfg, np.random.default_rng(42))
    assert np.isfinite(scint.wavelength_nm).all()
    assert np.isfinite(cher.wavelength_nm).all()
    assert np.mean(cher.wavelength_nm) < np.mean(scint.wavelength_nm) - 20.0


def test_cherenkov_cone_uses_each_photon_wavelength():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.optics_tables import ls_refractive_index

    photons, _, s1 = _cherenkov(3.0)
    step_dir = s1.steps.dir[photons.step_idx]
    beta = np.array([beta_from_kinetic(e) for e in s1.steps.kinetic_mev])
    expected = 1.0 / (
        beta[photons.step_idx] * ls_refractive_index(photons.wavelength_nm)
    )
    observed = np.einsum("ij,ij->i", photons.dir.astype(float), step_dir)
    assert np.allclose(observed, expected, atol=2e-3)


def test_ls_group_index_is_dispersive():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.optics_tables import ls_group_index

    ng = ls_group_index(np.array([350.0, 430.0, 500.0]))
    assert np.all(np.diff(ng) < 0.0)
    assert np.all((ng > 1.45) & (ng < 1.75))
```

- [ ] **Step 2: Run tests and verify failures**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_stage2.py -q
```

Expected: FAIL because the spectrum samplers and dispersion functions are absent and Stage 2 does not populate wavelengths.

- [ ] **Step 3: Add compact public-shaped optical functions**

Add to `optics_tables.py`:

```python
CHER_LAMBDA_MIN_NM = 300.0
CHER_LAMBDA_MAX_NM = 600.0


def ls_refractive_index(lam_nm):
    """Synthetic LAB-like Cauchy dispersion, anchored to n(430 nm)=1.49."""
    lam_um = np.asarray(lam_nm, float) * 1e-3
    b = 0.0080
    a = 1.49 - b / 0.43**2
    return a + b / lam_um**2


def ls_group_index(lam_nm):
    """Cauchy group index n_g=n-lambda*dn/dlambda."""
    lam_um = np.asarray(lam_nm, float) * 1e-3
    b = 0.0080
    a = 1.49 - b / 0.43**2
    return a + 3.0 * b / lam_um**2


def sample_scintillation_lambda(rng, n):
    return sample_emission_lambda(rng, n)


def sample_cherenkov_lambda(rng, beta):
    beta = np.asarray(beta, float)
    out = np.empty(len(beta), float)
    unresolved = np.ones(len(beta), bool)
    n_blue = ls_refractive_index(np.array([CHER_LAMBDA_MIN_NM]))[0]
    for _ in range(64):
        if not unresolved.any():
            break
        idx = np.where(unresolved)[0]
        lam = rng.uniform(CHER_LAMBDA_MIN_NM, CHER_LAMBDA_MAX_NM, len(idx))
        n_lam = ls_refractive_index(lam)
        weight = np.maximum(0.0, 1.0 - 1.0 / (beta[idx]**2 * n_lam**2)) / lam**2
        envelope = np.maximum(
            0.0, 1.0 - 1.0 / (beta[idx]**2 * n_blue**2)
        ) / CHER_LAMBDA_MIN_NM**2
        accept = rng.random(len(idx)) * envelope <= weight
        out[idx[accept]] = lam[accept]
        unresolved[idx[accept]] = False
    if unresolved.any():
        raise RuntimeError("Cherenkov wavelength rejection sampler did not converge")
    return out
```

- [ ] **Step 4: Populate wavelengths and wavelength-dependent cones**

In `run_s2_scint`, sample and pass:

```python
wavelength_nm = sample_scintillation_lambda(rng, n_gamma)
```

In `run_s2_cherenkov`, expand the step beta to each produced photon, sample its wavelength, and replace the scalar-step cone cosine with:

```python
beta_ph = np.repeat(beta[idx], n_per)
wavelength_nm = sample_cherenkov_lambda(rng, beta_ph)
ct_ph = 1.0 / (beta_ph * ls_refractive_index(wavelength_nm))
st_ph = np.sqrt(np.maximum(0.0, 1.0 - ct_ph**2))
```

Slice `ct_ph` and `st_ph` in the existing per-step loop and pass `wavelength_nm` to `PhotonSoA`. Keep the existing per-step count normalization in this batch so the change isolates spectrum and cone geometry.

- [ ] **Step 5: Run Stage-2 tests**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_stage2.py -q
```

Expected: all Stage-2 tests PASS after updating the old cone expectation to use `ls_refractive_index(photons.wavelength_nm)`.

- [ ] **Step 6: Commit source spectra and dispersion**

```bash
git add benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/optics_tables.py benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/stages/s2_photons.py benchmarks/JunoResBench/tests/test_stage2.py
git commit -m "feat(jrb): separate photon source spectra"
```

### Task 3: Preserve Wavelength and Final Direction Through Trace Transport

**Files:**
- Modify: `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/stages/s3_trace.py`
- Test: `benchmarks/JunoResBench/tests/test_trace.py`

**Interfaces:**
- Consumes: `PhotonSoA.wavelength_nm`.
- Consumes: `ls_group_index(wavelength_nm)`.
- Produces: `S3Output.lam_nm` from the propagated photon state.
- Produces: `S3Output.dir_at_pmt` from the final ray direction.

- [ ] **Step 1: Write failing trace continuity tests**

Add to `test_trace.py`:

```python
def test_trace_starts_from_stage2_wavelength():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages import s3_trace
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import EventInput, PhotonSoA

    cfg = DetectorConfig(optics_mode="trace")
    photons = PhotonSoA(
        photon_type=np.zeros(2000, np.int8),
        pos_m=np.zeros((2000, 3), np.float32),
        dir=np.tile([0, 0, 1], (2000, 1)).astype(np.float32),
        t_emit_ns=np.zeros(2000, np.float32),
        step_idx=np.zeros(2000, np.int32),
        wavelength_nm=np.full(2000, 500.0),
    )
    out = s3_trace.trace_photons(
        photons, EventInput(0, 0, 0, 1.0), cfg,
        PMTLayout.uniform(400), np.random.default_rng(51)
    )
    assert len(out.lam_nm) > 0
    assert np.all(out.lam_nm == 500.0)


def test_trace_populates_unit_final_directions():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages import s1_response, s2_photons, s3_trace
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import EventInput

    cfg = DetectorConfig(optics_mode="trace", ly_photons_mev=300.0)
    event = EventInput(0, 0, 0, 1.0)
    s1 = s1_response.run_s1(event, cfg)
    photons = s2_photons.run_s2_scint(s1, event, cfg, np.random.default_rng(31))
    arrived = s3_trace.trace_photons(
        photons, event, cfg, PMTLayout.uniform(400), np.random.default_rng(32)
    )
    assert arrived.dir_at_pmt.shape == (len(arrived.pmt_idx), 3)
    assert np.allclose(np.linalg.norm(arrived.dir_at_pmt, axis=1), 1.0)


def test_blue_photons_have_larger_group_delay():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.optics_tables import ls_group_index

    distance_m = 17.7
    blue = distance_m * ls_group_index(np.array([350.0]))[0] / 0.299792458
    red = distance_m * ls_group_index(np.array([500.0]))[0] / 0.299792458
    assert blue > red + 5.0
```

- [ ] **Step 2: Run trace tests and verify failures**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_trace.py -q
```

Expected: FAIL because trace mode resamples the emission spectrum, uses constant velocity, and omits final direction.

- [ ] **Step 3: Make Stage-2 wavelength authoritative**

Replace trace initialization with:

```python
lam = photons.wavelength_nm.astype(np.float64).copy()
if not np.isfinite(lam).all():
    raise ValueError("trace mode requires finite Stage-2 photon wavelengths")
```

Remove `sample_emission_lambda` from `s3_trace.py` imports.

- [ ] **Step 4: Use group velocity per propagation segment**

Replace the constant `v_over_c` update with:

```python
t[act_idx] = t_act + dmin * ls_group_index(lm) / C_M_NS
```

Re-emitted photons continue with their newly sampled fluor wavelength on the next loop iteration.

- [ ] **Step 5: Export final PMT direction**

Add `"dir_at_pmt": []` to the arrival accumulator, append `db[hit]`, and return:

```python
dir_at_pmt=np.concatenate(arrived["dir_at_pmt"]).astype(np.float32)
```

The empty return uses `np.zeros((0, 3), np.float32)`.

- [ ] **Step 6: Run trace tests**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_trace.py -q
```

Expected: all trace tests PASS; adjust straight-flight numeric expectations to the new group-index calculation without loosening the tail assertions.

- [ ] **Step 7: Commit authoritative trace state**

```bash
git add benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/stages/s3_trace.py benchmarks/JunoResBench/tests/test_trace.py
git commit -m "fix(jrb): preserve causal photon trace state"
```

### Task 4: Consume Final Direction and Remove Trace Radial Reweighting

**Files:**
- Modify: `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/stages/s4_detection.py`
- Modify: `benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/detector.py`
- Test: `benchmarks/JunoResBench/tests/test_stage4.py`

**Interfaces:**
- Consumes: `S3Output.dir_at_pmt` when present.
- Produces: `_arrival_cosine(s3, event, layout, pos_ph, arrived_type, photon_dir) -> np.ndarray`.
- Produces: `_base_detection_probability(s3, event, cfg, photon_pos) -> np.ndarray | float`.
- Preserves: fast-mode chord geometry and its legacy radial factor.

- [ ] **Step 1: Write failing Stage-4 causality tests**

Add to `test_stage4.py`:

```python
def test_trace_arrival_direction_overrides_emission_chord():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages.s4_detection import _arrival_cosine
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import EventInput, S3Output

    layout = PMTLayout.uniform(1, radius_m=19.365)
    final_dir = -layout.inward_normals[[0]]
    s3 = S3Output(
        n_arrived_pmt=np.array([1]), pmt_idx=np.array([0], np.int32),
        t_arrive_ns=np.array([0], np.float32), photon_idx=np.array([0]),
        det_scale=np.ones(1), dir_at_pmt=final_dir,
    )
    cos_inc = _arrival_cosine(
        s3, EventInput(0, 0, 0, 1.0), layout,
        pos_ph=np.array([[10.0, 0.0, 0.0]]),
        arrived_type=np.array([0], np.int8), photon_dir=None,
    )
    assert np.allclose(cos_inc, 1.0)


def test_trace_base_detection_has_no_hand_radial_factor():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages.s4_detection import _base_detection_probability
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import EventInput, S3Output

    cfg = DetectorConfig(optics_mode="trace")
    s3 = S3Output(
        n_arrived_pmt=np.array([1]), pmt_idx=np.array([0], np.int32),
        t_arrive_ns=np.array([0], np.float32), photon_idx=np.array([0]),
        det_scale=np.ones(1), lam_nm=np.array([430.0]),
        dir_at_pmt=np.array([[0.0, 0.0, 1.0]]),
    )
    center = _base_detection_probability(s3, EventInput(0, 0, 0, 1.0), cfg, None)
    edge = _base_detection_probability(s3, EventInput(15, 0, 0, 1.0), cfg, None)
    assert np.array_equal(np.asarray(center), np.asarray(edge))
```

- [ ] **Step 2: Run tests and verify helper failures**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_stage4.py -q
```

Expected: FAIL because `_arrival_cosine` and `_base_detection_probability` do not exist.

- [ ] **Step 3: Extract base detection probability**

Implement:

```python
def _base_detection_probability(s3, event, cfg, photon_pos):
    if cfg.optics_mode == "trace":
        p_det = cfg.p_det_center
    elif photon_pos is not None:
        pos_ph = np.asarray(photon_pos, np.float64)[s3.photon_idx]
        p_det = cfg.p_det_center * cfg.mu_pe_ratio(
            np.maximum(np.linalg.norm(pos_ph, axis=1), 1e-6)
        )
    else:
        p_det = cfg.p_det_center * cfg.mu_pe_ratio(
            max(float(np.linalg.norm(event.vertex_m)), 1e-6)
        )
    scale = s3.det_scale if s3.det_scale is not None else np.ones(len(s3.pmt_idx))
    return p_det * scale
```

Use this helper at the start of `run_s4`. This removes only the hand radial factor from trace mode; wavelength QE and the single global `trace_det_norm` remain.

- [ ] **Step 4: Extract authoritative incidence geometry**

Implement:

```python
def _arrival_cosine(s3, event, layout, pos_ph, arrived_type, photon_dir):
    n_in = layout.inward_normals[s3.pmt_idx]
    if s3.dir_at_pmt is not None:
        direction = np.asarray(s3.dir_at_pmt, np.float64)
        direction /= np.linalg.norm(direction, axis=1, keepdims=True)
        return -np.einsum("ij,ij->i", direction, n_in)

    if pos_ph is not None:
        direction = layout.positions_m[s3.pmt_idx] - pos_ph
    else:
        direction = layout.positions_m[s3.pmt_idx] - event.vertex_m[None, :]
    direction /= np.linalg.norm(direction, axis=1, keepdims=True)
    cos_inc = -np.einsum("ij,ij->i", direction, n_in)
    if photon_dir is not None:
        emitted = np.asarray(photon_dir, np.float64)[s3.photon_idx]
        emitted /= np.linalg.norm(emitted, axis=1, keepdims=True)
        cher_cos = -np.einsum("ij,ij->i", emitted, n_in)
        cos_inc = np.where(arrived_type == 1, cher_cos, cos_inc)
    return cos_inc
```

Use it in `run_s4`. It makes trace arrivals authoritative for both photon types while retaining fast-mode behavior.

- [ ] **Step 5: Run Stage-4 and integration tests**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_stage4.py benchmarks/JunoResBench/tests/test_trace.py benchmarks/JunoResBench/tests/test_stage2.py -q
```

Expected: all tests PASS. Update the old off-center trace expectation only if it asserted the removed hand polynomial rather than emergent optics.

- [ ] **Step 6: Commit Stage-4 causal detection**

```bash
git add benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/stages/s4_detection.py benchmarks/JunoResBench/world_generator/authoritative/juno_res_bench/detector.py benchmarks/JunoResBench/tests/test_stage4.py
git commit -m "fix(jrb): detect from final photon arrival state"
```

### Task 5: Document and Verify the First Batch

**Files:**
- Modify: `benchmarks/JunoResBench/docs/generator_physics_and_resolution_budget.md`
- Test: all JunoResBench generator tests.

**Interfaces:**
- Documents: wavelength ownership, source-specific spectra, group velocity, final-direction authority, and trace-mode radial-factor removal.
- Does not change: public task contract or external release artifacts.

- [ ] **Step 1: Update the physics documentation**

Add a section containing these exact claims:

```markdown
### Strengthened optical causality

- Stage 2 assigns source-specific photon wavelengths: scintillation follows the synthetic fluor spectrum, while Cherenkov follows a Frank--Tamm-shaped spectrum.
- Stage 3 transports that wavelength without resampling it, except when physical absorption/re-emission creates a new fluor wavelength.
- Every propagation segment uses wavelength-dependent LS group velocity.
- Trace-mode PMT detection uses the final propagation direction after scattering/reflection, not the emission direction or source-to-PMT chord.
- The legacy hand radial light-yield polynomial is not applied after trace transport; trace-mode nonuniformity must emerge from the optical path.
```

- [ ] **Step 2: Run the focused first-batch suite**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_stage2.py benchmarks/JunoResBench/tests/test_stage3.py benchmarks/JunoResBench/tests/test_trace.py benchmarks/JunoResBench/tests/test_stage4.py benchmarks/JunoResBench/tests/test_pmt_response.py -q
```

Expected: all tests PASS.

- [ ] **Step 3: Run the full JunoResBench suite**

Run:

```bash
python -m pytest benchmarks/JunoResBench/tests -q
```

Expected: all tests PASS. Tests requiring an externally mounted release may SKIP only when their documented mount is unavailable; no FAIL or ERROR is acceptable.

- [ ] **Step 4: Check scope and whitespace**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only files named in this plan plus pre-existing unrelated user changes are modified.

- [ ] **Step 5: Commit first-batch documentation**

```bash
git add benchmarks/JunoResBench/docs/generator_physics_and_resolution_budget.md
git commit -m "docs(jrb): explain strengthened optical causality"
```

## Following Batches

After this plan passes review, write separate plans in this order:

1. multi-medium LS/acrylic/water Snell--Fresnel--TIR transport and fixed analytic structures;
2. PMT reflection, type-specific photocathode response, SPE/TTS templates, electronics and full-window readout;
3. calibration-run datasets, four-metric white-box evaluator, oracle/simple-probe gates, QA atlas and HTCondor release workflow.

Each batch must preserve and test the contracts introduced here before the next batch begins.
