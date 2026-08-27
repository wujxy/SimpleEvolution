# JunoResBench — JUNO-like energy/vertex resolution benchmark

Toy detector MC benchmark: given a real event `(x, y, z, E_true, t0)`,
generate per-PMT waveforms through the full forward chain

```
E_true → photon production → N_pe → PMT response → waveform
```

The agent's task is the inverse chain:

```
waveform → hit reconstruction → Npe estimation → energy / vertex / t0 reconstruction
```

The readout window follows a **global trigger** on the summed PE rate
(100-ns causal sliding window, 200-pe threshold; dark noise participates),
so waveforms are referenced to the trigger time — the event time t0 is
scored window-relative (see `docs/effects.md` E10).

Scored on energy resolution & linearity, vertex resolution, and timing
resolution. This is **not** a full JUNO simulation and does not reproduce
JUNO-SW; it is a fast, physics-motivated detector world whose dominant
resolution-limiting effects are documented (with rationale) in
[`docs/effects.md`](docs/effects.md).

## Relationship to `waverec`

`../waverec` covers the bottom of the chain (`N_pe → SPE charge → waveform`):
SPE spectrum, pulse shaping, FADC digitization, noise. JunoResBench wraps it
and adds everything above it: photon production statistics, position-dependent
light collection, scintillation timing, TOF, TTS, dark noise.

```
juno_res_bench/
  __init__.py        public API (waverec snapshot re-exports)
  config.py          DetectorConfig: LY, mu_pe(r), scint timing, TTS, dark rate
  rng.py             per-stage RNG streams (SeedSequence.spawn)
  truth.py           EventInput / PhotonSoA / DetectorCalibration / EventTruth
  geometry.py        PMT layouts (Fibonacci uniform | JUNO CSV), TOF, direction grid
  detector.py        stage pipeline orchestrator
  stages/            s1_response .. s5_electronics (one module per stage)
  _vendor/           frozen waverec wavegen snapshot (see PROVENANCE.md)
scripts/
  check_detector.py  anchor validation + throughput + determinism self-checks
  generate_dataset.py  events -> frozen npz dataset (ragged truth layout)
  make_figures.py    intermediate-quantity distribution plots from a dataset
tests/
  test_stage0.py     schema / RNG isolation / stage reproducibility smoke tests
docs/
  effects.md          first/second-order effects list + rationale ← read first
  differences.md      toy MC vs JUNO-SW full MC, per stage ← read second
  stage_design.md     per-stage design: schema, sampling, anchors, RNG
```

## Quick start

```bash
cd benchmarks/JunoResBench
python3 scripts/check_detector.py --layout uniform   # anchors + throughput
python3 scripts/generate_dataset.py --events 100 --emin 1 --emax 8 \
    --seed 20261101 --max-wf-per-event 256 --particle-type mixed \
    --direction isotropic --out data/test.npz
python3 scripts/make_figures.py --data data/test.npz
```

`--particle-type {electron,gamma,positron,mixed}` selects the event mix
(v1: gamma Compton chains + positron annihilation are implemented;
`--mix 1,1,1` weights the mixed draw).

Measured performance (uniform layout, E ~ U(1,8) MeV): truth-only
~1.4 ms/event; with waveform synthesis ~60 ms/event (~1000/min/core);
γ/e⁺ events add a ~ms-level Compton chain in stage 1.
Datasets store the complete truth chain — per-event
`(E_true, E_dep, E_vis, e_escaped, e_scored, particle_type, n_steps, ...)`,
per-deposition-step `(pos, e_dep, e_vis, t, dir, kind)` ragged arrays,
per-PMT `(pmt_id, n_pe)` and per-PE `(t_emit, t_tof, t_rel, q_pe, pe_step)`
ragged arrays, plus digitized `adc` rows (`uint16`) with their channel ids.
See `docs/differences.md` §7 for the key-by-key layout.

## Validated anchors (uniform layout, full stage-5 physics)

