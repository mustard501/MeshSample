#!/usr/bin/env python3
"""
Build an .m2lt LOD tree using axis-aligned bounding boxes (AABBs) per Gaussian.

Geometry (aligned with Mesh2Splat PLY export / ``parsers.cpp``)
---------------------------------------------------------------
Each vertex uses ``x,y,z``, log-scales ``scale_0..2`` (base semi-axes are
``sigma[j] = exp(scale_j)``), and quaternion ``rot_0..3`` as ``glm::quat(w,x,y,z)``.
Columns of ``R`` are the oriented ellipsoid axes.

We bound the **3σ iso-surface ellipsoid** (same shape, semi-axes scaled by **3**):
``sigma_prime[j] = 3 * sigma[j]``. Its **tight axis-aligned bounding box** in world
coordinates uses the ellipsoid support radii:

    extent[k] = sqrt( sum_j R[k,j]^2 * sigma_prime[j]^2 )

This is **not** the AABB of an intermediate oriented box (no ``Σ |R||σ|`` looseness).

Parent rule (fine → coarse)
---------------------------
For each Gaussian at level ``l ≥ 2``, among all Gaussians at level ``l−1`` compare
**axis-aligned intersection volumes** between the two Gaussians' AABBs.

- Default ``--overlap max``: choose the coarse Gaussian with **largest**
  intersection volume (usual spatial coherence).
- ``--overlap min``: choose **smallest strictly positive** intersection volume;
  if none intersect, fall back to **nearest centre** (Euclidean).

Ties → smaller coarse vertex index (NumPy ``argmax`` / ``argmin`` behaviour).

If ``max`` mode has zero overlap for everyone, fall back to nearest centre as well.

Implementation batches fine Gaussians to limit peak memory ``O(chunk × N_coarse)``.

Output matches ``tree_file_format.md`` (format version 2, same layout as
``build_center_distance_lod_tree.py``).

Dependencies: numpy, scipy, plyfile, tqdm — see ``requirements-lod.txt``.

Example
-------
  python build_aabb_overlap_lod_tree.py ./my_lod_dir -o ./tree_aabb.m2lt
  python build_aabb_overlap_lod_tree.py ./my_lod_dir --no-progress
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
from plyfile import PlyData
from scipy.spatial.transform import Rotation

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None  # type: ignore[misc, assignment]

_LOD_DIR = Path(__file__).resolve().parent
if str(_LOD_DIR) not in sys.path:
    sys.path.insert(0, str(_LOD_DIR))

from build_center_distance_lod_tree import (  # noqa: E402
    _assign_global_ids,
    _discover_level_plys,
    write_m2lt,
)
from build_center_distance_lod_tree import M2LT_VERSION  # noqa: E402


def _read_gaussian_aabbs(ply_path: Path) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Load centres and axis-aligned bounds for each Gaussian.

    Returns
    -------
    centre : (N, 3) float64
    lo, hi : (N, 3) float64 inclusive-standard AABB [lo, hi)
    intersection uses half-open-like checks via strict ``lo < hi`` per axis.
    """
    data = PlyData.read(str(ply_path))
    v = data["vertex"]
    need = (
        "x",
        "y",
        "z",
        "scale_0",
        "scale_1",
        "scale_2",
        "rot_0",
        "rot_1",
        "rot_2",
        "rot_3",
    )
    for name in need:
        if name not in v:
            raise KeyError(f"{ply_path}: vertex missing property `{name}`")

    x = np.asarray(v["x"], dtype=np.float64)
    y = np.asarray(v["y"], dtype=np.float64)
    z = np.asarray(v["z"], dtype=np.float64)
    centre = np.column_stack([x, y, z])

    ls0 = np.asarray(v["scale_0"], dtype=np.float64)
    ls1 = np.asarray(v["scale_1"], dtype=np.float64)
    ls2 = np.asarray(v["scale_2"], dtype=np.float64)
    sigma = np.column_stack([np.exp(ls0), np.exp(ls1), np.exp(ls2)])
    sigma_prime = 3.0 * sigma

    q0 = np.asarray(v["rot_0"], dtype=np.float64)
    q1 = np.asarray(v["rot_1"], dtype=np.float64)
    q2 = np.asarray(v["rot_2"], dtype=np.float64)
    q3 = np.asarray(v["rot_3"], dtype=np.float64)
    quat_wxyz = np.column_stack([q0, q1, q2, q3])
    norms = np.linalg.norm(quat_wxyz, axis=1)
    if np.any(norms < 1e-12):
        raise ValueError(f"{ply_path}: degenerate quaternion row(s)")
    quat_wxyz = quat_wxyz / norms[:, np.newaxis]
    # scipy Rotation expects [x,y,z,w]; PLY matches glm::quat(w,x,y,z)
    quat_xyzw = np.column_stack(
        [quat_wxyz[:, 1], quat_wxyz[:, 2], quat_wxyz[:, 3], quat_wxyz[:, 0]]
    )
    rmat = Rotation.from_quat(quat_xyzw).as_matrix()
    # Tight AABB for ellipsoid with semi-axes sigma_prime along columns of R.
    extent = np.sqrt(np.sum((rmat * sigma_prime[:, np.newaxis, :]) ** 2, axis=2))
    lo = centre - extent
    hi = centre + extent

    return centre, lo, hi


