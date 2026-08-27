# waverec v1 data manifest

Datasets are bit-exact reproducible from the pinned seeds below with
`scripts/generate_dataset.py`. SHA256 recorded for tamper detection.

| file | sha256 | events | total PEs | seed | overrides |
|---|---|---|---|---|---|
| data/waverec_v1_snr_nominal.npz | 408bcc7501ee5182e921d4bd6512470db3b029243aa981a3c52536e272ce7efa | 300 | 3051 | 20260824 | — |
| data/waverec_v1_snr_low.npz | 8ae344c7fd303be0384779ee734c104d6b97c874aa5f9b2620cdcbd5e89c99bb | 300 | 2980 | 20260825 | noise 1.0 mV |
| data/waverec_v1_sparse.npz | f619d3b9272d6518c8fb0f66ded69aaf2d84f2cc7b1886158fa484ab9dc158ee | 300 | 621 | 20260826 | mean pe 2 |

Regenerate all:

```bash
python3 scripts/generate_dataset.py --out data/waverec_v1_snr_nominal.npz \
    --events 300 --mean-pe 10 --seed 20260824
python3 scripts/generate_dataset.py --out data/waverec_v1_snr_low.npz \
    --events 300 --mean-pe 10 --noise-mv 1.0 --seed 20260825
python3 scripts/generate_dataset.py --out data/waverec_v1_sparse.npz \
    --events 300 --mean-pe 2 --seed 20260826
sha256sum data/*.npz
```

## Task packages (blind + white-box, 2026-08-27 re-issue)

Seeds are 60-bit random values: the generator is fast (ms/event), so
small/date-style seeds would be brute-forceable once the source ships in
`whitebox_task/`. The blind files carry no meta (seed stripped); blind_truth
keeps it. Data/scorer in the white-box package are byte-identical to blind.

| file | sha256 | events | seed |
|---|---|---|---|
| blind_task/data/waverec_train.npz | 4ef77b5490b29061bb2672a7b175878e39ded61f6acf0e0d6b87b462c07c27fb | 400 | 249405856277295613 |
| blind_task/data/waverec_val.npz | fbaefef4355c3ee44b3af77c35a3bd99bad7573908ca71222def179484392536 | 100 | 917777727599791913 |
| blind_task/data/waverec_test.npz | f4eee6a41e1b2415269d1d72fc225546e7904f071967651198140788578a214c | 300 (adc only) | 263293646208505012 |
| blind_truth/waverec_test_full.npz | 4f25dd3ef20aa0633c8676a1e71adbc7941e4f7c2550f366110063a8afc6399f | 300 (PRIVATE: + truth & meta) | 263293646208505012 |
| whitebox_task/{data,evaluate.py,TASK.md} | byte-identical to blind (data/scorer); see README | — | — |

Rebuild:

```bash
# blind files (strip meta from train/val, adc-only test, full private copy)
for s in "train 400 249405856277295613" "val 100 917777727599791913" \
         "test 300 263293646208505012"; do set -- $s
  python3 scripts/generate_dataset.py --out /tmp/wr_$1.npz \
      --events $2 --seed $3; done
python3 baselines/threshold_integrator.py \
    --data blind_truth/waverec_test_full.npz \
    --out blind_truth/baseline_test_pred.npz
python3 scripts/evaluate.py --data blind_truth/waverec_test_full.npz \
    --pred blind_truth/baseline_test_pred.npz \
    --json-out blind_truth/baseline_test_score.json
python3 scripts/make_whitebox.py   # + self-checks
sha256sum blind_task/data/*.npz blind_truth/*.npz
```

Baseline on the new test: efficiency 0.658, purity 1.000, time RMSE
1.23 ns, charge bias +0.35 (`blind_truth/baseline_test_score.json`).
