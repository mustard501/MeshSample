#!/usr/bin/env python3
"""
Inspect a Mesh2Splat `.m2lt` LOD tree (format version 2, see `tree_file_format.md`).

Reads the CSR child lists and performs a depth-first traversal from the root (id 0),
printing an indented outline. Also reports how many nodes sit at each semantic `depth`
(0 = root, 1 = coarsest PLY, …, L = finest).

Also prints, for **each semantic depth**, how many nodes are **leaves** in the CSR
sense (have **zero** children edges), i.e. no finer LOD attaches below.

With ``--ply-dir``, loads ``level##_?.ply`` (same naming as LOD builders), reads vertex
``scale_0..2`` like Mesh2Splat/Splat (semi-axis lengths ``exp(scale_i)`` per axis),
then for **each semantic depth** ``depth = d`` among Gaussian tree nodes computes the
**minimum and maximum** of ``max(exp(s₀),exp(s₁),exp(s₂))`` at that depth and prints a
table after the DFS tree output; in ``--summary-only`` mode, after the leaf-count block.

Uses ``plyfile`` when ``--ply-dir`` is set (`pip install plyfile`).

Large trees: use `--limit` to cap printed tree lines only (summary counts are complete).

Dependencies: Python standard library; optional ``plyfile`` for ``--ply-dir``.

Example
-------
  python inspect_m2lt_tree.py ./out/lod_tree.m2lt
  python inspect_m2lt_tree.py ./out/lod_tree.m2lt --ply-dir ./exported_lod_plys
  python inspect_m2lt_tree.py ./out/lod_tree.m2lt --summary-only --ply-dir ./exported_lod_plys
"""

from __future__ import annotations

import argparse
import math
import re
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from plyfile import PlyData
except ImportError:
    PlyData = None  # type: ignore[misc, assignment]


ROOT_PARENT = 0xFFFFFFFF
EXPECTED_MAGIC = b"M2LT"
EXPECTED_NODE_RECORD = 12


def _parse_header(blob: bytes) -> Tuple[int, ...]:
    if len(blob) < 64:
        raise ValueError("file too small for 64-byte header")
    head36 = blob[:36]
    (
        magic,
        version,
        reserved0,
        lod_levels,
        total_nodes,
        csr_child_count,
        node_record_bytes,
        flags,
        header_bytes,
    ) = struct.unpack("<4s8I", head36)
    if magic != EXPECTED_MAGIC:
        raise ValueError(f"bad magic {magic!r}, expected {EXPECTED_MAGIC!r}")
    if header_bytes != 64:
        raise ValueError(f"unsupported header_bytes={header_bytes}")
    if node_record_bytes != EXPECTED_NODE_RECORD:
        raise ValueError(
            f"node_record_bytes={node_record_bytes} expected {EXPECTED_NODE_RECORD} "
            f"(version/layout mismatch)"
        )
    return (
        int(version),
        int(reserved0),
        int(lod_levels),
        int(total_nodes),
        int(csr_child_count),
        int(node_record_bytes),
        int(flags),
    )


def _parse_nodes(nodes_blob: bytes, n: int) -> Tuple[List[int], List[int], List[int], List[int]]:
    """Returns parents, kinds, depths, gauss_indices per global id."""
    exp = n * EXPECTED_NODE_RECORD
    if len(nodes_blob) != exp:
        raise ValueError(f"nodes section length {len(nodes_blob)} expected {exp}")
    parents: List[int] = []
    kinds: List[int] = []
    depths: List[int] = []
    gauss_idx: List[int] = []
    off = 0
    for _ in range(n):
        pid, kind, depth, gix = struct.unpack_from("<IBB2xI", nodes_blob, off)
        off += EXPECTED_NODE_RECORD
        parents.append(pid)
        kinds.append(kind)
        depths.append(depth)
        gauss_idx.append(gix)
    return parents, kinds, depths, gauss_idx


