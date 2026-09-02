# JunoResBench electron single-site standard research world

This template gives coding and Scientist agents the same task: reconstruct
`E_rec, x_rec, y_rec, z_rec` for 1--10 MeV single-electron events and drive
the fitted JUNO-style `R_1MeV` to 3.0% or below while satisfying the frozen
1 MeV vertex threshold.

The mounted dataset is public-only. The large sparse waveform arrays remain
on the external release filesystem and are read through a read-only mapping;
they are never copied into this repository or a run directory. The editable
surface is `src/`. Task text, scoring code, scripts and data are frozen.

Prepare the template once:

```bash
bash examples/junoresbench_electron_single_site_std_opt/setup.sh
```

Launch either arm with the same world:

```bash
bash examples/junoresbench_electron_single_site_std_opt/launch_singlenode.sh scientist runs/singlenode/jrb-electron-scientist
bash examples/junoresbench_electron_single_site_std_opt/launch_singlenode.sh coding runs/singlenode/jrb-electron-coding
```

Set `JRB_ELECTRON_PUBLIC` only when the mounted release lives somewhere other
than the default path. The launcher maps exactly that directory to
`/data/jrb/electron_single_site_public:ro`.
