"""PMT geometry: layouts, light collection and time of flight.

Two layout modes:
  - uniform: Fibonacci-lattice points on the PMT sphere (smooth coverage,
    reproducible, no external file needed);
  - juno csv: exact JUNO CD LPMT positions from
    data/Detector/Geometry/PMTPos_CD_LPMT.csv (CopyNo X Y Z ... in mm).
"""

from dataclasses import dataclass

import numpy as np

JUNO_LPMT_CSV = (
    "/cvmfs/juno.ihep.ac.cn/el9_amd64_gcc15/Release/J26.4.1/"
    "data/Detector/Geometry/PMTPos_CD_LPMT.csv"
)


def fibonacci_sphere(n: int, radius_m: float) -> np.ndarray:
    """n near-uniform points on a sphere of given radius (meters)."""
    i = np.arange(n) + 0.5
    z = 1.0 - 2.0 * i / n
    phi = np.pi * (1.0 + 5.0**0.5) * i
    r_xy = np.sqrt(1.0 - z * z)
    return np.column_stack(
        (r_xy * np.cos(phi), r_xy * np.sin(phi), z)
    ) * radius_m


def load_juno_lpmt_csv(path: str = JUNO_LPMT_CSV) -> np.ndarray:
    """Read JUNO PMTPos_CD_LPMT.csv -> (N, 3) positions in meters.

    Format: CopyNo X Y Z theta phi (mm / deg); '#' and quoted comment lines
    are skipped.
    """
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith('"#'):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            rows.append((float(parts[1]), float(parts[2]), float(parts[3])))
    pos_mm = np.asarray(rows, dtype=np.float64)
    if pos_mm.ndim != 2 or pos_mm.shape[1] != 3:
        raise ValueError(f"unexpected CSV layout in {path}")
    return pos_mm * 1e-3


@dataclass(frozen=True)
class PMTLayout:
    """PMT centers on a sphere; normals point toward the detector center."""

    positions_m: np.ndarray   # (N, 3)

    @property
    def n_pmt(self) -> int:
        return self.positions_m.shape[0]

    @property
    def radius_m(self) -> float:
        return float(np.linalg.norm(self.positions_m, axis=1).mean())

    @property
    def inward_normals(self) -> np.ndarray:
        """Unit vectors from each PMT toward the origin."""
        n = np.linalg.norm(self.positions_m, axis=1, keepdims=True)
        return -self.positions_m / n

    @classmethod
    def uniform(cls, n_pmt: int = 17612, radius_m: float = 19.365) -> "PMTLayout":
        return cls(positions_m=fibonacci_sphere(n_pmt, radius_m))

    @classmethod
    def from_juno_csv(cls, path: str = JUNO_LPMT_CSV) -> "PMTLayout":
        return cls(positions_m=load_juno_lpmt_csv(path))


def nearest_pmt_indices(
    layout: PMTLayout, dirs: np.ndarray, chunk: int = 4096
) -> np.ndarray:
    """Exact nearest-PMT index for each unit direction (chunked argmax).

    Exact alternative to DirectionGrid; O(N_dir x N_pmt) but chunked, fine
    for the few hundred Cherenkov photons per event.
    """
    pmts = layout.positions_m / np.linalg.norm(
        layout.positions_m, axis=1, keepdims=True
    )
    dirs = dirs / np.linalg.norm(dirs, axis=1, keepdims=True)
    out = np.empty(len(dirs), dtype=np.int32)
    for i in range(0, len(dirs), chunk):
        out[i : i + chunk] = np.argmax(dirs[i : i + chunk] @ pmts.T, axis=1)
    return out


def coverage_fraction(layout: PMTLayout, pmt_diameter_m: float) -> float:
    """Geometric PMT coverage: N * pi(d/2)^2 / (4 pi R^2)."""
    n = layout.n_pmt
    r = layout.radius_m
    return n * np.pi * (pmt_diameter_m / 2.0) ** 2 / (4.0 * np.pi * r**2)


def tof_ns(pmt_positions_m: np.ndarray, vertex_m: np.ndarray, n_ls: float) -> np.ndarray:
    """Time of flight (ns) from the vertex to every PMT through LS.

    Straight-line geometric path; group velocity c/n with
    c = 0.299792458 m/ns. Full-scale path ~19.4 m -> ~96 ns.
    """
    d = np.linalg.norm(pmt_positions_m - vertex_m, axis=1)
    return d / (0.299792458 / n_ls)