def _parse_csr(
    data: bytes, base: int, n: int, expected_flat: int
) -> Tuple[List[int], List[int]]:
    need_off = 4 * (n + 1)
    if base + need_off > len(data):
        raise ValueError("truncated csr_off section")
    csr_off = list(struct.unpack(f"<{n + 1}I", data[base : base + need_off]))
    flat_n = csr_off[-1]
    if flat_n != expected_flat:
        raise ValueError(
            f"csr_off sentinel {flat_n} != header csr_child_count {expected_flat}"
        )
    flat_start = base + need_off
    if flat_start + 4 * flat_n > len(data):
        raise ValueError("truncated flat_children section")
    if flat_n == 0:
        flat: List[int] = []
    else:
        flat = list(struct.unpack(f"<{flat_n}I", data[flat_start : flat_start + 4 * flat_n]))
    trailing = flat_start + 4 * flat_n
    if trailing != len(data):
        raise ValueError(f"unexpected {len(data) - trailing} trailing bytes after flat_children")
    return csr_off, flat


def _children_from_csr(csr_off: Sequence[int], flat: Sequence[int], u: int) -> List[int]:
    a = csr_off[u]
    b = csr_off[u + 1]
    return list(flat[a:b])


def _discover_level_plys(data_dir: Path) -> List[Tuple[int, Path]]:
    """``level##_?.ply`` sorted coarse→fine; levels must be 1..L contiguous."""
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
            f"No levelXX.ply under {data_dir} (e.g. level01.ply, level_01.ply)"
        )
    found.sort(key=lambda t: t[0])
    levels = [lv for lv, _ in found]
    L = len(found)
    if levels != list(range(1, L + 1)):
        raise ValueError(f"PLY level indices must be 1..L contiguous, got {levels}")
    return found


def _load_max_semiaxis_exp_by_depth(data_dir: Path, expected_L: int) -> Dict[int, List[float]]:
    """
    For each LOD depth d in 1..L (``level##_?.ply``, row order = gauss_index),
    ``list[row] = max(exp(scale₀), exp(scale₁), exp(scale₂))`` (Mesh2Splat PLY convention).
    """
    if PlyData is None:
        raise RuntimeError("`plyfile` is required when using --ply-dir (pip install plyfile)")
    entries = _discover_level_plys(data_dir)
    if len(entries) != expected_L:
        raise ValueError(
            f"Ply dir has {len(entries)} levels but .m2lt declares lod_levels L={expected_L}"
        )

    scales_by_depth: Dict[int, List[float]] = {}
    for d, ply_path in entries:
        data = PlyData.read(str(ply_path))
        v = data["vertex"]
        for name in ("scale_0", "scale_1", "scale_2"):
            if name not in v:
                raise KeyError(f"{ply_path}: missing vertex property `{name}`")
        s0 = v["scale_0"]
        s1 = v["scale_1"]
        s2 = v["scale_2"]
        nvert = len(s0)
        row_max: List[float] = []
        for i in range(nvert):
            e0 = math.exp(float(s0[i]))
            e1 = math.exp(float(s1[i]))
            e2 = math.exp(float(s2[i]))
            row_max.append(max(e0, e1, e2))
        scales_by_depth[d] = row_max
    return scales_by_depth


def _per_depth_min_max_scale_among_gaussian_nodes(
    n: int,
    kinds: Sequence[int],
    depths: Sequence[int],
    gauss_idx: Sequence[int],
    scale_table: Dict[int, List[float]],
) -> Dict[int, Tuple[Tuple[float, int, int], Tuple[float, int, int]]]:
    """
    For each semantic depth ``d`` with at least one Gaussian (kind==1) node:

    Track ``metric = maxᵢ exp(scale_i)`` per node from ``scale_table``.

    Returns ``depth -> (min_example, max_example)`` where each side is
    ``(metric, global_node_id, gauss_index)``. Min/max examples use strict `<` / `>` and
    keep the first global id across ties (ascending scan order).
    """
    mins: Dict[int, Tuple[float, int, int]] = {}
    maxs: Dict[int, Tuple[float, int, int]] = {}
    for nid in range(n):
        if kinds[nid] != 1:
            continue
        d = depths[nid]
        g = gauss_idx[nid]
        lst = scale_table.get(d)
        if lst is None or not (0 <= g < len(lst)):
            continue
        v = lst[g]
        if d not in mins or v < mins[d][0]:
            mins[d] = (v, nid, g)
        if d not in maxs or v > maxs[d][0]:
            maxs[d] = (v, nid, g)

    out: Dict[int, Tuple[Tuple[float, int, int], Tuple[float, int, int]]] = {}
    for d in mins:
        out[d] = (mins[d], maxs[d])
    return out


