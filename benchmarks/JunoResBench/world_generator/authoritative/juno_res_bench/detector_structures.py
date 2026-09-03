"""Cheap fixed-coordinate optical occluders for trace-mode transport.

The forms preserve the main reconstruction consequence of JUNO structures
without claiming CAD fidelity. Dimensions/optical depths not fixed by public
geometry are synthetic and are intentionally centralized here.
"""

import numpy as np


N_ACRYLIC_NODES = 590
NODE_ANGULAR_RADIUS_RAD = np.radians(1.0)


def _fibonacci_nodes(n=N_ACRYLIC_NODES):
    i = np.arange(n, dtype=float)
    z = 1.0 - 2.0 * (i + 0.5) / n
    phi = i * (np.pi * (3.0 - np.sqrt(5.0)))
    radial = np.sqrt(1.0 - z * z)
    return np.column_stack((radial * np.cos(phi), radial * np.sin(phi), z))


_NODE_DIRS = _fibonacci_nodes()


def _near_node(unit_points):
    """O(N) nearest-candidate test for the z-ordered Fibonacci lattice."""
    estimate = np.rint((1.0 - unit_points[:, 2]) * N_ACRYLIC_NODES / 2.0 - 0.5)
    estimate = estimate.astype(int)
    candidates = np.clip(
        estimate[:, None] + np.arange(-6, 7)[None, :],
        0, N_ACRYLIC_NODES - 1,
    )
    dots = np.einsum("ij,ikj->ik", unit_points, _NODE_DIRS[candidates])
    return np.max(dots, axis=1) >= np.cos(NODE_ANGULAR_RADIUS_RAD)


def structure_transmission(hit_positions_m, acrylic_radius_m, enabled=True):
    """Transmission at outward acrylic crossings for fixed analytic structures."""
    points = np.asarray(hit_positions_m, float)
    if not enabled:
        return np.ones(len(points), float)
    unit = points / np.linalg.norm(points, axis=1, keepdims=True)
    transmission = np.ones(len(points), float)

    # Public-geometry-constrained synthetic optical depths: top chimney,
    # acrylic connection nodes, and broad equatorial/meridional support masks.
    rho = np.linalg.norm(points[:, :2], axis=1)
    chimney = (rho < 0.65) & (points[:, 2] > 0.0)
    transmission[chimney] *= 0.20
    transmission[_near_node(unit)] *= 0.55

    phi = np.arctan2(unit[:, 1], unit[:, 0])
    support = ((np.abs(unit[:, 2]) < 0.012)
               | (np.abs(np.sin(6.0 * phi)) < 0.025))
    transmission[support] *= 0.72
    return transmission
