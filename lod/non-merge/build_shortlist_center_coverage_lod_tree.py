#!/usr/bin/env python3
"""
Build an .m2lt LOD tree: **kNN shortlist by centre distance**, then **Coverage score**.

For each fine Gaussian (level ``l ≥ 2``):

1. Take the ``x`` **nearest coarse centres** (``cKDTree``), same as
   ``build_shortlist_center_aabb_lod_tree.py``.
2. For each candidate coarse parent ``p`` and fixed fine child ``c``,
   build covariances (same convention as Mesh2Splat PLY)::

       Σ = R · diag(σ₁², σ₂², σ₃²) · Rᵀ,   σᵢ = exp(scale_i)

   and compute (as requested, in **log space** for stability)::

       log Coverage = ½ log det(Σ_p) − ½ log det(Σ_c + Σ_p)
                     − ½ (μ_c − μ_p)ᵀ (Σ_c + Σ_p)⁻¹ (μ_c − μ_p)

   Parent = argmax Coverage among the shortlist.

3. If all ``log Coverage`` are non-finite, fall back to **nearest centre**
   (first kNN index).

**Note on semantics:** The closed-form **integral** ∫ 𝒩(x|μ_c,Σ_c) 𝒩(x|μ_p,Σ_p) dx
for *normalized* 3D Gaussians has a slightly different monomial in ``det(Σ_c)`` and
``det(Σ_p)`` in the prefactor. Your expression is implemented **exactly as given**
so rankings match your definition; if you later want the standard product integral,
only the **additive** log-prefactors (depending on ``Σ_c``, ``Σ_p``) would change,
and with **fixed** ``c`` some terms are constant across ``p`` — see any note on
multivariate Gaussian products.

Covariances are **full 3×3**; ``Σ_c + Σ_p`` gets a tiny diagonal jitter for
Cholesky / slogdet stability.

Output: ``tree_file_format.md`` v2 (same as other lod builders).

Dependencies: numpy, scipy, plyfile, optional tqdm — see ``requirements-lod.txt``.

Example
-------
  python build_shortlist_center_coverage_lod_tree.py ./data -o out.m2lt --shortlist-x 64
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
from plyfile import PlyData
from scipy.spatial import cKDTree
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
    M2LT_VERSION,
)


def _read_gaussian_covariances(ply_path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """
    Return centre (N,3) and covariance Σ (N,3,3), float64.
    Σ = R diag(σ²) Rᵀ with σᵢ = exp(scale_i).
    """
    data = PlyData.read(str(ply_path))
    v = data["vertex"]
    need = (
        "x", "y", "z",
        "scale_0", "scale_1", "scale_2",
        "rot_0", "rot_1", "rot_2", "rot_3",
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
    sig = np.column_stack([np.exp(ls0), np.exp(ls1), np.exp(ls2)])

    q0 = np.asarray(v["rot_0"], dtype=np.float64)
    q1 = np.asarray(v["rot_1"], dtype=np.float64)
    q2 = np.asarray(v["rot_2"], dtype=np.float64)
    q3 = np.asarray(v["rot_3"], dtype=np.float64)
    quat_wxyz = np.column_stack([q0, q1, q2, q3])
    norms = np.linalg.norm(quat_wxyz, axis=1)
    if np.any(norms < 1e-12):
        raise ValueError(f"{ply_path}: degenerate quaternion row(s)")
    quat_wxyz = quat_wxyz / norms[:, np.newaxis]
    quat_xyzw = np.column_stack(
        [quat_wxyz[:, 1], quat_wxyz[:, 2], quat_wxyz[:, 3], quat_wxyz[:, 0]]
    )
    rmat = Rotation.from_quat(quat_xyzw).as_matrix()
    # Σ = R @ diag(sig**2) @ R.T = (R * sig**2[:,newaxis,:]) @ R.T
    s2 = sig**2
    Sigma = np.matmul(rmat * s2[:, np.newaxis, :], np.swapaxes(rmat, -1, -2))

    return centre, Sigma


def _kd_query_knn(
    tree: cKDTree,
    points: np.ndarray,
    k: int,
) -> Tuple[np.ndarray, np.ndarray]:
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


def _log_coverage_pairs_batched(
    sigma_f: np.ndarray,
    centre_f: np.ndarray,
    sigma_c_all: np.ndarray,
    centre_c_all: np.ndarray,
    cand_idx: np.ndarray,
    jitter: float,
) -> np.ndarray:
    """
    sigma_f: (B, 3, 3) fine covariances in this chunk
    centre_f: (B, 3)
    sigma_c_all: (Nc, 3, 3) full coarse
    centre_c_all: (Nc, 3)
    cand_idx: (B, K) coarse indices

    Returns logCoverage (B, K).
    """
    b, n_k = cand_idx.shape
    sig_p = sigma_c_all[cand_idx]  # (B, K, 3, 3)
    s_sum = sigma_f[:, np.newaxis, :, :] + sig_p

    id3 = np.eye(3, dtype=np.float64) * jitter
    s_sum = s_sum + id3

    mu_f = centre_f[:, np.newaxis, :]
    mu_p = centre_c_all[cand_idx]
    delta = mu_f - mu_p

    # slogdet per (B,K) matrix
    bk = b * n_k
    s_flat = s_sum.reshape(bk, 3, 3)
    sig_p_flat = sig_p.reshape(bk, 3, 3)

    sign_s, lds = np.linalg.slogdet(s_flat)
    sign_p, ldp = np.linalg.slogdet(sig_p_flat)
    if np.any(sign_s <= 0) or np.any(sign_p <= 0):
        # numerical — try stronger jitter once in caller; here mask bad
        lds = np.where(sign_s > 0, lds, -np.inf)
        ldp = np.where(sign_p > 0, ldp, -np.inf)

    d_flat = delta.reshape(bk, 3)
    # solve S w = d  =>  quadratic = d.T w
    try:
        w = np.linalg.solve(s_flat, d_flat[..., np.newaxis])[..., 0]
    except np.linalg.LinAlgError:
        w = np.linalg.lstsq(s_flat, d_flat, rcond=None)[0]

    quad = np.sum(d_flat * w, axis=1)
    logcov = 0.5 * (ldp - lds) - 0.5 * quad
    logcov = np.nan_to_num(logcov, nan=-1e300, neginf=-1e300, posinf=-1e300)
    return logcov.reshape(b, n_k)


def _parents_layer_shortlist_coverage(
    coarse_centre: np.ndarray,
    sigma_coarse: np.ndarray,
    fine_centre: np.ndarray,
    sigma_fine: np.ndarray,
    *,
    shortlist_x: int,
    chunk: int,
    progress: bool,
    progress_desc: str,
    jitter: float,
) -> np.ndarray:
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
            sf = sigma_fine[start:end]

            _, ii = _kd_query_knn(tree, fc, k)
            logc = _log_coverage_pairs_batched(
                sf,
                fc,
                sigma_coarse,
                coarse_centre,
                ii,
                jitter=jitter,
            )
            pick_rel = np.argmax(logc, axis=1)
            best_log = logc[np.arange(logc.shape[0]), pick_rel]
            fallback = ii[:, 0].copy()
            chosen = ii[np.arange(ii.shape[0]), pick_rel]
            bad = ~np.isfinite(best_log)
            chosen = np.where(bad, fallback, chosen)
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


def build_parents_shortlist_coverage(
    geom_by_level: List[Tuple[np.ndarray, np.ndarray]],
    *,
    shortlist_x: int,
    chunk: int,
    progress: bool,
    jitter: float,
) -> Tuple[np.ndarray, int]:
    L = len(geom_by_level)
    counts = [int(g[0].shape[0]) for g in geom_by_level]
    offsets, ranges = _assign_global_ids(counts)
    total_nodes = 1 + int(offsets[-1])
    parent = np.zeros(total_nodes, dtype=np.int32)

    s1, e1 = ranges[0]
    parent[s1:e1] = 0

    for li in range(1, L):
        cc, Sc = geom_by_level[li - 1]
        fc, Sf = geom_by_level[li]
        coarse_lv = li
        fine_lv = li + 1
        n_f = int(fc.shape[0])
        n_c = int(cc.shape[0])
        kd_k = max(1, min(int(shortlist_x), n_c))
        desc = (
            f"kNN+Coverage parents  fine L{fine_lv:02d} ({n_f}) "
            f"← coarse L{coarse_lv:02d} ({n_c})  shortlist={kd_k}"
        )
        loc_idx = _parents_layer_shortlist_coverage(
            cc,
            Sc,
            fc,
            Sf,
            shortlist_x=shortlist_x,
            chunk=chunk,
            progress=progress,
            progress_desc=desc,
            jitter=jitter,
        )
        rs, re = ranges[li]
        parent[rs:re] = np.int32(loc_idx) + int(offsets[li - 1]) + 1

    return parent, total_nodes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir", type=Path, help="Directory with level*.ply coarse→fine")
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .m2lt (default: <data_dir>/lod_tree_shortlist_coverage.m2lt)",
    )
    ap.add_argument("--shortlist-x", type=int, default=32, help="kNN shortlist size")
    ap.add_argument("--chunk", type=int, default=4096, help="Fine batch size")
    ap.add_argument(
        "--jitter",
        type=float,
        default=1e-9,
        help="Add jitter * I to Σ_c+Σ_p for stability (default: 1e-9)",
    )
    ap.add_argument("--no-progress", action="store_true")
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
    geom_by_level: List[Tuple[np.ndarray, np.ndarray]] = []
    for lv, pth in level_entries:
        c, S = _read_gaussian_covariances(pth)
        geom_by_level.append((c, S))
        print(
            f"level {lv:02d}: {pth.name}  ({c.shape[0]} Gaussians, Σ from scales+rot)",
            flush=True,
        )

    parent, total_nodes = build_parents_shortlist_coverage(
        geom_by_level,
        shortlist_x=args.shortlist_x,
        chunk=chunk,
        progress=not args.no_progress,
        jitter=max(0.0, float(args.jitter)),
    )
    counts = [int(g[0].shape[0]) for g in geom_by_level]
    offsets, _ = _assign_global_ids(counts)
    L = len(geom_by_level)

    out_path = (
        args.output.resolve()
        if args.output is not None
        else (data_dir / "lod_tree_shortlist_coverage.m2lt").resolve()
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
