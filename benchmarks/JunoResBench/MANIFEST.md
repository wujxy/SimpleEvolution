# JunoResBench test data manifest

Small **intermediate-check** datasets only (not benchmark datasets). Both are
bit-exact reproducible with the pinned seeds; SHA256 for tamper detection.

| file | sha256 | events | E range (MeV) | waveforms | seed |
|---|---|---|---|---|---|
| data/jrb_test_small.npz | 96b90d6ebd327c995d49584392c16be75ce1bef22259d7c2e5ef6073adeec021 | 100 | 1–8 | 256 ch/event, uint16 | 20260901 |
| data/jrb_scan2k.npz | 69890cbde4748345b881ef7c37c3eb5e3dd781abd34e51320052f2f586d5c29b | 2000 | 1–8 | none (truth-only) | 20260902 |

## Benchmark package v2 (per-photon trace optics, seed 20260910)

| file | sha256 | contents |
|---|---|---|
| data/jrb_bench_v1.npz | 1083a74468398a8e960e0f4d06fd8858f4e133e0e48e95e864d85ad62ea59820 | 300 events, full truth + 192 ch/event |
| blind_task/train.npz | d74b5bf18ad0cb31c6312573f1b37d9ffae9f1425f3a1a2067512ab9e714e2eb | 120 events, truth visible |
| blind_task/val.npz | 4a378ffec4d5ca51b28b1ec8297c64c2a927b87a82e82ce345f37dd9d0f7b5f3 | 60 events, truth visible |
| blind_task/test.npz | 101b5d2358924b9a6b89b8af2564560f419e6438d824297192b983cc6ba85e4b | 120 events, adc only |
| blind_truth/test_full.npz | f50df5e0e5ed005c57d077047a726728734a2779e553939cbc29fd37af7a0dec | PRIVATE: test truth |

All datasets generated with `optics_mode="trace"`: per-photon transport with
wavelength-dependent absorption + re-emission (red shift), Rayleigh path
randomization (propagation tail, mean TOF 141.7 ns vs 96.2 straight-line),
ESR diffuse recycling. Reference to beat (charge_centroid baseline on blind
test): energy res 0.168, vertex 68% 13.6 m, timing 7.4 ns
(`blind_truth/baseline_test_score.json`).

Regenerate benchmark:
`python3 scripts/make_benchmark.py --events 300 --seed 20260910 --optics-mode trace`

Regenerate:

```bash
cd benchmarks/JunoResBench
python3 scripts/generate_dataset.py --events 100 --emin 1 --emax 8 \
    --seed 20260901 --max-wf-per-event 256 --out data/jrb_test_small.npz
python3 scripts/generate_dataset.py --events 2000 --emin 1 --emax 8 \
    --seed 20260902 --truth-only --skip-per-pe --out data/jrb_scan2k.npz
sha256sum data/*.npz
```

Notes:

- Generated with stage-5 physics: Birks ON, low-E nonlinearity ON,
  Cherenkov ON (ray-traced), scatter timing ON (a_scatter=0.03 ns/m),
  CE(θ) ON (NNVT table), per-PMT PDE spread ON (0.08), per-PMT time
  offsets ON (1 ns), afterpulses ON (1.6%, Exp 500 ns).
- `jrb_test_small.npz` carries the full truth chain (per-PE
  `t_emit/t_tof/t_rel/q_pe`) plus a capped random subset of digitized
  channels — sized for inspecting intermediate distributions
  (`scripts/make_figures.py`), not for reconstruction scoring.
- `jrb_scan2k.npz` is event-level + per-PMT counts only (~21 MB); use it for
  resolution/nonuniformity studies.
- Large frozen benchmark datasets are deliberately **not** generated yet.
