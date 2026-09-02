# JunoResBench Electron Release and Diagnostics Design

## Goal

Turn the mounted `electron_single_site` release into the standard-mode research
world used by both coding and Scientist agents, without copying waveform data
or exposing private truth or the generator. Add release-specific diagnostic
figures that establish the generated world's intermediate particle-response
properties before agents are launched.

## Scope

This design covers the current mounted release only:

```text
/home/wujxy/mnt/lustrefs_juno26/users/lidian/jrb_v2/production/
  electron_single_site/release/
```

It does not regenerate the dataset, run the 209 GB full evaluator, or prepare
the positron tier. The release has 200 probe events at every integer energy
from 1 to 10 MeV and 7,680 continuous controls. The only acceptance objective
is energy resolution `R_1MeV <= 3.0%`; the electron vertex threshold is the
frozen `0.54 m` quality constraint. `t0` is not an output or metric for this
release because no window-referenced `t0_rel` truth was stored.

## Security and data boundary

The mounted release root contains both `public/` and `private/`. An agent
container must bind **only** the host `release/public` directory to:

```text
/data/jrb/electron_single_site_public:ro
```

It must never bind the release root, its parent, the repository's
`world_generator/`, or any `private/` path. A template-local absolute symlink
from the task data path to `/data/jrb/electron_single_site_public` is safe
because its target is the restricted bind destination, not the host mount.

Both coding and Scientist agents receive identical `/work` and `/data` worlds:

```text
/work     frozen task template with only src/ writable
/data/jrb/electron_single_site_public  public data, read-only
/scratch  per-run scratch, read-write
/state    harness-owned Scientist state, only when applicable
```

The existing `--containall --no-mount cwd,home,hostfs` container policy
remains mandatory. Smoke tests must prove that the public bind is readable and
that `/data/jrb/electron_single_site_public/../private`, `/lustrefs`,
`/datafs`, and the host repository are unavailable.

## Formal release layout

The release-side package is a small, data-free directory adjacent to the
mounted `public/` and `private/` trees. It contains only task-facing files and
a relative `public` symlink:

```text
release/
  public/                         generated data; retained in place
  private/                        evaluator-only; never bound to agents
  agent_package/
    TASK.md
    README.md
    public -> ../public
    evaluator/                    standalone public scoring utilities
    submission_api.py
```

The agent's editable repository is versioned in this project under
`examples/junoresbench_electron_single_site_std_opt/`. Its data reference is
the container-local `/data/jrb/...` path, never a host path. It contains a
minimal charge/waveform baseline under `src/`, immutable public scoring and
verification scripts, the task document, a Scientist `spec.json`, and one
launcher supporting `scientist` and `coding` arms. The baseline and public
bench script run on `public/dev`; the held-out private evaluator remains
host-side.

## Mount implementation

`singlenode/node_common.sh` currently accepts same-path read-only binds only.
Add a narrowly-scoped variable for explicit mappings, e.g.:

```text
EXTRA_RO_MOUNTS=/host/source:/container/destination
```

Each mapping is converted into one `apptainer --bind
source:destination:ro`. Existing `EXTRA_RO_BINDS` semantics must remain
unchanged. The JRB launcher exports exactly one mapping from the mounted
release's `public` directory to `/data/jrb/electron_single_site_public`.

## Task contract

The public research contract asks both agent modes to produce per-event
`(E_rec, x_rec, y_rec, z_rec)`. The public proxy score evaluates the labelled
development split with the same energy curve and vertex definition as the
hidden evaluator. It reports the 3.0% target and the 0.54 m vertex threshold,
but does not fabricate a `t0` metric. The task sheet must reference
`public/evaluation_config.json`, not the nonexistent
`evaluator/task_config.json`.

The generator mechanism, parameter files and private truth do not appear in
the template, prompt or public package. The task context gives only the data
format, reconstruction outputs and metrics; agents may independently inspect
the public data and consult public literature.

## Diagnostics

Release diagnostics consume `private/truth.npz` and public metadata only; they
do not load `segment_samples.npy` or invoke event generation. The command
writes PNGs under `benchmarks/JunoResBench/figures/electron_single_site_v2/`:

1. `energy_deposition_closure.png`: per-event deposited plus escaped energy
   minus total energy, with a stated numerical tolerance.
2. `local_quenching.png`: step visible fraction versus kinetic energy and
   versus `dE/dx`, including low (`<50 keV`) and mid (`0.5--2 MeV`) means.
3. `energy_response.png`: event `E_vis/E_true` against the ten probe energies
   with uncertainty bands from the 200-event samples.
4. `track_topology.png`: charged-step count and total path length versus true
   energy, showing that this is a multi-step electron transport rather than a
   global energy scale.
5. `probe_population.png`: released energy/role/vertex-radius distributions,
   documenting 200 probe events per point and continuous-control coverage.

These are internal release evidence, never included in the agent-visible
package. Existing historical `figures/stage*.png` and `chain_*.png` are not
used as v2 release validation because they are not derived from this frozen
release.

## Verification

- Unit tests exercise package layout, symlink target, public-only mount
  mapping parsing, immutable public files, and the corrected task-config path.
- The package assembly refuses a nonempty output and refuses any symlink that
  resolves outside `release/public` for agent-visible data.
- A smoke-only coding and Scientist container launch checks readable public
  data, writable `src/` and `/scratch`, frozen task assets, and absence of
  private/generator paths.
- Diagnostics validate energy closure and the expected low-energy quenching
  ordering before writing figures. The existing private truth gate remains
  the physics source of record.

## Out of scope / subsequent release work

Full baseline/reference hidden evaluation and a `validation.json` release
sign-off remain a deliberate later cluster job, because each online evaluation
streams the 100 GB private waveform split. A future timing task requires
regeneration with a saved window-referenced `t0_rel` truth and a separate
scoring contract.
