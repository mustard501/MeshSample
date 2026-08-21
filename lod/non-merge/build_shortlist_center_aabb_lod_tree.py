#!/usr/bin/env python3
"""
Build an .m2lt LOD tree by **shortlisting with centre distance, then AABB overlap**.

For each fine-level Gaussian (level ``l ≥ 2``):

1. On the coarser level ``l−1``, take the **``x`` nearest neighbours by Euclidean
   centre distance** (``scipy.spatial.cKDTree``, ``O(N_f log N_c + N_f x)``).
2. Among only those ``x`` coarse Gaussians, pick the parent with **largest**
   intersection volume between **3σ tight ellipsoid AABBs** (same geometry as
   ``build_aabb_overlap_lod_tree.py``).

If **all** shortlisted pairs have zero AABB intersection, fall back to the
**nearest centre** in the shortlist (the first KD neighbour).

``.m2lt`` layout matches ``tree_file_format.md`` (v2).

Dependencies: numpy, scipy, plyfile — optional ``tqdm`` for progress —
see ``requirements-lod.txt``.

Example
-------
  python build_shortlist_center_aabb_lod_tree.py ./my_lod_dir -o out.m2lt --shortlist-x 64
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
from scipy.spatial import cKDTree

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None  # type: ignore[misc, assignment]

_LOD_DIR = Path(__file__).resolve().parent
if str(_LOD_DIR) not in sys.path:
    sys.path.insert(0, str(_LOD_DIR))

from build_aabb_overlap_lod_tree import _read_gaussian_aabbs  # noqa: E402
from build_center_distance_lod_tree import (  # noqa: E402
    _assign_global_ids,
    _discover_level_plys,
    write_m2lt,
    M2LT_VERSION,
)


def _kd_query_knn(
    tree: cKDTree,
    points: np.ndarray,
    k: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (dists, idx) with shape (N, k) each."""
    try:
        dd, ii = tree.query(points, k=k, workers=-1)
    except TypeError:
        try:
            dd, ii = tree.query(points, k=k, n_jobs=-1)
        except TypeError:
            dd, ii = tree.query(points, k=k)
    if k == 1:
        dd = np.asarray(dd, dtype=np.float64).reshape(-1, 1)
        ii = np.asarray(ii, dtype=np.int64).reshape(-1, 1)
    else:
        dd = np.asarray(dd, dtype=np.float64)
        ii = np.asarray(ii, dtype=np.int64)
    return dd, ii


def _intersection_volume_for_candidate_pairs(
    coarse_lo: np.ndarray,
    coarse_hi: np.ndarray,
    fine_lo: np.ndarray,
    fine_hi: np.ndarray,
    cand_idx: np.ndarray,
) -> np.ndarray:
    """
    coarse_* : (Nc, 3)
    fine_*   : (B, 3)
    cand_idx : (B, K) coarse indices

    Returns volumes (B, K).
    """
    cl = coarse_lo[cand_idx]
    ch = coarse_hi[cand_idx]
    fl = fine_lo[:, np.newaxis, :]
    fh = fine_hi[:, np.newaxis, :]
    lo = np.maximum(cl, fl)
    hi = np.minimum(ch, fh)
    valid = np.all(lo < hi, axis=2)
    vol = np.prod(np.maximum(hi - lo, 0.0), axis=2)
    vol[~valid] = 0.0
    return vol