| check | result | target |
|---|---|---|
| center pe @1 MeV | ~1480 | 1453 (1500 × quench 0.976 × nl 0.993) + Cherenkov |
| E_vis/E_true @1 MeV | 0.9693 | 0.9765 × nl(1 MeV) |
| Cherenkov fraction @1 MeV | 2.5% | ~2.5% (ly_cherenkov=500) |
| geometric coverage | 0.757 | ~0.75 |
| radial nonuniformity vs model | ratio 1.00 center → 0.94 @16 m | ε(r) × CE(θ) |
| post-TOF/emission time residual | 4.03 ns | TTS 4 ⊕ scatter |
| γ chain energy conservation | ≤1e-6 MeV | Σstep_e_dep + e_escape = E_true |
| γ mean free path @1 MeV | 16.9 cm | ~17 cm (NIST μ/ρ × ρ) |
| γ escape fraction @2 MeV | 0 (r≤16 m), 5.2% (r=17.5 m) | wall-proximity only |
| e⁺/e⁻ visible scale @1 MeV | 0.995 | ~0.99 (annihilation e⁻ quench) |
| o-Ps delayed fraction | matches 54.5%×Exp(3.08 ns) | effects.md A4 |
| trigger latency (t_trig − t0) | ~50 ± 14 ns | causal, after first light |
| pure-dark spurious trigger | 0/50 | >20σ below threshold |
| window-referenced t0 spread | ~29 ns (q84−q16) | t0 task dynamic range |
| electron bit-compat (v4 architecture) | golden digests 12/12 | lock vs drift |
| reproducibility (same seed) | bit-exact | bit-exact |

## Intermediate quantities & figures

Every dataset saves the full truth chain — per event
`(E_true, E_dep, E_vis, n_gamma, n_pe_total)`, per PMT
`(n_pe, hit times, spe charges)`, and per PE `(t_emit, t_hit, q)` — so all
intermediate distributions can be inspected directly from the npz (see
`docs/differences.md` §7).

Two figure families:

Dataset-based (`scripts/make_figures.py --data <npz>`):
`chain_distributions.png` (E_true→…→N_pe), `stages.png` (staged chain),
`timing.png`, `nonuniformity.png`.

Per-stage effect views (`scripts/make_stage_figures.py`, generated directly
from the detector, one figure per forward-model stage):
- `stage1.png` — E_vis/E_true: Birks + low-E nonlinearity curve; v1
  per-particle quenching response (e⁻/γ/e⁺ ⟨E_vis⟩/E_ref vs E)
- `stage2.png` — N_γ fluctuation vs √N, 4-component emission time, Cherenkov fraction vs E
- `stage3.png` — A_proj/d² weight pattern, Cherenkov cone in arrivals, scatter timing spread
- `stage4.png` — CE(θ) suppression vs incidence angle, per-PMT PDE recovery (corr 0.96), yield anchor
- `stage5.png` — clean vs dark+afterpulse waveforms, per-PMT time-offset residuals

Per-link chain figures (`scripts/make_chain_figures.py`, one PNG per chain
link E_true→N_γ→N_pe→Q_PMT→waveform, plus trace comparisons from
`scripts/make_trace_figures.py` and `scripts/make_hit_time_figure.py`):
- `chain_s1_etru.png` — E_dep/E_vis ratio curves (deterministic e branch)
- `chain_s2_pull.png` — N_γ Poisson pull check + Cherenkov fraction
- `chain_s4_pe_map.png` — single-event PE sky map (Mollweide, trace)
- `chain_s4_npe_vs_e.png` — N_pe linearity + σ/μ vs thinning prediction
- `chain_s4_pmt_hist.png` — per-PMT PE counts vs Poisson reference
- `chain_s4_efficiency.png` — realized N_pe/N_γ vs radius, fast vs trace
- `chain_s5_spe.png` — SPE charge spectrum, 1/2/3 pe sums
- `chain_s5_q_vs_npe.png` — channel charge vs true PE count + σ_Q/Q
- `chain_s5_gain.png` — gain-spread broadening of channel charge
- `chain_s5_waveforms.png` — example waveforms with true PE times
- `chain_s5_timing.png` — leading-edge vs true hit-time fidelity
- `chain_s5_dark_ap.png` — afterpulse charge tail + dark-only channels
- `chain_s5_window.png` — PE time distribution vs 1 µs window + truncation
- `chain_anatomy.png` — one-event anatomy: sky map, hit times, occupancy, waveform
- `trace_tail.png`, `trace_spectrum.png`, `hit_time_by_type.png` — trace-optics views

