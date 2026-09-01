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

After a job completes, run the task's private release validator on the same
cluster and preserve its JSON report alongside the private truth tree.