def _parents_layer_shortlist_aabb(
    coarse_centre: np.ndarray,
    coarse_lo: np.ndarray,
    coarse_hi: np.ndarray,
    fine_centre: np.ndarray,
    fine_lo: np.ndarray,
    fine_hi: np.ndarray,
    *,
    shortlist_x: int,
    chunk: int,
    progress: bool,
    progress_desc: str,
) -> np.ndarray:
    """Local coarse vertex index per fine Gaussian."""
    n_f = fine_centre.shape[0]
    n_c = coarse_centre.shape[0]
    k = max(1, min(int(shortlist_x), n_c))

    tree = cKDTree(coarse_centre)
    out = np.zeros(n_f, dtype=np.int64)

    pbar = None
    if progress and tqdm is not None:
        pbar = tqdm(
            total=n_f,
            desc=progress_desc,
            unit="gauss",
            mininterval=0.2,
        )
    elif progress and n_f > 0:
        print(f"{progress_desc}: 0/{n_f} Gaussians …", flush=True)

    try:
        last_report = 0
        report_step = max(4096, min(262144, n_f // 50 or 1))

        for start in range(0, n_f, chunk):
            end = min(start + chunk, n_f)
            fc = fine_centre[start:end]
            fl = fine_lo[start:end]
            fh = fine_hi[start:end]

            dd, ii = _kd_query_knn(tree, fc, k)
            vol = _intersection_volume_for_candidate_pairs(
                coarse_lo, coarse_hi, fl, fh, ii
            )
            pick_rel = np.argmax(vol, axis=1)
            best_vol = vol[np.arange(vol.shape[0]), pick_rel]

            fallback = ii[:, 0].copy()
            chosen = ii[np.arange(ii.shape[0]), pick_rel]
            chosen = np.where(best_vol <= 0.0, fallback, chosen)
            out[start:end] = chosen

            if pbar is not None:
                pbar.update(end - start)
                pbar.set_postfix_str(f"≤#{end - 1} kNN={k}")
            elif progress and (end - last_report >= report_step or end == n_f):
                print(f"{progress_desc}: {end}/{n_f} Gaussians", flush=True)
                last_report = end
    finally:
        if pbar is not None:
            pbar.close()

    return out


def build_parents_shortlist_center_aabb(
    geom_by_level: List[Tuple[np.ndarray, np.ndarray, np.ndarray]],
    *,
    shortlist_x: int,
    chunk: int,
    progress: bool,
) -> Tuple[np.ndarray, int]:
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
        kd_k = max(1, min(int(shortlist_x), n_c))
        desc = (
            f"kNN+AABB parents  fine L{fine_lv:02d} ({n_f}) "
            f"← coarse L{coarse_lv:02d} ({n_c})  shortlist={kd_k}"
        )
        loc_idx = _parents_layer_shortlist_aabb(
            cc,
            clo,
            chi,
            fc,
            flo,
            fhi,
            shortlist_x=shortlist_x,
            chunk=chunk,
            progress=progress,
            progress_desc=desc,
        )
        sf, ef = ranges[li]
        parent[sf:ef] = np.int32(loc_idx) + int(offsets[li - 1]) + 1

    return parent, total_nodes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir", type=Path, help="Directory with level*.ply coarse→fine")
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .m2lt (default: <data_dir>/lod_tree_shortlist_aabb.m2lt)",
    )
    ap.add_argument(
        "--shortlist-x",
        type=int,
        default=32,
        help="Number of coarse centres closest by Euclidean distance "
        "to consider before AABB (default: 32)",
    )
    ap.add_argument(
        "--chunk",
        type=int,
        default=4096,
        help="Fine Gaussians per batch (default: 4096)",
    )
    ap.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress output",
    )
    args = ap.parse_args()
    data_dir = args.data_dir.resolve()
    if not data_dir.is_dir():
        print(f"Not a directory: {data_dir}", file=sys.stderr)
        return 2

    if args.shortlist_x < 1:
        print("--shortlist-x must be >= 1", file=sys.stderr)
        return 2
    chunk = max(128, int(args.chunk))

    level_entries = _discover_level_plys(data_dir)
    geom_by_level: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for lv, pth in level_entries:
        c, lo, hi = _read_gaussian_aabbs(pth)
        geom_by_level.append((c, lo, hi))
        print(
            f"level {lv:02d}: {pth.name}  ({c.shape[0]} Gaussians, "
            "3σ tight AABB + kNN shortlist)",
            flush=True,
        )

    parent, total_nodes = build_parents_shortlist_center_aabb(
        geom_by_level,
        shortlist_x=args.shortlist_x,
        chunk=chunk,
        progress=not args.no_progress,
    )
    counts = [int(g[0].shape[0]) for g in geom_by_level]
    offsets, _ = _assign_global_ids(counts)
    L = len(geom_by_level)

    out_path = (
        args.output.resolve()
        if args.output is not None
        else (data_dir / "lod_tree_shortlist_aabb.m2lt").resolve()
    )

    write_m2lt(out_path, L, offsets, parent)
    print(
        f"Wrote {out_path}  (nodes={total_nodes}, lod_levels={L}, "
        f"format_version={M2LT_VERSION}, shortlist_x={args.shortlist_x}, chunk={chunk})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
