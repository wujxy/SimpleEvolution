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
