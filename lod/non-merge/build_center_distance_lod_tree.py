#!/usr/bin/env python3
"""
Build an .m2lt LOD parent tree from multiple 3D Gaussian Splatting PLY exports.

Convention (directory layout)
-----------------------------
A data directory contains PLY files named like ``level01.ply``, ``level02.ply``, ...
``levelXX`` (two-digit index). **Smaller XX = coarser LOD** (fewer / larger Gaussians).

Tree semantics
---------------
- One node per Gaussian in every PLY, plus one virtual **root** (id 0).
- **Fine → coarse linking**: for each Gaussian in level ``l`` with ``l >= 2``,
  parent is the Gaussian in level ``l-1`` whose **center** (x,y,z) has minimum
  Euclidean distance. Ties break by **smaller coarse-level vertex index**.
- **Level 1**: every Gaussian's parent is the root.
- Resulting graph is an out-tree rooted at 0; several fine Gaussians may share
  the same coarse parent.

Output matches ``tree_file_format.md`` (format version 2, 12-byte node records).

Dependencies: numpy, scipy, plyfile  (see requirements-lod.txt)

Example
-------
  python build_center_distance_lod_tree.py ./my_lod_dir --output tree.m2lt
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
from plyfile import PlyData
from scipy.spatial import cKDTree


M2LT_MAGIC = b"M2LT"
M2LT_VERSION = 2
NODE_RECORD_BYTES = 12
HEADER_BYTES = 64
ROOT_PARENT = 0xFFFFFFFF
UNUSED_U32 = 0xFFFFFFFF


def _read_gaussian_centers(ply_path: Path) -> np.ndarray:
    """Return (N, 3) float32 array of vertex positions from a 3DGS-style PLY."""
    data = PlyData.read(str(ply_path))
    v = data["vertex"]
    x = np.asarray(v["x"], dtype=np.float64)
    y = np.asarray(v["y"], dtype=np.float64)
    z = np.asarray(v["z"], dtype=np.float64)
    if x.shape != y.shape or x.shape != z.shape:
        raise ValueError(f"Inconsistent vertex coordinates in {ply_path}")
    return np.column_stack([x, y, z]).astype(np.float64)


def _discover_level_plys(data_dir: Path) -> List[Tuple[int, Path]]:
    """
    Find ``levelXX.ply`` / ``level_XX.ply`` (case-insensitive), sorted coarse→fine.
    Returns list of (level_index_1based, path).
    """
    pat = re.compile(r"^level_?(\d+)\.ply$", re.IGNORECASE)
    found: List[Tuple[int, Path]] = []
    for p in sorted(data_dir.iterdir()):
        if not p.is_file():
            continue
        m = pat.match(p.name)
        if m:
            found.append((int(m.group(1)), p.resolve()))
    if not found:
        raise FileNotFoundError(
            f"No levelXX.ply files under {data_dir} "
            "(expected names like level01.ply or level_01.ply)"
        )
    found.sort(key=lambda t: t[0])
    # Require contiguous indices 1..L
    levels = [lv for lv, _ in found]
    L = len(found)
    if levels != list(range(1, L + 1)):
        raise ValueError(
            f"Level indices must be contiguous 1..L, got {levels} in {data_dir}"
        )
    return found


def _assign_global_ids(counts: List[int]) -> Tuple[np.ndarray, List[Tuple[int, int]]]:
    """
    counts[k] = number of Gaussians at depth k+1 (level index k+1).
    Returns:
      offsets: shape (L+1,) int32 — for depth d in 1..L, ids are
               offsets[d-1] .. offsets[d]-1; offsets[L] = total non-root nodes.
      ranges: list of (start_id, end_exclusive) per depth 1..L
    """
    L = len(counts)
    offsets = np.zeros(L + 1, dtype=np.int64)
    for i in range(L):
        offsets[i + 1] = offsets[i] + counts[i]
    total = int(offsets[-1])
    ranges = []
    for d in range(L):
        s = int(offsets[d]) + 1  # global id space starts at 1 (after root)
        e = int(offsets[d + 1]) + 1
        ranges.append((s, e))
    return offsets, ranges


def build_parents_nn(
    centers_by_level: List[np.ndarray],
) -> Tuple[np.ndarray, int]:
    """
    centers_by_level[i] = (Ni, 3) for level i+1 (coarsest first).

    Returns parent array of length N = 1 + sum N_i:
      parent[0] unused (root has no parent in-file)
      parent[g] = parent global id for each g in 1..N-1
    """
    L = len(centers_by_level)
    counts = [int(c.shape[0]) for c in centers_by_level]
    offsets, ranges = _assign_global_ids(counts)
    total_nodes = 1 + int(offsets[-1])
    parent = np.zeros(total_nodes, dtype=np.int32)

    # depth 1 → root
    s1, e1 = ranges[0]
    parent[s1:e1] = 0

    # finer levels: nearest neighbor in coarser layer
    for li in range(1, L):
        coarse = centers_by_level[li - 1]
        fine = centers_by_level[li]
        tree = cKDTree(coarse)
        try:
            _, idx = tree.query(fine, k=1, workers=-1)
        except TypeError:
            try:
                _, idx = tree.query(fine, k=1, n_jobs=-1)
            except TypeError:
                _, idx = tree.query(fine, k=1)

        sf, ef = ranges[li]
        parent[sf:ef] = np.int32(idx) + int(offsets[li - 1]) + 1

    return parent, total_nodes


def _build_csr_children(parent: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """parent[g] for g in 1..N-1; build CSR over children lists, sorted."""
    n = parent.shape[0]
    children: List[List[int]] = [[] for _ in range(n)]
    for g in range(1, n):
        p = int(parent[g])
        children[p].append(g)
    for lst in children:
        lst.sort()

    csr_off = np.zeros(n + 1, dtype=np.uint32)
    flat: List[int] = []
    pos = 0
    for i in range(n):
        csr_off[i] = pos
        for c in children[i]:
            flat.append(c)
            pos += 1
    csr_off[n] = pos
    flat_arr = np.array(flat, dtype=np.uint32) if flat else np.zeros(0, dtype=np.uint32)
    return csr_off, flat_arr


def _pack_node_record(parent_id: int, kind: int, depth: int, gauss_index: int) -> bytes:
    return struct.pack(
        "<IBB2xI",
        parent_id & 0xFFFFFFFF,
        kind & 0xFF,
        depth & 0xFF,
        gauss_index & 0xFFFFFFFF,
    )


def write_m2lt(
    out_path: Path,
    lod_levels: int,
    offsets: np.ndarray,
    parent: np.ndarray,
) -> None:
    n = parent.shape[0]
    csr_off, flat_children = _build_csr_children(parent)
    csr_child_count = int(csr_off[-1])

    magic = M2LT_MAGIC
    version = M2LT_VERSION
    reserved0 = 0
    flags = 0
    hdr = struct.pack(
        "<4s8I",
        magic,
        version,
        reserved0,
        lod_levels,
        n,
        csr_child_count,
        NODE_RECORD_BYTES,
        flags,
        HEADER_BYTES,
    )
    assert len(hdr) == 36
    file_header = hdr + b"\x00" * (HEADER_BYTES - len(hdr))
    assert len(file_header) == HEADER_BYTES

    nodes_blob = bytearray()
    # id 0 root
    nodes_blob.extend(_pack_node_record(ROOT_PARENT, 0, 0, UNUSED_U32))
    L = lod_levels
    for depth in range(1, L + 1):
        lo = int(offsets[depth - 1]) + 1
        hi = int(offsets[depth]) + 1
        local = 0
        for gid in range(lo, hi):
            nodes_blob.extend(
                _pack_node_record(int(parent[gid]), 1, depth, local)
            )
            local += 1

    if len(nodes_blob) != n * NODE_RECORD_BYTES:
        raise RuntimeError("internal: wrong nodes blob size")

    with out_path.open("wb") as f:
        f.write(file_header)
        f.write(nodes_blob)
        f.write(csr_off.tobytes())
        f.write(flat_children.tobytes())


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
        help="Output path (default: <data_dir>/lod_tree.m2lt)",
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

    out_path = args.output
    if out_path is None:
        out_path = data_dir / "lod_tree.m2lt"
    else:
        out_path = out_path.resolve()

    write_m2lt(out_path, L, offsets, parent)
    print(f"Wrote {out_path}  (nodes={total_nodes}, lod_levels={L}, format_version={M2LT_VERSION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
