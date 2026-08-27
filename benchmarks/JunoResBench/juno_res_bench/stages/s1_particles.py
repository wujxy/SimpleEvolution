"""Stage 1 (v1) — gamma transport chain and positron annihilation.

Physics scope (docs/effects.md A3/A4/A5, parameterized as documented):

  gamma     sequential Compton chain: analytic Klein-Nishina total cross
            section -> mean free path lambda(E) = 1/(n_e * sigma_KN(E) *
            (1+pe_ratio)); free path ~ Exp(lambda); escape when the free
            path exits the LS sphere (energy leaves the detector, never
            produces light); photoelectric termination with toy
            P_PE = pe_ratio/(1+pe_ratio), pe_ratio = (E_x/E)^3; below
            gamma_abs_cut_kev the residual photon range is <= ~1 cm and is
            deposited at the last interaction point. Compton scatter angles
            from the KN differential distribution (rejection sampling,
            envelope M=2 on f(cos) = eps^2 (eps + 1/eps - sin^2 theta)).
            Deposits sit at the interaction points; the recoil-electron
            direction (Cherenkov cone axis) is the momentum transfer
            E*d - E'*d'. Hard-gamma flight time accrues at c (x-ray index
            ~1), sub-ns per step, ~2-3 ns over a full chain.

  positron  kinetic energy deposited promptly at the vertex (MeV e+/e-
            dE/dx differ by <0.5%, folded into the shared quench/nl
            constants); then annihilation — o-Ps fraction 54.5% delayed by
            Exp(3.08 ns), 3-gamma absolute branch 2.2% (1.022 MeV split
            uniformly on the simplex, independent isotropic directions —
            momentum conservation is a documented toy approximation),
            remainder prompt 2 x 511 keV back-to-back along a random axis.
            Each annihilation gamma enters the gamma chain.

RNG draw order per chain interaction (fixed, do not reorder):
  exponential(free path) -> uniform(PE vs Compton) -> [KN rejection pairs]
  -> uniform(azimuth). The electron branch consumes no RNG at all.
"""

import numpy as np

from ..config import DetectorConfig
from ..truth import (
    DEPOSITION_KINDS,
    DepositionSteps,
    EventInput,
    ParticleType,
    S1Output,
)

ELECTRON_MASS_MEV = 0.510999
ANNIH_ENERGY_MEV = 2.0 * ELECTRON_MASS_MEV      # 1.021998
CLASSICAL_R_M = 2.8179403262e-15                # classical electron radius, m
C_M_PER_NS = 0.299792458


# ---- Klein-Nishina ---------------------------------------------------------


def sigma_kn_total(energy_mev: float) -> float:
    """Total Klein-Nishina cross section per electron (m^2), analytic."""
    k = energy_mev / ELECTRON_MASS_MEV
    t = 1.0 + 2.0 * k
    term1 = (1.0 + k) / k**2 * (2.0 * (1.0 + k) / t - np.log(t) / k)
    term2 = np.log(t) / (2.0 * k)
    term3 = (1.0 + 3.0 * k) / t**2
    return 2.0 * np.pi * CLASSICAL_R_M**2 * (term1 + term2 - term3)


def _pe_ratio(energy_mev: float, cfg: DetectorConfig) -> float:
    """sigma_PE / sigma_KN toy parameterization: (E_x/E)^3."""
    e_x = cfg.gamma_pe_crossover_kev / 1000.0
    return (e_x / max(energy_mev, 1e-9)) ** 3


def gamma_mfp_m(energy_mev: float, cfg: DetectorConfig) -> float:
    """Total-interaction mean free path (m), Compton + photoelectric."""
    s_kn = sigma_kn_total(energy_mev)
    return 1.0 / (cfg.ls_electron_density_per_m3 * s_kn * (1.0 + _pe_ratio(energy_mev, cfg)))


def _sample_compton(energy_mev: float, rng) -> tuple:
    """Sample (cos_theta, E_prime/E) from the KN differential distribution."""
    k = energy_mev / ELECTRON_MASS_MEV
    while True:
        ct = rng.uniform(-1.0, 1.0)
        eps = 1.0 / (1.0 + k * (1.0 - ct))
        f = eps * eps * (eps + 1.0 / eps - (1.0 - ct * ct))
        if rng.uniform(0.0, 1.0) < f / 2.0:      # envelope M = 2
            return ct, eps


def _orthonormal(d: np.ndarray) -> tuple:
    a = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(d, a)
    u /= np.linalg.norm(u)
    return u, np.cross(d, u)


def _rotate(d: np.ndarray, cos_t: float, sin_t: float, phi: float) -> np.ndarray:
    u, v = _orthonormal(d)
    return cos_t * d + sin_t * (np.cos(phi) * u + np.sin(phi) * v)


def _exit_distance(pos: np.ndarray, d: np.ndarray, radius_m: float) -> float:
    """Distance along unit d to leave the sphere of radius R (m)."""
    b = float((pos * d).sum())
    return -b + np.sqrt(b * b + (radius_m**2 - float((pos * pos).sum())))


# ---- chain accumulator ------------------------------------------------------


class _Acc:
    """Mutable deposition accumulator (lists -> DepositionSteps)."""

    def __init__(self):
        self.pos = []
        self.e_dep = []
        self.e_vis = []
        self.t = []
        self.dir = []
        self.kind = []

    def deposit(self, pos, e_dep_mev, t_ns, direction, kind: int,
                cfg: DetectorConfig):
        self.pos.append(np.asarray(pos, np.float64))
        self.e_dep.append(float(e_dep_mev))
        self.e_vis.append(cfg.quench(e_dep_mev) * cfg.nl_correction(e_dep_mev))
        self.t.append(float(t_ns))
        self.dir.append(np.asarray(direction, np.float64))
        self.kind.append(kind)

    def finish(self) -> DepositionSteps:
        return DepositionSteps(
            pos_m=np.asarray(self.pos, np.float64).reshape(-1, 3),
            e_dep_mev=np.asarray(self.e_dep, np.float64),
            e_vis_mev=np.asarray(self.e_vis, np.float64),
            t_ns=np.asarray(self.t, np.float64),
            dir=np.asarray(self.dir, np.float64).reshape(-1, 3),
            kind=np.asarray(self.kind, np.int8),
        )


