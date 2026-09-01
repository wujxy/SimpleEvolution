"""Private vertex-oracle utilities for the electron release gate."""

import math

import numpy as np

from .authoritative.juno_res_bench.stages.s3_optics import scint_weights


def freeze_threshold(oracle_rms_m):
    """Allow 15% above the oracle and round upward to the next 0.1 cm."""
    if not math.isfinite(oracle_rms_m) or oracle_rms_m <= 0:
        raise ValueError("oracle vertex RMS must be positive and finite")
    return math.ceil(1.15 * float(oracle_rms_m) / 0.001) * 0.001


def charge_pattern_vertex_rms(vertices_m, layout, config, energy_mev=1.0):
    """Mean charge-pattern Cramer--Rao vertex limit for the hidden world.

    The oracle observes ideal per-PMT photoelectron counts. Its Poisson means
    use the private light-collection response; finite differences form the
    three-dimensional Fisher matrix at each generated vertex.
    """
    vertices = np.asarray(vertices_m, dtype=float).reshape(-1, 3)
    if len(vertices) == 0 or energy_mev <= 0:
        raise ValueError("vertices and positive energy are required")

    def expected_counts(vertex):
        weights = scint_weights(layout, vertex, config)
        collection = config.mu_pe_per_mev_center * config.mu_pe_ratio(np.linalg.norm(vertex))
        return energy_mev * collection * weights

    step_m = 0.01
    rms2 = []
    for vertex in vertices:
        mean = expected_counts(vertex)
        derivative = np.empty((len(mean), 3))
        for axis in range(3):
            offset = np.zeros(3)
            offset[axis] = step_m
            derivative[:, axis] = (expected_counts(vertex + offset) - expected_counts(vertex - offset)) / (2.0 * step_m)
        fisher = (derivative.T / np.maximum(mean, 1e-12)) @ derivative
        covariance = np.linalg.pinv(fisher, rcond=1e-12)
        rms2.append(float(np.trace(covariance)))
    return float(np.sqrt(np.mean(rms2)))
