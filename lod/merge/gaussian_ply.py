"""BRDF Gaussian PLY I/O for merge LOD (see docs/mesh_to_3dgs_tree_ply_params.md)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from plyfile import PlyData, PlyElement

from progress_util import iter_progress

PLY_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("nx", "<f4"),
        ("ny", "<f4"),
        ("nz", "<f4"),
        ("base_r", "<f4"),
        ("base_g", "<f4"),
        ("base_b", "<f4"),
        ("metallic", "<f4"),
        ("roughness", "<f4"),
        ("opacity", "<f4"),
        ("scale_0", "<f4"),
        ("scale_1", "<f4"),
        ("scale_2", "<f4"),
        ("rot_0", "<f4"),
        ("rot_1", "<f4"),
        ("rot_2", "<f4"),
        ("rot_3", "<f4"),
    ]
)

PLY_PROPERTY_NAMES: Sequence[str] = tuple(PLY_DTYPE.names)


@dataclass
class BrdfGaussian:
    position: np.ndarray  # (3,) float64
    normal: np.ndarray  # (3,) float64
    basecolor: np.ndarray  # (3,) float64 sRGB
    metallic: float
    roughness: float
    opacity: float
    scale_log: np.ndarray  # (3,) float64
    rot_wxyz: np.ndarray  # (4,) float64 quaternion (w,x,y,z)

    def copy(self) -> BrdfGaussian:
        return BrdfGaussian(
            position=self.position.copy(),
            normal=self.normal.copy(),
            basecolor=self.basecolor.copy(),
            metallic=float(self.metallic),
            roughness=float(self.roughness),
            opacity=float(self.opacity),
            scale_log=self.scale_log.copy(),
            rot_wxyz=self.rot_wxyz.copy(),
        )


def _require_props(vertex) -> None:
    missing = [n for n in PLY_PROPERTY_NAMES if n not in vertex.data.dtype.names]
    if missing:
        raise KeyError(f"PLY vertex missing properties: {missing}")


def read_brdf_ply(ply_path: Path, *, progress: bool = False) -> list[BrdfGaussian]:
    data = PlyData.read(str(ply_path))
    v = data["vertex"]
    _require_props(v)
    n = len(v)
    pos = np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64)
    nrm = np.column_stack([v["nx"], v["ny"], v["nz"]]).astype(np.float64)
    base = np.column_stack([v["base_r"], v["base_g"], v["base_b"]]).astype(np.float64)
    metallic = np.asarray(v["metallic"], dtype=np.float64)
    roughness = np.asarray(v["roughness"], dtype=np.float64)
    opacity = np.asarray(v["opacity"], dtype=np.float64)
    scale_log = np.column_stack([v["scale_0"], v["scale_1"], v["scale_2"]]).astype(np.float64)
    rot_wxyz = np.column_stack(
        [v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]]
    ).astype(np.float64)

    out: list[BrdfGaussian] = []
    for i in iter_progress(
        range(n),
        total=n,
        desc=f"Load {ply_path.name}",
        enabled=progress,
        unit=" vtx",
    ):
        out.append(
            BrdfGaussian(
                position=pos[i],
                normal=nrm[i],
                basecolor=base[i],
                metallic=float(metallic[i]),
                roughness=float(roughness[i]),
                opacity=float(opacity[i]),
                scale_log=scale_log[i],
                rot_wxyz=rot_wxyz[i],
            )
        )
    return out


def gaussian_to_row(g: BrdfGaussian) -> np.ndarray:
    row = np.empty((), dtype=PLY_DTYPE)
    row["x"], row["y"], row["z"] = g.position
    row["nx"], row["ny"], row["nz"] = g.normal
    row["base_r"], row["base_g"], row["base_b"] = g.basecolor
    row["metallic"] = np.float32(g.metallic)
    row["roughness"] = np.float32(g.roughness)
    row["opacity"] = np.float32(g.opacity)
    row["scale_0"], row["scale_1"], row["scale_2"] = g.scale_log
    row["rot_0"], row["rot_1"], row["rot_2"], row["rot_3"] = g.rot_wxyz
    return row


def write_brdf_ply(
    ply_path: Path,
    gaussians: Sequence[BrdfGaussian],
    *,
    progress: bool = False,
) -> None:
    n = len(gaussians)
    rows = np.empty(n, dtype=PLY_DTYPE)
    for i in iter_progress(
        range(n),
        total=n,
        desc=f"Write {ply_path.name}",
        enabled=progress,
        unit=" vtx",
    ):
        rows[i] = gaussian_to_row(gaussians[i])
    el = PlyElement.describe(rows, "vertex")
    ply_path.parent.mkdir(parents=True, exist_ok=True)
    PlyData([el], text=False).write(str(ply_path))


def level_ply_name(depth: int) -> str:
    return f"level{depth:02d}.ply"
