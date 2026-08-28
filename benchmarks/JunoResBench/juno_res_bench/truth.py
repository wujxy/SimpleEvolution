"""Stage 0 schema contracts: event input, photon SoA, calibration, truth.

Truth levels (see docs/stage_design.md):
  event level   - scalars per event
  per-PMT level - ragged arrays over hit PMTs
  per-PE level  - ragged arrays over photoelectrons
plus a detector-level calibration record (per-PMT, fixed per detector,
saved in datasets but stripped from blind packages) and, since the v1
particle upgrade, a per-deposition-step level for gamma/positron chains.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np


class ParticleType(Enum):
    ELECTRON = "electron"
    GAMMA = "gamma"        # v1
    POSITRON = "positron"  # v1


# dataset int8 encoding of particle_type (evt_particle_type column)
PARTICLE_TYPE_CODE = {
    ParticleType.ELECTRON: 0,
    ParticleType.GAMMA: 1,
    ParticleType.POSITRON: 2,
}
PARTICLE_CODE_TYPE = {v: k for k, v in PARTICLE_TYPE_CODE.items()}
PARTICLE_CODE_NAME = {v: k.value for k, v in PARTICLE_TYPE_CODE.items()}


# int8 codes used by DepositionSteps.kind and the dataset step_process column
DEPOSITION_KINDS = {
    "primary": 0,        # primary e-/e+ ionization at the vertex
    "compton": 1,        # Compton step of the primary gamma
    "photoelectric": 2,  # photoelectric absorption of the primary gamma
    "annih_compton": 3,  # Compton step of an annihilation gamma
    "annih_photo": 4,    # photoelectric absorption of an annihilation gamma
    "sub_cutoff": 5,     # absorbed locally below gamma_abs_cut_kev
}


@dataclass(frozen=True)
class EventInput:
    """One real event: the benchmark's forward-model input."""

    x_m: float
    y_m: float
    z_m: float
    e_true_mev: float
    t0_ns: float = 0.0
    direction: tuple = (0.0, 0.0, 1.0)   # initial particle direction (unit)
    particle_type: ParticleType = ParticleType.ELECTRON

    @property
    def vertex_m(self) -> np.ndarray:
        return np.asarray([self.x_m, self.y_m, self.z_m], dtype=np.float64)


@dataclass
class DepositionSteps:
    """Stage-1 output: energy depositions of one event as SoA.

    One row per deposition point. Electrons are a single step at the vertex;
    gamma/positron events carry the Compton/annihilation chain. Light is
    generated per step (stage 2) at pos_m with emission-time offset t_ns
    (o-Ps delay for annihilation gammas).

    kind: see DEPOSITION_KINDS (primary / compton / photoelectric /
    annih_compton / annih_photo / sub_cutoff).
    """

    pos_m: np.ndarray        # (M, 3) float64, deposition position
    e_dep_mev: np.ndarray    # (M,) float64, deposited energy
    e_vis_mev: np.ndarray    # (M,) float64, visible energy after quench/nl
    t_ns: np.ndarray         # (M,) float64, deposition time rel. t0
    dir: np.ndarray          # (M, 3) float64, charged-particle direction (unit)
    kind: np.ndarray         # (M,) int8

    @property
    def n_steps(self) -> int:
        return len(self.e_dep_mev)

    @classmethod
    def single(cls, vertex_m, e_dep_mev, e_vis_mev, direction, t_ns=0.0,
               kind=DEPOSITION_KINDS["primary"]) -> "DepositionSteps":
        """The electron case: one fully-contained point deposition."""
        return cls(
            pos_m=vertex_m.reshape(1, 3).astype(np.float64),
            e_dep_mev=np.asarray([e_dep_mev], np.float64),
            e_vis_mev=np.asarray([e_vis_mev], np.float64),
            t_ns=np.asarray([t_ns], np.float64),
            dir=np.asarray([direction], np.float64),
            kind=np.asarray([kind], np.int8),
        )


@dataclass
class PhotonSoA:
    """Stage-2 output: photons as structured-of-arrays (in-memory only).

    photon_type: 0 = scintillation, 1 = Cherenkov.
    step_idx:    deposition-step index per photon (int32; into the
                 DepositionSteps of the same event).
    """

    photon_type: np.ndarray   # (N,) int8
    pos_m: np.ndarray         # (N, 3) float32, emission position
    dir: np.ndarray           # (N, 3) float32, emission direction (unit)
    t_emit_ns: np.ndarray     # (N,) float32, emission time rel. t0
    step_idx: np.ndarray = None   # (N,) int32

    def __post_init__(self):
        if self.step_idx is None:
            self.step_idx = np.zeros(len(self.photon_type), np.int32)

    @classmethod
    def empty(cls) -> "PhotonSoA":
        return cls(
            photon_type=np.zeros(0, np.int8),
            pos_m=np.zeros((0, 3), np.float32),
            dir=np.zeros((0, 3), np.float32),
            t_emit_ns=np.zeros(0, np.float32),
            step_idx=np.zeros(0, np.int32),
        )

    def __len__(self) -> int:
        return len(self.photon_type)

    @classmethod
    def concatenate(cls, parts) -> "PhotonSoA":
        parts = [p for p in parts if len(p)]
        if not parts:
            return cls.empty()
        return cls(
            photon_type=np.concatenate([p.photon_type for p in parts]),
            pos_m=np.concatenate([p.pos_m for p in parts]),
            dir=np.concatenate([p.dir for p in parts]),
            t_emit_ns=np.concatenate([p.t_emit_ns for p in parts]),
            step_idx=np.concatenate([p.step_idx for p in parts]),
        )


