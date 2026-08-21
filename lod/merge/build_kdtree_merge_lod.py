#!/usr/bin/env python3
"""
Build KD-tree merge LOD from a single fine BRDF Gaussian PLY.

Pipeline
--------
1. Read input PLY (N Gaussians).
2. Build KD-tree by recursive median split; pad shallow leaves with unary chains
   so all original Gaussians sit at the same max depth L.
3. Post-order merge (``gaussian_merge.merge_gaussians``).
4. Export ``level00.ply`` … ``level0L.ply`` and ``.m2lt`` (format v3).

PLY row order at depth ``l``: ``gauss_index`` 0 … N_l−1 (see ``tree_file_format.md``).
Finest layer ``level0L.ply`` matches input vertex order.

Example
-------
  python build_kdtree_merge_lod.py input.ply -o ./out_dir
  python build_kdtree_merge_lod.py input.ply -o ./out_dir --tree ./out_dir/lod_tree.m2lt

Dependencies: numpy, scipy, plyfile, tqdm — see ``lod/requirements-lod.txt``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_MERGE_DIR = Path(__file__).resolve().parent
if str(_MERGE_DIR) not in sys.path:
    sys.path.insert(0, str(_MERGE_DIR))

from gaussian_ply import level_ply_name, read_brdf_ply, write_brdf_ply  # noqa: E402
from kdtree_build import build_merge_tree  # noqa: E402
from m2lt_write import write_merge_m2lt  # noqa: E402


def export_level_plys(
    out_dir: Path,
    buckets: list[list],
    *,
    progress: bool = False,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for depth, depth_nodes in enumerate(buckets):
        gaussians = [n.gaussian for n in depth_nodes]
        if any(g is None for g in gaussians):
            raise RuntimeError(f"depth {depth}: missing Gaussian before export")
        write_brdf_ply(
            out_dir / level_ply_name(depth),
            gaussians,
            progress=progress,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input_ply", type=Path, help="Finest BRDF Gaussian PLY")
    ap.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for level00.ply … level0L.ply",
    )
    ap.add_argument(
        "--tree",
        type=Path,
        default=None,
        help="Output .m2lt path (default: <output-dir>/lod_tree.m2lt)",
    )
    ap.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable tqdm progress bars",
    )
    args = ap.parse_args()

    input_ply = args.input_ply.resolve()
    if not input_ply.is_file():
        print(f"Not a file: {input_ply}", file=sys.stderr)
        return 2

    out_dir = args.output_dir.resolve()
    tree_path = args.tree.resolve() if args.tree else out_dir / "lod_tree.m2lt"
    show_progress = not args.no_progress

    source = read_brdf_ply(input_ply, progress=show_progress)
    n = len(source)
    if n == 0:
        print("Input PLY has no vertices.", file=sys.stderr)
        return 2

    print(f"Input: {input_ply.name}  ({n} Gaussians)")
    root, max_depth, buckets = build_merge_tree(source, progress=show_progress)

    export_level_plys(out_dir, buckets, progress=show_progress)
    write_merge_m2lt(tree_path, root, max_depth, buckets)

    for d, depth_nodes in enumerate(buckets):
        print(f"  level{d:02d}.ply: {len(depth_nodes)} Gaussians")

    print(
        f"Wrote {out_dir}  ({max_depth + 1} level PLYs), "
        f"tree {tree_path}  (nodes={sum(len(b) for b in buckets)}, L={max_depth})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