def _print_per_depth_scale_table(
    per_depth: Dict[int, Tuple[Tuple[float, int, int], Tuple[float, int, int]]],
    lod_levels: int,
) -> None:
    print()
    print("--- PLY min / max semi-axis by depth (tree Gaussian nodes; --ply-dir) ---")
    print("metric: maxᵢ exp(scale_i) per Gaussian; PLY row = gauss_index (Mesh2Splat)")
    if not per_depth:
        print("  (no Gaussian scales collected)")
        return

    glob_min = float("inf")
    glob_max = float("-inf")

    for d in range(1, lod_levels + 1):
        if d not in per_depth:
            print(f"  depth {d}: (no Gaussian nodes)")
            continue
        min_v, min_nid, min_g = per_depth[d][0]
        max_v, max_nid, max_g = per_depth[d][1]

        print(
            f"  depth {d}:  min max_i exp(scale_i) = {min_v:.10g}  "
            f"(example node id {min_nid}, gauss_index {min_g})"
        )
        print(
            f"            max max_i exp(scale_i) = {max_v:.10g}  "
            f"(example node id {max_nid}, gauss_index {max_g})"
        )
        if min_v < glob_min:
            glob_min = min_v
        if max_v > glob_max:
            glob_max = max_v

    if glob_min != float("inf"):
        print(f"  overall min across listed depths: {glob_min:.10g}")
    if glob_max != float("-inf"):
        print(f"  overall max across listed depths: {glob_max:.10g}")


def _validate_parents(
    parents: Sequence[int],
    kinds: Sequence[int],
    depths: Sequence[int],
    n: int,
) -> None:
    if parents[0] != ROOT_PARENT or kinds[0] != 0:
        raise ValueError("root must have parent 0xFFFFFFFF and kind 0")
    for i in range(1, n):
        p = parents[i]
        if p >= n:
            raise ValueError(f"node {i} invalid parent_id {p}")
        if p == 0:
            if depths[i] != 1:
                raise ValueError(f"node {i} must have depth 1 when parent is root")
        elif depths[i] != depths[p] + 1:
            raise ValueError(
                f"node {i} depth={depths[i]} != parent {p} depth {depths[p]} + 1"
            )


def _print_depth_counts(depths: Sequence[int]) -> None:
    ctr = Counter(depths)
    max_d = max(ctr) if ctr else 0
    print("--- nodes per depth (semantic layer, depth 0 = root) ---")
    for d in range(max_d + 1):
        print(f"  depth {d}: {ctr[d]}")
    print(f"  total:   {sum(ctr.values())}")


def _leaf_counts_by_depth(
    n: int,
    csr_off: Sequence[int],
    depths: Sequence[int],
) -> Dict[int, int]:
    """
    Count nodes with no CSR children (``csr_off[u+1] == csr_off[u]``), grouped by
    that node's ``depth`` field.
    """
    ctr: Dict[int, int] = {}
    for u in range(n):
        if csr_off[u + 1] != csr_off[u]:
            continue
        d = depths[u]
        ctr[d] = ctr.get(d, 0) + 1
    return ctr


def _print_leaf_counts_by_depth(leaf_ct: Dict[int, int], lod_levels: int) -> None:
    print()
    print("--- Leaf nodes per depth (no CSR children; tree topology) ---")
    for d in range(0, lod_levels + 1):
        print(f"  depth {d}: {leaf_ct.get(d, 0)}")
    print(f"  total leaves: {sum(leaf_ct.values())}")


def _dfs_print(
    csr_off: Sequence[int],
    flat: Sequence[int],
    depths: Sequence[int],
    gauss_idx: Sequence[int],
    kinds: Sequence[int],
    limit: int,
) -> int:
    """Returns number of lines printed."""
    lines = 0

    def emit(s: str) -> None:
        nonlocal lines
        if lines >= limit:
            return
        print(s)
        lines += 1

    def dfs(u: int, prefix: str, is_last: bool) -> None:
        if lines >= limit:
            return
        connector = "`-- " if is_last else "+-- "
        k = kinds[u]
        g = gauss_idx[u]
        if k == 0:
            label = "root"
        else:
            label = f"gauss[{g}]"
        emit(f"{prefix}{connector}id={u} depth={depths[u]} {label}")

        child_prefix = prefix + ("    " if is_last else "|   ")
        ch = _children_from_csr(csr_off, flat, u)
        if not ch:
            return
        for i, c in enumerate(ch):
            last = i == len(ch) - 1
            dfs(c, child_prefix, last)

    # Root is printed without a connector branch from a parent
    if lines < limit:
        k0 = kinds[0]
        emit(f"id=0 depth={depths[0]} {'root' if k0 == 0 else '???'}")
    ch0 = _children_from_csr(csr_off, flat, 0)
    for i, c in enumerate(ch0):
        if lines >= limit:
            break
        dfs(c, "", i == len(ch0) - 1)

    return lines