## Design constraints

- No Geant4 optical tracking: optical transport is folded into an analytic
  collection-efficiency function `ε(r, cosθ)`.
- Single PMT type (NNVT MCP parameters).
- Fast batch generation (≫1 kHz/core) for agent evaluation.

## Status

**All stages (0-6) complete**; the **v1 particle upgrade** adds gamma
(Compton chain, multi-point deposition, escape) and positron
(annihilation + o-Ps) branches — the electron path is bit-identical to
v0 (golden-digest locked in `tests/test_stage0.py`).

Dual-fidelity optics mode: `optics_mode="fast"` (analytic weights, scans)
and `optics_mode="trace"` (per-photon transport: wavelength-dependent
absorption + re-emission red shift + Rayleigh scattering + ESR recycling,
~19 ms/event; see `docs/trace_design.md`). The trace mode reveals the fast
mode's folded scatter-timing approximation under-represents the propagation
tail (mean TOF 141.6 ns vs 96.2 ns straight-line) — frozen benchmark
datasets use trace mode.

Frozen benchmark packages (trigger architecture, `optics_mode="trace"`;
`scripts/make_benchmark.py --name <pkg>` writes `data/jrb_<pkg>.npz` +
`blind_task_<pkg>/` (train/val truth-visible, test adc-only) against
private `blind_truth_<pkg>/`):

- **`electron`** — electron-only, the base task: waveforms → E, vertex, t0.
- **`mixed`** — electron/gamma/positron equal thirds; train/val carry
  particle-type labels (type-conditional calibration is the task).

600 events each, split 240/120/240, isotropic directions, per-event
t0 ~ U(0, 1000) ns. Seeds are 60-bit random values (see MANIFEST). Prediction
contract: npz with
`E_rec, x_rec, y_rec, z_rec, t0_rec` (t0 measured from window start);
score via `scripts/evaluate.py`.

**White-box variant** (`scripts/make_whitebox.py --name <pkg>`, built for
`electron`): byte-identical data files, scorer and metrics as the blind
package, plus the complete numpy-only forward model inside the package
(`juno_res_bench/` + a package-local `generate_dataset.py`) — the agent may
read, run and modify the generator (unlimited self-generated labeled data,
forward fits). Blind vs white-box scores sit on the same test events, so the
difference isolates the value of understanding the forward model.

Scoring conventions: energy resolution = (q84−q16)/2 of E_rec/E_ref
(quantile width; γ escape tails count), E_ref = e_true + 1.022 MeV for
positrons (JUNO convention), bias = median−1, vertex = 68% quantile of
|r_rec − r_true|, timing = (q84−q16)/2 of t0_rec − t0_ref with
t0_ref = evt_t0 − (evt_t_trigger − 300 ns). On mixed packages metrics are
reported overall AND per particle type. The blind-package meta strips the
generation seed.

Baseline to beat: `baselines/charge_centroid.py` (charge-sum energy with
train-calibrated scale, charge-centroid vertex, leading-edge-minus-TOF t0).
Reference numbers live in each `blind_truth_<pkg>/baseline_test_score.json`.
Trace-mode comparison figures: `figures/trace_tail.png`,
`figures/trace_spectrum.png` (`scripts/make_trace_figures.py`).
