"""Local charged-particle response for the synthetic LAB-like world.

The table is a compact continuous-slowing-down approximation shaped like
public electron stopping-power curves for organic scintillators.  Its values
define JunoResBench's synthetic material; they are not a real JUNO material
database.  Electron/positron stopping-power differences are below the retained
accuracy of this benchmark and therefore share this table.

Energy is in MeV, stopping power in MeV/cm, and the Birks constant in cm/MeV.
"""

import numpy as np


ENERGY_MEV = np.array([
    0.005, 0.010, 0.020, 0.050, 0.100, 0.200,
    0.500, 1.000, 2.000, 5.000, 10.000, 20.000,
])

DEDX_MEV_CM = np.array([
    31.00, 16.50, 8.80, 4.20, 2.65, 1.90,
    1.52, 1.48, 1.55, 1.75, 1.98, 2.28,
])


def electron_stopping_power_mev_cm(kinetic_mev):
    """Return log-interpolated electron stopping power in synthetic LS."""
    energy = np.clip(
        np.asarray(kinetic_mev, dtype=float), ENERGY_MEV[0], ENERGY_MEV[-1]
    )
    return np.exp(
        np.interp(np.log(energy), np.log(ENERGY_MEV), np.log(DEDX_MEV_CM))
    )


def birks_visible_mev(deposited_mev, dedx_mev_cm, kb_cm_mev):
    """Apply first-order Birks response independently to local deposits."""
    deposited = np.asarray(deposited_mev, dtype=float)
    dedx = np.asarray(dedx_mev_cm, dtype=float)
    return deposited / (1.0 + float(kb_cm_mev) * dedx)


def charged_steps(
    kinetic_mev: float,
    step_fraction: float = 0.05,
    cut_mev: float = 0.002,
):
    """Subdivide one charged track under a continuous-slowing-down model.

    Returns aligned arrays of deposited energy, kinetic energy at the step
    midpoint, and path length.  The final residual is deposited as one step so
    the returned energy sum is exactly the input within floating precision.
    """
    kinetic = float(kinetic_mev)
    if kinetic < 0:
        raise ValueError("kinetic_mev must be non-negative")
    if not 0 < step_fraction < 1:
        raise ValueError("step_fraction must lie between zero and one")
    if cut_mev <= 0:
        raise ValueError("cut_mev must be positive")
    if kinetic == 0:
        empty = np.zeros(0, dtype=float)
        return empty, empty.copy(), empty.copy()

    remaining = kinetic
    deposited = []
    midpoint = []
    while remaining > cut_mev:
        loss = min(remaining - cut_mev, max(cut_mev, step_fraction * remaining))
        if loss <= 0:
            break
        deposited.append(loss)
        midpoint.append(remaining - 0.5 * loss)
        remaining -= loss
    if remaining > 0:
        deposited.append(remaining)
        midpoint.append(0.5 * remaining)

    deposited_arr = np.asarray(deposited, dtype=float)
    midpoint_arr = np.asarray(midpoint, dtype=float)
    length_cm = deposited_arr / electron_stopping_power_mev_cm(midpoint_arr)
    return deposited_arr, midpoint_arr, length_cm
