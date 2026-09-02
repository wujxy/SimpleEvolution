import json
from pathlib import Path

import numpy as np

from benchmarks.JunoResBench.scripts.plot_electron_single_site_release import (
    build_figures,
)


def _synthetic_release(root: Path):
    (root / "private").mkdir(parents=True)
    (root / "public/dev").mkdir(parents=True)
    energies = np.tile(np.arange(1.0, 11.0), 2)
    role = np.r_[np.zeros(10, dtype=np.int8), np.ones(10, dtype=np.int8)]
    step_kinetic = np.tile([0.02, 1.0], len(energies))
    step_dep = np.tile([0.1, 0.9], len(energies))
    step_vis = step_dep * np.tile([0.80, 0.98], len(energies))
    offsets = np.arange(0, 2 * len(energies) + 1, 2, dtype=np.int64)
    deposited = np.add.reduceat(step_dep, offsets[:-1])
    vertex = np.column_stack((np.linspace(0, 10, len(energies)), np.zeros((len(energies), 2))))
    np.savez(
        root / "private/truth.npz",
        evt_e_true=energies,
        evt_vertex_m=vertex,
        evt_sample_role=role,
        evt_e_vis=np.add.reduceat(step_vis, offsets[:-1]),
        evt_e_dep_mev=deposited,
        evt_e_escape_mev=energies - deposited,
        evt_total_energy=energies,
        step_pos_m=np.column_stack((np.arange(len(step_dep)) * 0.001, np.zeros((len(step_dep), 2)))),
        step_e_dep_mev=step_dep,
        step_e_vis_mev=step_vis,
        step_dedx_mev_cm=np.tile([8.0, 1.5], len(energies)),
        step_kinetic_mev=step_kinetic,
        step_length_m=np.full(len(step_dep), 0.001),
        step_kind=np.zeros(len(step_dep), dtype=np.int8),
        step_offsets=offsets,
    )
    np.savez(
        root / "public/dev/truth.npz",
        evt_e_true=energies,
        evt_sample_role=role,
        evt_e_vis=np.add.reduceat(step_vis, offsets[:-1]),
        evt_vertex_m=vertex,
    )
    (root / "public/evaluation_config.json").write_text(
        json.dumps({"energy_target_r_1mev": 0.03, "vertex_threshold_m": 0.54}),
        encoding="utf-8",
    )


def test_builds_five_truth_only_release_figures(tmp_path):
    release = tmp_path / "release"
    _synthetic_release(release)

    figures = build_figures(release, tmp_path / "figures")

    assert set(figures) == {
        "energy_deposition_closure",
        "local_quenching",
        "energy_response",
        "track_topology",
        "probe_population",
    }
    assert all(path.is_file() and path.stat().st_size > 0 for path in figures.values())
