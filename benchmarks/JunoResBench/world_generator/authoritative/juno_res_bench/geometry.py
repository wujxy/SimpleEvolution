"""PMT geometry: layouts, light collection and time of flight.

Two layout modes:
  - uniform: Fibonacci-lattice points on the PMT sphere (smooth coverage,
    reproducible, no external file needed);
  - juno csv: exact JUNO CD LPMT positions from
    data/Detector/Geometry/PMTPos_CD_LPMT.csv (CopyNo X Y Z ... in mm).
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path

import numpy as np

JUNO_LPMT_CSV = (
    "/cvmfs/juno.ihep.ac.cn/el9_amd64_gcc15/Release/J26.4.1/"
    "data/Detector/Geometry/PMTPos_CD_LPMT.csv"
)
JUNO_LPMT_TYPE_CSV = (
    "/cvmfs/juno.ihep.ac.cn/el9_amd64_gcc15/Release/J26.4.1/"
    "data/Detector/Geometry/PMTType_CD_LPMT.csv"
)

PMT_GENERIC = -1
PMT_HAMAMATSU = 0
PMT_NNVT = 1
PMT_HIGHQE_NNVT = 2
_PMT_MODEL_CODE = {
    "Hamamatsu": PMT_HAMAMATSU,
    "NNVT": PMT_NNVT,
    "HighQENNVT": PMT_HIGHQE_NNVT,
}


def fibonacci_sphere(n: int, radius_m: float) -> np.ndarray:
    """n near-uniform points on a sphere of given radius (meters)."""
    i = np.arange(n) + 0.5
    z = 1.0 - 2.0 * i / n
    phi = np.pi * (1.0 + 5.0**0.5) * i
    r_xy = np.sqrt(1.0 - z * z)
    return np.column_stack(
        (r_xy * np.cos(phi), r_xy * np.sin(phi), z)
    ) * radius_m


def _data_lines(path):
    with Path(path).open(encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith('"#'):
                continue
            yield line.split()


def _unique_rows(path, min_fields):
    rows = {}
    for parts in _data_lines(path):
        if len(parts) < min_fields:
            raise ValueError(f"unexpected CSV row in {path}: {' '.join(parts)}")
        copy_no = int(parts[0])
        if copy_no in rows:
            raise ValueError(f"duplicate CopyNo {copy_no} in {path}")
        rows[copy_no] = parts
    return rows


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class PMTLayout:
    """PMT centers on a sphere; normals point toward the detector center."""

    positions_m: np.ndarray   # (N, 3)
    copy_no: np.ndarray = None
    pmt_model: np.ndarray = None
    source: str = "synthetic"
    source_sha256: tuple = ()

    def __post_init__(self):
        positions = np.asarray(self.positions_m, dtype=np.float64)
        if positions.ndim != 2 or positions.shape[1] != 3 or not np.isfinite(positions).all():
            raise ValueError("positions_m must be a finite (N, 3) array")
        n = len(positions)
        copy_no = (np.arange(n, dtype=np.int32) if self.copy_no is None
                   else np.asarray(self.copy_no, dtype=np.int32))
        pmt_model = (np.full(n, PMT_GENERIC, dtype=np.int8)
                     if self.pmt_model is None
                     else np.asarray(self.pmt_model, dtype=np.int8))
        if copy_no.shape != (n,) or np.unique(copy_no).size != n:
            raise ValueError("copy_no must contain one unique value per PMT")
        if pmt_model.shape != (n,) or not np.isin(
            pmt_model,
            [PMT_GENERIC, PMT_HAMAMATSU, PMT_NNVT, PMT_HIGHQE_NNVT],
        ).all():
            raise ValueError("pmt_model contains an unknown code")
        object.__setattr__(self, "positions_m", positions)
        object.__setattr__(self, "copy_no", copy_no)
        object.__setattr__(self, "pmt_model", pmt_model)

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
    def from_juno_csv(
        cls,
        position_path: str | Path = JUNO_LPMT_CSV,
        type_path: str | Path = JUNO_LPMT_TYPE_CSV,
    ) -> "PMTLayout":
        """Load and align official CD-LPMT position/type rows by CopyNo."""
        positions = _unique_rows(position_path, 4)
        types = _unique_rows(type_path, 2)
        if positions.keys() != types.keys():
            raise ValueError("position/type CopyNo mismatch")
        copy_no = np.asarray(sorted(positions), dtype=np.int32)
        pos_mm = np.asarray(
            [[float(value) for value in positions[i][1:4]] for i in copy_no],
            dtype=np.float64,
        )
        models = []
        for i in copy_no:
            name = types[i][1].strip()
            if name not in _PMT_MODEL_CODE:
                raise ValueError(f"unknown PMT type: {name}")
            models.append(_PMT_MODEL_CODE[name])
        return cls(
            positions_m=pos_mm * 1e-3,
            copy_no=copy_no,
            pmt_model=np.asarray(models, dtype=np.int8),
            source="JUNO J26.4.1 CD-LPMT",
            source_sha256=(_sha256(position_path), _sha256(type_path)),
        )


def load_juno_lpmt_csv(path: str = JUNO_LPMT_CSV) -> np.ndarray:
    """Backward-compatible position-only loader."""
    rows = _unique_rows(path, 4)
    return np.asarray(
        [[float(value) for value in rows[i][1:4]] for i in sorted(rows)],
        dtype=np.float64,
    ) * 1e-3


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