def _gamma_chain(pos_m, direction, energy_mev, t_ns, kind_compton: int,
                 kind_photo: int, cfg: DetectorConfig, rng, acc: _Acc) -> float:
    """Follow one gamma to absorption or escape; returns escaped energy (MeV)."""
    pos = np.asarray(pos_m, np.float64).copy()
    d = np.asarray(direction, np.float64)
    d = d / np.linalg.norm(d)
    e = float(energy_mev)
    t = float(t_ns)
    cut = cfg.gamma_abs_cut_kev / 1000.0
    for _ in range(cfg.gamma_max_steps):
        if e <= cut:
            # residual range <= ~1 cm: absorb at the last interaction point
            acc.deposit(pos, e, t, d, DEPOSITION_KINDS["sub_cutoff"], cfg)
            return 0.0
        lam = gamma_mfp_m(e, cfg)
        s = rng.exponential(lam)
        if s > _exit_distance(pos, d, cfg.nonuniform_radius_m):
            return e                                  # escaped the LS
        pos = pos + s * d
        t += s / C_M_PER_NS
        pr = _pe_ratio(e, cfg)
        if rng.uniform() < pr / (1.0 + pr):
            acc.deposit(pos, e, t, d, kind_photo, cfg)  # photoelectric
            return 0.0
        ct, eps = _sample_compton(e, rng)
        e2 = e * eps
        e_rec = e - e2
        phi = rng.uniform(0.0, 2.0 * np.pi)
        sin_t = np.sqrt(max(0.0, 1.0 - ct * ct))
        d_new = _rotate(d, ct, sin_t, phi)
        if e_rec > 1e-9:
            p_el = e * d - e2 * d_new       # momentum transfer = recoil electron
            norm = np.linalg.norm(p_el)
            e_dir = p_el / norm if norm > 1e-12 else d
            acc.deposit(pos, e_rec, t, e_dir, kind_compton, cfg)
        d = d_new
        e = e2
    # safety net: should not happen (energy-conservation test trips on it)
    acc.deposit(pos, e, t, d, DEPOSITION_KINDS["sub_cutoff"], cfg)
    return 0.0


# ---- event-level branches ---------------------------------------------------


def run_s1_gamma(event: EventInput, cfg: DetectorConfig, rng) -> S1Output:
    acc = _Acc()
    escaped = _gamma_chain(
        event.vertex_m, event.direction, event.e_true_mev, 0.0,
        DEPOSITION_KINDS["compton"], DEPOSITION_KINDS["photoelectric"],
        cfg, rng, acc,
    )
    steps = acc.finish()
    return S1Output(
        e_dep_mev=float(steps.e_dep_mev.sum()),
        e_vis_mev=float(steps.e_vis_mev.sum()),
        steps=steps, e_escape_mev=escaped, particle_type=ParticleType.GAMMA,
    )


def run_s1_positron(event: EventInput, cfg: DetectorConfig, rng) -> S1Output:
    acc = _Acc()
    acc.deposit(event.vertex_m, event.e_true_mev, 0.0, event.direction,
                DEPOSITION_KINDS["primary"], cfg)
    # annihilation branching: one uniform decides 3gamma / o-Ps 2gamma /
    # prompt 2gamma (3gamma is a subset of o-Ps; absolute fraction 2.2%)
    u = rng.uniform()
    if u < cfg.three_gamma_frac:
        mode = "3g"
        t_ann = rng.exponential(cfg.ops_tau_ns)
    elif u < cfg.ops_fraction:
        mode = "2g"
        t_ann = rng.exponential(cfg.ops_tau_ns)
    else:
        mode = "2g"
        t_ann = 0.0                                   # prompt (p-Ps / direct)

    escaped = 0.0
    kc, kp = DEPOSITION_KINDS["annih_compton"], DEPOSITION_KINDS["annih_photo"]
    if mode == "3g":
        w = rng.exponential(1.0, 3)                   # uniform on the simplex
        for e_i, d_i in zip(ANNIH_ENERGY_MEV * w / w.sum(), _isotropic3(rng)):
            escaped += _gamma_chain(event.vertex_m, d_i, e_i, t_ann, kc, kp,
                                    cfg, rng, acc)
    else:
        axis = _isotropic3(rng)[0]
        for sign in (1.0, -1.0):
            escaped += _gamma_chain(event.vertex_m, sign * axis,
                                    ELECTRON_MASS_MEV, t_ann, kc, kp,
                                    cfg, rng, acc)
    steps = acc.finish()
    return S1Output(
        e_dep_mev=float(steps.e_dep_mev.sum()),
        e_vis_mev=float(steps.e_vis_mev.sum()),
        steps=steps, e_escape_mev=escaped, particle_type=ParticleType.POSITRON,
    )


def _isotropic3(rng) -> np.ndarray:
    """Three independent isotropic unit directions (3-gamma toy)."""
    out = []
    for _ in range(3):
        ct = rng.uniform(-1.0, 1.0)
        phi = rng.uniform(0.0, 2.0 * np.pi)
        st = np.sqrt(max(0.0, 1.0 - ct * ct))
        out.append(np.array([st * np.cos(phi), st * np.sin(phi), ct]))
    return np.asarray(out)
