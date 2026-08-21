#!/usr/bin/env python3
"""
Two-stage LOD ``.m2lt`` tree (format v2 — same as ``build_center_distance_lod_tree.py``).

**Stage 1** — centre-distance parent links (``build_parents_nn``).

**Stage 2 — extend non-finest leaves**

After stage 1, run passes until stable:

Nodes ``A`` with semantic depth ``depth(A) ≤ L−1`` and **no CSR children** search for an
unused finer Gaussian ``B`` at depth ``depth(A)+1`` whose centre is nearest in Euclidean distance
among **unclaimed** candidates. Set ``parent[B] ← A``. Each finer global id is used at most once
(the ``claimed`` set).

Passes recompute CSR child counts so coarse nodes that become leaves after rewiring may receive
a finer child in a later pass.

``--disable-patch`` skips stage 2 (output matches plain nearest-centre tree).

Deps: numpy, scipy, plyfile — ``lod/requirements-lod.txt``

Example::

  python build_center_distance_patch_leaves_lod_tree.py ./lod_dir -o out.m2lt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.spatial import cKDTree

_LOD_DIR = Path(__file__).resolve().parent
if str(_LOD_DIR) not in sys.path:
    sys.path.insert(0, str(_LOD_DIR))

from build_center_distance_lod_tree import (  # noqa: E402
    M2LT_VERSION,
    _assign_global_ids,
    _discover_level_plys,
    _read_gaussian_centers,
    build_parents_nn,
    write_m2lt,
)


def _depth_vec(offsets: np.ndarray, L: int, n: int) -> np.ndarray:
    dpt = np.zeros(n, dtype=np.int32)
    for dd in range(1, L + 1):
        lo = int(offsets[dd - 1]) + 1
        hi_incl = int(offsets[dd])
        if lo <= hi_incl:
            dpt[lo : hi_incl + 1] = dd
    return dpt


def _stack_centers_per_global_id(
    centers_by_level: List[np.ndarray], offsets: np.ndarray, L: int, n: int
) -> np.ndarray:
    all_c = np.zeros((n, 3), dtype=np.float64)
    for li in range(L):
        lo = int(offsets[li]) + 1
        hi_incl = int(offsets[li + 1])
        blk = centers_by_level[li]
        if hi_incl - lo + 1 != blk.shape[0]:
            raise RuntimeError("internal: vertex counts vs offsets mismatch")
        all_c[lo : hi_incl + 1] = blk
    return all_c


def _precompute_depth_kdtrees(
    offsets: np.ndarray,
    centers_all: np.ndarray,
    L: int,
) -> Dict[int, Tuple[np.ndarray, cKDTree]]:
    out: Dict[int, Tuple[np.ndarray, cKDTree]] = {}
    for dd in range(1, L + 1):
        lo = int(offsets[dd - 1]) + 1
        hi_incl = int(offsets[dd])
        gids = np.arange(lo, hi_incl + 1, dtype=np.int64)
        pts = centers_all[gids]
        out[dd] = (gids, cKDTree(pts))
    return out


def _child_degrees(parent: np.ndarray) -> np.ndarray:
    n = parent.shape[0]
    cd = np.zeros(n, dtype=np.int32)
    for g in range(1, n):
        cd[int(parent[g])] += 1
    return cd


def _kd_query(
    tree: cKDTree,
    center: np.ndarray,
    k: int,
) -> Tuple[np.ndarray, np.ndarray]:
    try:
        return tree.query(center, k=k, workers=-1)
    except TypeError:
        try:
            return tree.query(center, k=k, n_jobs=-1)
        except TypeError:
            return tree.query(center, k=k)


def _query_nearest_unclaimed(
    center: np.ndarray,
    depth_target: int,
    trees: Dict[int, Tuple[np.ndarray, cKDTree]],
    claimed: set,
) -> Optional[int]:
    gids, tree = trees[depth_target]
    nk = int(gids.shape[0])
    if nk == 0:
        return None

    kk = 1
    while True:
        kq = min(max(kk, 1), nk)
        dists, iloc = _kd_query(tree, center, kq)
        iloc = np.atleast_1d(iloc).astype(np.int64, copy=False)
        dists = np.atleast_1d(dists).astype(np.float64, copy=False)
        order = np.argsort(dists)
        for oi in np.nditer(order):
            jj = int(iloc[int(oi)])
            gid_b = int(gids[jj])
            if gid_b not in claimed:
                return gid_b
        if kq >= nk:
            break
        kk *= 4
        if kk > nk:
            kk = nk
    return None


def refine_parents_extend_nonfinest_leaves(
    parent: np.ndarray,
    *,
    depths: np.ndarray,
    centers_all: np.ndarray,
    kdt_meta: Dict[int, Tuple[np.ndarray, cKDTree]],
    L: int,
    max_passes: int,
) -> Tuple[int, int]:
    """Edit ``parent`` in-place; return (passes_used, rewires_done)."""
    claimed: set[int] = set()
    rewires = 0
    passes_used = 0

    for _ in range(max_passes):
        passes_used += 1
        cd = _child_degrees(parent)

        leaf_batch: List[int] = []
        for u in range(parent.shape[0]):
            if int(depths[u]) > L - 1:
                continue
            if int(cd[u]) != 0:
                continue
            leaf_batch.append(u)

        leaf_batch.sort(key=lambda z: (int(depths[z]), z))

        progressed = False
        for A in leaf_batch:
            kd_child = int(depths[A]) + 1
            if kd_child > L:
                continue

            B = _query_nearest_unclaimed(
                centers_all[A],
                kd_child,
                kdt_meta,
                claimed,
            )
            if B is None:
                continue

            oldp = int(parent[B])
            newp = A
            claimed.add(B)
            if oldp == newp:
                continue

            parent[B] = np.int32(newp)
            rewires += 1
            progressed = True

        if not progressed:
            break

    return passes_used, rewires


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "data_dir",
        type=Path,
        help="Directory with level01.ply … levelXX.ply (coarse→fine)",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path (default: <data_dir>/lod_tree_nn_patch_leaves.m2lt)",
    )
    ap.add_argument(
        "--disable-patch",
        action="store_true",
        help="Skip stage‑2 refinement (pure nearest-centre LOD tree)",
    )
    ap.add_argument(
        "--max-passes",
        type=int,
        default=512,
        help="Maximum refinement sweep rounds for stage‑2",
    )

    args = ap.parse_args()
    data_dir = args.data_dir.resolve()
    if not data_dir.is_dir():
        print(f"Not a directory: {data_dir}", file=sys.stderr)
        return 2

    level_entries = _discover_level_plys(data_dir)
    centers_by_level: List[np.ndarray] = []
    for lv, pth in level_entries:
        centers_by_level.append(_read_gaussian_centers(pth))
        print(f"level {lv:02d}: {pth.name}  ({centers_by_level[-1].shape[0]} Gaussians)")

    parent, total_nodes = build_parents_nn(centers_by_level)
    counts = [c.shape[0] for c in centers_by_level]
    offsets, _ = _assign_global_ids(counts)
    L = len(centers_by_level)

    if not args.disable_patch:
        depths = _depth_vec(offsets, L, total_nodes)
        centers_all = _stack_centers_per_global_id(
            centers_by_level, offsets.astype(np.int64, copy=False), L, total_nodes
        )
        kdt_meta = _precompute_depth_kdtrees(offsets, centers_all, L)
        pu, nw = refine_parents_extend_nonfinest_leaves(
            parent,
            depths=depths,
            centers_all=centers_all,
            kdt_meta=kdt_meta,
            L=L,
            max_passes=max(1, args.max_passes),
        )
        print(
            f"Stage‑2: passes={pu}, parent rewires={nw}",
            flush=True,
        )

    out_path = args.output
    if out_path is None:
        out_path = data_dir / "lod_tree_nn_patch_leaves.m2lt"
    else:
        out_path = out_path.resolve()

    write_m2lt(out_path, L, offsets, parent)
    print(
        f"Wrote {out_path}  (nodes={total_nodes}, lod_levels={L}, format_version={M2LT_VERSION})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