def inspect_file(
    path: Path,
    *,
    limit: int,
    summary_only: bool,
    validate: bool,
    ply_dir: Optional[Path],
) -> int:
    data = path.read_bytes()
    (
        version,
        _reserved0,
        lod_levels,
        total_nodes,
        csr_child_count,
        _nbytes,
        _flags,
    ) = _parse_header(data)

    n = total_nodes
    nodes_blob = data[64 : 64 + n * EXPECTED_NODE_RECORD]
    csr_off, flat = _parse_csr(
        data, 64 + n * EXPECTED_NODE_RECORD, n, csr_child_count
    )

    parents, kinds, depths, gauss_idx = _parse_nodes(nodes_blob, n)

    print(f"file:          {path}")
    print(f"format version {version}")
    print(f"lod_levels L = {lod_levels}")
    print(f"total_nodes N = {n}")
    print(f"csr_child_count = {csr_child_count}")
    print()

    _print_depth_counts(depths)

    if validate:
        try:
            _validate_parents(parents, kinds, depths, n)
            print("parent/depth consistency check: OK")
        except ValueError as e:
            print(f"parent/depth consistency check: FAILED ({e})", file=sys.stderr)

    # CSR vs parent_id: O(edges)
    for p in range(n):
        for c in _children_from_csr(csr_off, flat, p):
            if parents[c] != p:
                print(
                    f"warning: CSR has edge parent {p} -> child {c}, "
                    f"but parent[{c}]={parents[c]}",
                    file=sys.stderr,
                )

    leaf_by_depth = _leaf_counts_by_depth(n, csr_off, depths)
    _print_leaf_counts_by_depth(leaf_by_depth, lod_levels)

    depth_scale_stats: Optional[
        Dict[int, Tuple[Tuple[float, int, int], Tuple[float, int, int]]]
    ] = None
    if ply_dir is not None:
        try:
            st = _load_max_semiaxis_exp_by_depth(ply_dir, lod_levels)
            depth_scale_stats = _per_depth_min_max_scale_among_gaussian_nodes(
                n, kinds, depths, gauss_idx, st
            )
        except Exception as e:
            print(f"PLY scale scan failed ({e})", file=sys.stderr)

    if summary_only:
        if depth_scale_stats:
            _print_per_depth_scale_table(depth_scale_stats, lod_levels)
        return 0

    print()
    print(f"--- DFS tree (pre-order, max {limit} lines) ---")
    printed = _dfs_print(csr_off, flat, depths, gauss_idx, kinds, limit)
    if printed >= limit:
        print(f"... output truncated at --limit {limit}", file=sys.stderr)

    if depth_scale_stats:
        _print_per_depth_scale_table(depth_scale_stats, lod_levels)

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("m2lt_path", type=Path, help="Path to .m2lt file")
    ap.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Max lines of DFS tree output (default: 2000)",
    )
    ap.add_argument(
        "--summary-only",
        action="store_true",
        help="Only print header and per-depth counts",
    )
    ap.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip strict parent/depth validation",
    )
    ap.add_argument(
        "--ply-dir",
        type=Path,
        default=None,
        help="Directory with level*.ply; used for per-depth min/max scale stats (--ply-dir)",
    )
    args = ap.parse_args()
    p = args.m2lt_path.resolve()
    if not p.is_file():
        print(f"not a file: {p}", file=sys.stderr)
        return 2
    ply = args.ply_dir.resolve() if args.ply_dir else None
    if ply is not None and not ply.is_dir():
        print(f"--ply-dir is not a directory: {ply}", file=sys.stderr)
        return 2
    return inspect_file(
        p,
        limit=max(1, args.limit),
        summary_only=args.summary_only,
        validate=not args.no_validate,
        ply_dir=ply,
    )


if __name__ == "__main__":
    raise SystemExit(main())