@dataclass
class DetectorCalibration:
    """Per-PMT calibration truth (fixed per detector instance).

    pde_delta: relative PDE offset of each PMT (stage 4)
    gain:      relative gain of each PMT (stage 5)
    time_offset_ns: static per-PMT time offset (stage 5)
    tts_sigma_ns:   per-PMT TTS width (stage 4; per-PMT variation v1)
    """

    pde_delta: np.ndarray        # (N_pmt,) float64
    gain: np.ndarray             # (N_pmt,) float64
    time_offset_ns: np.ndarray   # (N_pmt,) float64
    tts_sigma_ns: np.ndarray     # (N_pmt,) float64


# ---- stage outputs -------------------------------------------------------


@dataclass(frozen=True)
class S1Output:
    """Stage 1: particle response.

    e_dep_mev/e_vis_mev are event totals (sums over steps); `steps` carries
    the per-deposition chain (electrons: one step at the vertex, t_ns=0,
    kind=primary — bit-identical legacy path). e_escape_mev is energy
    carried away by gammas that left the LS sphere (never produces light).
    """

    e_dep_mev: float
    e_vis_mev: float
    steps: Optional[DepositionSteps] = None
    e_escape_mev: float = 0.0
    particle_type: ParticleType = ParticleType.ELECTRON


@dataclass(frozen=True)
class S3Output:
    """Stage 3: optical response (photon -> arrived-at-PMT)."""

    n_arrived_pmt: np.ndarray   # (N_pmt,) int64, photons arriving at each PMT
    pmt_idx: np.ndarray         # (N_arrived,) int32, expanded PMT index per photon
    t_arrive_ns: np.ndarray     # (N_arrived,) float32, arrival time rel. t0
    t_tof_ns: np.ndarray = None    # (N_arrived,) float32, pure vertex->PMT tof
    photon_idx: np.ndarray = None  # (N_arrived,) int64, index into PhotonSoA
    det_scale: np.ndarray = None   # (N_arrived,) float64, per-photon detection
                                   # prob scale (Cherenkov hits: 1/coverage, the
                                   # explicit-geometry counterpart of the coverage
                                   # folded into the scint p_det)
    lam_nm: np.ndarray = None      # (N_arrived,) float64, photon wavelength
                                   # (trace mode; enables QE(lambda) in stage 4)


@dataclass(frozen=True)
class S4Output:
    """Stage 4: photon detection (arrived photon -> PE)."""

    pmt_idx: np.ndarray         # (N_pe,) int32
    t_hit_ns: np.ndarray        # (N_pe,) float32, PE time at anode rel. t0 (incl. TTS)
    pe_type: np.ndarray         # (N_pe,) int8, 0=scint 1=cherenkov
    n_pe_pmt: np.ndarray        # (N_pmt,) int64
    arrived_idx: np.ndarray = None  # (N_pe,) int64, index into S3 arrays


@dataclass
class EventTruth:
    """Full intermediate chain of one event (see docs/differences.md #7)."""

    # ---- event level -----------------------------------------------------
    x_m: float
    y_m: float
    z_m: float
    direction: tuple            # particle initial direction (unit, 3,)
    e_true_mev: float
    e_dep_mev: float            # total deposited (v0 e-like: == E_true)
    e_vis_mev: float            # after Birks hook (event total)
    n_gamma: int                # scintillation photons produced (fluctuating)
    n_gamma_cher: int           # Cherenkov photons produced (0 in stage 0)
    n_arrived: int              # photons arrived at PMTs (stage 3)
    n_pe_produced: int          # PEs detected over all PMTs (stage 4)
    n_pe_total: int             # PEs inside the readout window (observable)
    t0_ns: float
    t_trigger_ns: float          # global-trigger time defining the window

    # ---- per hit-PMT (physics pe > 0), sorted by pmt id -------------------
    pmt_ids: np.ndarray         # int32
    n_pe_pmt: np.ndarray        # int32, in-window counts

    # ---- per PE, ragged via pe_offsets (len = len(pmt_ids) + 1) ----------
    pe_offsets: np.ndarray      # int64
    pe_type: np.ndarray         # int8, 0=scint 1=cherenkov
    t_emit_ns: np.ndarray       # scintillation emission time rel. t0
    t_tof_ns: np.ndarray        # photon time of flight vertex -> PMT
    t_rel_ns: np.ndarray        # PE arrival at anode rel. window start
    q_pe: np.ndarray            # sampled SPE charge (pe units)

    # ---- waveforms, aligned with pmt_ids (None when truth_only) ----------
    adc: Optional[list] = field(default=None)
    # channel id of each adc row (== pmt_ids in hit-storage mode;
    # sorted hit ∪ in-window-dark channels in full-readout mode)
    adc_ids: Optional[np.ndarray] = field(default=None)

    # ---- v1 particle chain --------------------------------------------------
    particle_type: ParticleType = ParticleType.ELECTRON
    e_escape_mev: float = 0.0            # energy in escaped gammas
    step_pos_m: Optional[np.ndarray] = None     # (M, 3) float64
    step_e_dep_mev: Optional[np.ndarray] = None  # (M,) float64
    step_e_vis_mev: Optional[np.ndarray] = None  # (M,) float64
    step_t_ns: Optional[np.ndarray] = None       # (M,) float64
    step_dir: Optional[np.ndarray] = None        # (M, 3) float64
    step_kind: Optional[np.ndarray] = None       # (M,) int8
    pe_step: Optional[np.ndarray] = None         # (n_pe,) int32, per-PE step
