# HTCondor generation

Run these jobs only on the designated batch cluster. They create no data in
the repository checkout; each `output` value in `jobs.tsv` must be an empty,
dedicated destination on shared storage.

```bash
cd benchmarks/JunoResBench/world_generator/condor
mkdir -p logs
export JRB_REPO_ROOT=/absolute/path/to/SimpleEvolution
condor_submit generate.submit
```

The supplied rows are the release-scale starting counts: electron probes use
1000 events per energy and positron probes use 10000 per energy. Do not reuse
an output directory: `build_task.py` rejects a non-empty target.

Before submitting a release-scale job, build and validate a small preflight:

```bash
cd "$JRB_REPO_ROOT"
python benchmarks/JunoResBench/world_generator/build_task.py \
  --task electron_single_site \
  --out /shared/jrb/electron_preflight_candidate \
  --seed 20260902 \
  --probe-events-per-point 20 \
  --controls 128 \
  --calibration-events-per-point 2

python benchmarks/JunoResBench/world_generator/validate_release.py \
  --task electron_single_site \
  --release /shared/jrb/electron_preflight_candidate
```

Open `validation/README.md` and inspect all figures. Do not submit the full job
unless the command exits zero and `validation/ACCEPTED` exists. After the full
HTCondor job completes, run the same validator on its output; preserve the
complete validation directory alongside the private truth tree. An absent
atlas, `REJECTED`, or any nonzero validator exit forbids publication.
