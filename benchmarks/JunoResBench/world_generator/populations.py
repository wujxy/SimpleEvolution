"""Deterministic input populations for the two private benchmark worlds."""

import numpy as np


ELECTRON_PROBE_MEV = np.arange(1.0, 11.0)
POSITRON_PROBE_MEV = np.array([0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 11.0])
CALIBRATION_ENERGIES_MEV = np.array([0.511, 1.022, 2.223, 4.44, 8.0])


def _vertices(rng, count, radius_m):
    radius = radius_m * rng.random(count) ** (1.0 / 3.0)
    cos_theta = rng.uniform(-1.0, 1.0, count)
    phi = rng.uniform(0.0, 2.0 * np.pi, count)
    sin_theta = np.sqrt(1.0 - cos_theta * cos_theta)
    return np.column_stack((
        radius * sin_theta * np.cos(phi),
        radius * sin_theta * np.sin(phi),
        radius * cos_theta,
    ))


def _directions(rng, count):
    cos_theta = rng.uniform(-1.0, 1.0, count)
    phi = rng.uniform(0.0, 2.0 * np.pi, count)
    sin_theta = np.sqrt(1.0 - cos_theta * cos_theta)
    return np.column_stack((
        sin_theta * np.cos(phi), sin_theta * np.sin(phi), cos_theta
    ))


def _population(energy, vertex, particle_type, role, rng):
    return {
        "evt_e_true": np.asarray(energy, dtype=float),
        "evt_vertex_m": np.asarray(vertex, dtype=float),
        "evt_direction": _directions(rng, len(energy)),
        "evt_t0_ns": rng.uniform(0.0, 1000.0, len(energy)),
        "evt_particle_type": np.full(len(energy), particle_type, dtype=np.int8),
        "evt_sample_role": np.full(len(energy), role, dtype=np.int8),
    }


def calibration_population(seed, events_per_point, fiducial_radius_m=16.0):
    """Public gamma calibration grid shared by both tasks."""
    rng = np.random.default_rng(seed)
    positions = [np.zeros(3)]
    for radius in (8.0, 14.0):
        for axis in range(3):
            for sign in (-1.0, 1.0):
                point = np.zeros(3)
                point[axis] = sign * radius
                positions.append(point)
    positions = np.asarray(positions)
    energy = np.repeat(CALIBRATION_ENERGIES_MEV, len(positions) * events_per_point)
    vertex = np.tile(np.repeat(positions, events_per_point, axis=0), (len(CALIBRATION_ENERGIES_MEV), 1))
    return _population(energy, vertex, 1, -1, rng)


def physics_population(task_name, seed, probe_events_per_point, controls, fiducial_radius_m=16.0):
    """Build shuffled probe/control truth for one task topology."""
    rng = np.random.default_rng(seed)
    if task_name == "electron_single_site":
        probes = ELECTRON_PROBE_MEV
        particle_type = 0
        control_max = 10.0
    elif task_name == "ibd_positron_multisite":
        probes = POSITRON_PROBE_MEV
        particle_type = 2
        control_max = 11.0
    else:
        raise ValueError(f"unknown task: {task_name}")
    probe_energy = np.repeat(probes, probe_events_per_point)
    strata = np.arange(controls) % 64
    control_energy = control_max * (strata + rng.random(controls)) / 64.0
    energy = np.concatenate((probe_energy, control_energy))
    roles = np.concatenate((np.zeros(len(probe_energy), dtype=np.int8), np.ones(controls, dtype=np.int8)))
    order = rng.permutation(len(energy))
    population = _population(
        energy, _vertices(rng, len(energy), fiducial_radius_m), particle_type, 0, rng
    )
    population["evt_sample_role"] = roles
    return {key: value[order] for key, value in population.items()}