def _intersection_volume_broadcast(
    coarse_lo: np.ndarray,
    coarse_hi: np.ndarray,
    fine_lo: np.ndarray,
    fine_hi: np.ndarray,
) -> np.ndarray:
    """
    coarse_* : (Nc, 3), fine_* : (Nf, 3)

    Returns volumes (Nf, Nc), zero where disjoint.
    """
    fl = fine_lo[:, np.newaxis, :]
    fh = fine_hi[:, np.newaxis, :]
    cl = coarse_lo[np.newaxis, :, :]
    ch = coarse_hi[np.newaxis, :, :]
    lo = np.maximum(cl, fl)
    hi = np.minimum(ch, fh)
    valid = np.all(lo < hi, axis=2)
    vol = np.prod(np.maximum(hi - lo, 0.0), axis=2)
    vol[~valid] = 0.0
    return vol


def _parents_for_layer_pair(
    coarse_centre: np.ndarray,
    coarse_lo: np.ndarray,
    coarse_hi: np.ndarray,
    fine_centre: np.ndarray,
    fine_lo: np.ndarray,
    fine_hi: np.ndarray,
    *,
    chunk: int,
    overlap_mode: str,
    progress: bool,
    progress_desc: str,
) -> np.ndarray:
    """Local coarse indices for each fine Gaussian, shape (Nf,) int64."""
    n_f = fine_centre.shape[0]
    out = np.zeros(n_f, dtype=np.int64)
    n_c = coarse_centre.shape[0]

    pbar = None
    if progress and tqdm is not None:
        pbar = tqdm(
            total=n_f,
            desc=progress_desc,
            unit="gauss",
            unit_scale=False,
            mininterval=0.2,
        )
    elif progress and n_f > 0:
        print(f"{progress_desc}: 0/{n_f} Gaussians …", flush=True)

    try:
        last_report = 0
        report_step = max(4096, min(262144, n_f // 50 or 1))

        for start in range(0, n_f, chunk):
            end = min(start + chunk, n_f)
            vol = _intersection_volume_broadcast(
                coarse_lo,
                coarse_hi,
                fine_lo[start:end],
                fine_hi[start:end],
            )
            chunk_rows = vol.shape[0]
            if overlap_mode == "max":
                best = np.argmax(vol, axis=1)
                vb = vol[np.arange(chunk_rows), best]
                need_nn = vb <= 0.0
            elif overlap_mode == "min":
                masked = np.where(vol > 0.0, vol, np.inf)
                best = np.argmin(masked, axis=1)
                vb = masked[np.arange(chunk_rows), best]
                need_nn = np.isinf(vb)
            else:
                raise ValueError(f"unknown overlap_mode {overlap_mode!r}")

            if np.any(need_nn):
                fc = fine_centre[start:end]
                dists = np.linalg.norm(
                    coarse_centre[np.newaxis, :, :] - fc[:, np.newaxis, :],
                    axis=2,
                )
                nn = np.argmin(dists, axis=1)
                best = np.where(need_nn, nn, best)
            out[start:end] = best

            if pbar is not None:
                pbar.update(end - start)
                pbar.set_postfix_str(f"≤#{end - 1}")
            elif progress and (end - last_report >= report_step or end == n_f):
                print(
                    f"{progress_desc}: {end}/{n_f} Gaussians",
                    flush=True,
                )
                last_report = end
    finally:
        if pbar is not None:
            pbar.close()

    assert np.all((out >= 0) & (out < n_c))
    return out


def build_parents_aabb_overlap(
    geom_by_level: List[Tuple[np.ndarray, np.ndarray, np.ndarray]],
    *,
    chunk: int,
    overlap_mode: str,
    progress: bool,
) -> Tuple[np.ndarray, int]:
    """
    geom_by_level[i] = (centre, lo, hi), coarsest level first.

    Returns ``parent`` array of length N (same semantics as centre-distance builder).
    """
    L = len(geom_by_level)
    counts = [int(g[0].shape[0]) for g in geom_by_level]
    offsets, ranges = _assign_global_ids(counts)
    total_nodes = 1 + int(offsets[-1])
    parent = np.zeros(total_nodes, dtype=np.int32)

    s1, e1 = ranges[0]
    parent[s1:e1] = 0

    for li in range(1, L):
        cc, clo, chi = geom_by_level[li - 1]
        fc, flo, fhi = geom_by_level[li]
        coarse_lv = li
        fine_lv = li + 1
        n_f = int(fc.shape[0])
        n_c = int(cc.shape[0])
        desc = f"AABB parents  fine L{fine_lv:02d} ({n_f}) ← coarse L{coarse_lv:02d} ({n_c})"
        loc_idx = _parents_for_layer_pair(
            cc,
            clo,
            chi,
            fc,
            flo,
            fhi,
            chunk=chunk,
            overlap_mode=overlap_mode,
            progress=progress,
            progress_desc=desc,
        )
        sf, ef = ranges[li]
        parent[sf:ef] = np.int32(loc_idx) + int(offsets[li - 1]) + 1

    return parent, total_nodes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "data_dir",
        type=Path,
        help="Directory containing level01.ply … levelXX.ply (coarse→fine)",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path (default: <data_dir>/lod_tree_aabb.m2lt)",
    )
    ap.add_argument(
        "--chunk",
        type=int,
        default=2048,
        help="Fine Gaussians per batch vs full coarse layer (default: 2048)",
    )
    ap.add_argument(
        "--overlap",
        choices=("max", "min"),
        default="max",
        help="Pick coarse parent by largest (max) or smallest positive (min) "
        "AABB intersection volume (default: max)",
    )
    ap.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress output (tqdm or text milestones)",
    )
    args = ap.parse_args()
    data_dir = args.data_dir.resolve()
    if not data_dir.is_dir():
        print(f"Not a directory: {data_dir}", file=sys.stderr)
        return 2

    chunk = max(64, int(args.chunk))

    level_entries = _discover_level_plys(data_dir)
    geom_by_level: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for lv, pth in level_entries:
        c, lo, hi = _read_gaussian_aabbs(pth)
        geom_by_level.append((c, lo, hi))
        print(f"level {lv:02d}: {pth.name}  ({c.shape[0]} Gaussians, 3σ ellipsoid tight AABB)")

    parent, total_nodes = build_parents_aabb_overlap(
        geom_by_level,
        chunk=chunk,
        overlap_mode=args.overlap,
        progress=not args.no_progress,
    )
    counts = [int(g[0].shape[0]) for g in geom_by_level]
    offsets, _ = _assign_global_ids(counts)
    L = len(geom_by_level)

    out_path = args.output
    if out_path is None:
        out_path = data_dir / "lod_tree_aabb.m2lt"
    else:
        out_path = out_path.resolve()

    write_m2lt(out_path, L, offsets, parent)
    print(
        f"Wrote {out_path}  (nodes={total_nodes}, lod_levels={L}, "
        f"format_version={M2LT_VERSION}, overlap={args.overlap}, chunk={chunk})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