def tof_ns_steps(pmt_positions_m: np.ndarray, step_pos_m: np.ndarray,
                 n_ls: float) -> np.ndarray:
    """TOF (ns) from each deposition step to every PMT: (M, N_pmt).

    Broadcast form of tof_ns; row k is bit-identical to
    tof_ns(pmt_positions_m, step_pos_m[k], n_ls) (same op sequence,
    np.linalg.norm over the last axis).
    """
    rel = pmt_positions_m[None, :, :] - np.asarray(step_pos_m)[:, None, :]
    d = np.linalg.norm(rel, axis=2)
    return d / (0.299792458 / n_ls)


def scint_weights_steps(layout: PMTLayout, step_pos_m: np.ndarray,
                        pmt_diameter_m: float) -> np.ndarray:
    """Normalized per-PMT arrival weights for each deposition step: (M, N_pmt).

    w_i(step) proportional to A_proj(cos_inc)/d^2 (see s3_optics.scint_weights
    for the physics). Row k is bit-identical to scint_weights evaluated at
    step_pos_m[k]; vectorized in one broadcast so multi-step events stay fast.
    """
    pos = layout.positions_m
    steps = np.asarray(step_pos_m, dtype=np.float64)
    rel = pos[None, :, :] - steps[:, None, :]
    d = np.linalg.norm(rel, axis=2)
    cos_inc = -np.einsum("mpk,pk->mp", rel, layout.inward_normals) / d
    a_proj = np.pi * (pmt_diameter_m / 2.0) ** 2
    w = np.clip(cos_inc, 0.0, None) * a_proj / np.maximum(d**2, 1e-9)
    return w / w.sum(axis=1, keepdims=True)


class DirectionGrid:
    """Equal-solid-angle direction bins with nearest-PMT lookup.

    Used by the stage-3 Cherenkov ray pipeline: a photon direction maps to
    the PMT whose center is angularly closest to the ray's intersection
    point with the PMT sphere. Bins are (cosθ, φ) cells (~0.5° at the
    default n_theta=360, matching the ~0.86° PMT spacing); each bin center
    is matched to its nearest PMT once at build time (chunked brute force).
    """

    def __init__(self, bin_dirs: np.ndarray, pmt_idx: np.ndarray, n_theta: int):
        self.bin_dirs = bin_dirs       # (M, 3) float64 unit vectors
        self.pmt_idx = pmt_idx         # (M,) int32, nearest PMT per bin
        self.n_theta = n_theta
        self.n_phi = 2 * n_theta

    @classmethod
    def for_layout(cls, layout: PMTLayout, n_theta: int = 360,
                   chunk: int = 512) -> "DirectionGrid":
        ct_edges = np.linspace(-1.0, 1.0, n_theta + 1)
        phi_edges = np.linspace(0.0, 2.0 * np.pi, 2 * n_theta + 1)
        ct_c = 0.5 * (ct_edges[:-1] + ct_edges[1:])
        phi_c = 0.5 * (phi_edges[:-1] + phi_edges[1:])
        cc, pc = np.meshgrid(ct_c, phi_c, indexing="ij")
        st = np.sqrt(1.0 - cc * cc)
        dirs = np.column_stack(
            (st.ravel() * np.cos(pc.ravel()), st.ravel() * np.sin(pc.ravel()), cc.ravel())
        )
        pmts = layout.positions_m / np.linalg.norm(
            layout.positions_m, axis=1, keepdims=True
        )
        pmt_idx = np.empty(len(dirs), dtype=np.int32)
        for i in range(0, len(dirs), chunk):
            cos = dirs[i : i + chunk] @ pmts.T          # (b, N)
            pmt_idx[i : i + chunk] = np.argmax(cos, axis=1)
        return cls(dirs, pmt_idx, n_theta)

    def lookup(self, dirs: np.ndarray) -> np.ndarray:
        """Nearest-PMT index for each unit direction (via its bin)."""
        ct = np.clip(dirs[..., 2], -1.0, 1.0)
        phi = np.mod(np.arctan2(dirs[..., 1], dirs[..., 0]), 2.0 * np.pi)
        i_theta = np.minimum(
            ((ct + 1.0) / 2.0 * self.n_theta).astype(int), self.n_theta - 1
        )
        i_phi = np.minimum((phi / (2.0 * np.pi) * self.n_phi).astype(int), self.n_phi - 1)
        return self.pmt_idx[i_theta * self.n_phi + i_phi]
