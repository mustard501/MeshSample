#!/usr/bin/env python3
"""Summarize PLY schema and per-field value distributions for meshsample / 3DGS exports.

Examples:
  python debug/stats_ply_fields.py output/sacharinum/level_06.ply
  python debug/stats_ply_fields.py path/to/file.ply --output debug/level_06_ply_stats.md
  python debug/stats_ply_fields.py huge.ply --sample 100000
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    from plyfile import PlyData
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: pip install numpy plyfile\n"
        f"Original error: {exc}"
    ) from exc


# Known meshsample / downstream schemas (property-set heuristics).
KNOWN_SCHEMAS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "BRDF (meshsample format 3)",
        frozenset(
            {
                "base_r",
                "ior",
                "specular_factor",
                "specular_color_r",
                "metallic",
                "roughness",
            }
        ),
    ),
    (
        "Standard 3DGS",
        frozenset({"f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "rot_0"}),
    ),
    (
        "PBR PLY (meshsample format 1)",
        frozenset({"metallicFactor", "roughnessFactor", "f_dc_0"}),
    ),
    (
        "Compressed PBR (meshsample format 2)",
        frozenset({"red", "green", "blue", "octa_nx", "metallic"}),
    ),
)


@dataclass
class PlyHeaderInfo:
    path: Path
    format: str
    vertex_count: int
    properties: list[tuple[str, str]]  # (ply_type, name)
    other_elements: list[tuple[str, int]]


@dataclass
class FieldStats:
    name: str
    ply_type: str
    count: int
    finite_count: int
    nan_count: int
    pos_inf_count: int
    neg_inf_count: int
    min_val: float | None
    max_val: float | None
    mean: float | None
    std: float | None
    p01: float | None
    p50: float | None
    p99: float | None
    unique_count: int | None
    top_values: list[tuple[float, int]] | None


def parse_ply_header(path: Path) -> PlyHeaderInfo:
    properties: list[tuple[str, str]] = []
    other_elements: list[tuple[str, int]] = []
    fmt = ""
    vertex_count = 0
    current_element: str | None = None

    with path.open("rb") as f:
        first = f.readline()
        if first.strip() != b"ply":
            raise ValueError(f"Not a PLY file: {path}")

        while True:
            line = f.readline()
            if not line:
                raise ValueError("Unexpected EOF while reading PLY header")
            text = line.decode("ascii", errors="replace").strip()
            if text == "end_header":
                break

            parts = text.split()
            if not parts:
                continue
            keyword = parts[0]
            if keyword == "format":
                fmt = parts[1] if len(parts) > 1 else ""
            elif keyword == "element":
                current_element = parts[1]
                count = int(parts[2]) if len(parts) > 2 else 0
                if current_element == "vertex":
                    vertex_count = count
                else:
                    other_elements.append((current_element, count))
            elif keyword == "property" and current_element == "vertex":
                if len(parts) >= 3:
                    properties.append((parts[1], parts[2]))

    if not properties:
        raise ValueError(f"No vertex properties found in {path}")

    return PlyHeaderInfo(
        path=path,
        format=fmt,
        vertex_count=vertex_count,
        properties=properties,
        other_elements=other_elements,
    )


def classify_schema(property_names: list[str]) -> list[str]:
    names = set(property_names)
    matches: list[str] = []
    for label, required in KNOWN_SCHEMAS:
        if required.issubset(names):
            matches.append(label)
    if not matches:
        return ["(unrecognized custom schema)"]
    return matches


def load_vertex_arrays(path: Path, sample: int | None) -> tuple[PlyHeaderInfo, dict[str, np.ndarray]]:
    header = parse_ply_header(path)
    ply = PlyData.read(str(path))
    if "vertex" not in ply:
        raise ValueError(f"No 'vertex' element in {path}")

    vertex = ply["vertex"].data
    names = [name for _, name in header.properties]

    arrays: dict[str, np.ndarray] = {}
    n_total = len(vertex)
    if sample is not None and n_total > sample:
        rng = np.random.default_rng(0)
        idx = rng.choice(n_total, size=sample, replace=False)
        idx.sort()
        subset = vertex[idx]
    else:
        subset = vertex

    for name in names:
        if name not in subset.dtype.names:
            raise ValueError(f"Property '{name}' missing from loaded vertex data")
        raw = subset[name]
        if np.issubdtype(raw.dtype, np.floating):
            arrays[name] = raw.astype(np.float64, copy=False)
        elif np.issubdtype(raw.dtype, np.integer):
            arrays[name] = raw.astype(np.float64, copy=False)
        elif raw.dtype.kind == "u" and raw.dtype.itemsize == 1:
            arrays[name] = raw.astype(np.float64, copy=False)
        else:
            # Skip non-numeric (shouldn't occur in meshsample exports)
            continue

    if sample is not None and n_total > sample:
        header.vertex_count = n_total  # keep original count in report

    return header, arrays


def compute_field_stats(
    name: str,
    ply_type: str,
    values: np.ndarray,
    *,
    max_unique: int,
) -> FieldStats:
    flat = np.asarray(values).reshape(-1)
    count = flat.size
    nan_count = int(np.isnan(flat).sum())
    pos_inf = int(np.isposinf(flat).sum())
    neg_inf = int(np.isneginf(flat).sum())
    finite = flat[np.isfinite(flat)]
    finite_count = int(finite.size)

    if finite_count == 0:
        return FieldStats(
            name=name,
            ply_type=ply_type,
            count=count,
            finite_count=0,
            nan_count=nan_count,
            pos_inf_count=pos_inf,
            neg_inf_count=neg_inf,
            min_val=None,
            max_val=None,
            mean=None,
            std=None,
            p01=None,
            p50=None,
            p99=None,
            unique_count=0,
            top_values=[],
        )

    min_val = float(np.min(finite))
    max_val = float(np.max(finite))
    mean = float(np.mean(finite))
    std = float(np.std(finite)) if finite_count > 1 else 0.0
    p01, p50, p99 = (float(x) for x in np.percentile(finite, [1, 50, 99]))

    unique_count: int | None
    top_values: list[tuple[float, int]] | None
    if finite_count <= max_unique:
        uniq, counts = np.unique(finite, return_counts=True)
        order = np.argsort(-counts)
        unique_count = int(uniq.size)
        top_values = [(float(uniq[i]), int(counts[i])) for i in order[:8]]
    else:
        unique_count = None
        top_values = None

    return FieldStats(
        name=name,
        ply_type=ply_type,
        count=count,
        finite_count=finite_count,
        nan_count=nan_count,
        pos_inf_count=pos_inf,
        neg_inf_count=neg_inf,
        min_val=min_val,
        max_val=max_val,
        mean=mean,
        std=std,
        p01=p01,
        p50=p50,
        p99=p99,
        unique_count=unique_count,
        top_values=top_values,
    )


def fmt_float(v: float | None, digits: int = 6) -> str:
    if v is None:
        return "—"
    if not math.isfinite(v):
        return str(v)
    if abs(v) >= 1e4 or (abs(v) > 0 and abs(v) < 1e-4):
        return f"{v:.6g}"
    return f"{v:.{digits}f}".rstrip("0").rstrip(".") if digits else str(v)


def is_constant(stats: FieldStats, tol: float = 1e-6) -> bool:
    if stats.finite_count == 0 or stats.min_val is None or stats.max_val is None:
        return False
    return abs(stats.max_val - stats.min_val) <= tol


def build_markdown_report(
    header: PlyHeaderInfo,
    property_types: dict[str, str],
    stats_by_name: dict[str, FieldStats],
    *,
    sample: int | None,
    analyzed_rows: int,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    size_mib = header.path.stat().st_size / (1024 * 1024)
    prop_names = [name for _, name in header.properties]
    schemas = classify_schema(prop_names)

    lines: list[str] = []
    w = lines.append

    w(f"# PLY 字段统计：`{header.path.name}`")
    w("")
    w(f"- 生成时间：{now}")
    w(f"- 路径：`{header.path.as_posix()}`")
    w(f"- 文件大小：{size_mib:.2f} MiB")
    w(f"- PLY format：`{header.format or '(unknown)'}`")
    w(f"- vertex 数量：{header.vertex_count:,}")
    if sample is not None and header.vertex_count > analyzed_rows:
        w(f"- 统计采样：{analyzed_rows:,} / {header.vertex_count:,} 行（`--sample {sample}`）")
    if header.other_elements:
        w("- 其他 element：" + ", ".join(f"`{n}`×{c}" for n, c in header.other_elements))
    w(f"- 推测 schema：{', '.join(schemas)}")
    w("")

    w("## Header 字段顺序")
    w("")
    w("| # | PLY 类型 | 字段名 |")
    w("|---|----------|--------|")
    for i, (ply_type, name) in enumerate(header.properties):
        w(f"| {i} | `{ply_type}` | `{name}` |")
    w("")

    w("## 数值分布")
    w("")
    w(
        "| 字段 | 类型 | finite | min | max | mean | std | p01 | p50 | p99 | unique | 备注 |"
    )
    w("|------|------|--------|-----|-----|------|-----|-----|-----|-----|--------|------|")

    for _, name in header.properties:
        st = stats_by_name.get(name)
        if st is None:
            w(f"| `{name}` | `{property_types.get(name, '?')}` | — | — | — | — | — | — | — | — | — | 非数值/未分析 |")
            continue

        notes: list[str] = []
        if st.nan_count or st.pos_inf_count or st.neg_inf_count:
            notes.append(
                f"nan={st.nan_count}, +inf={st.pos_inf_count}, -inf={st.neg_inf_count}"
            )
        if is_constant(st):
            notes.append(f"constant≈{fmt_float(st.min_val)}")
        elif st.unique_count is not None and st.unique_count <= 8 and st.top_values:
            tops = ", ".join(f"{fmt_float(v)}×{c}" for v, c in st.top_values[:5])
            notes.append(f"values: {tops}")

        w(
            "| `{name}` | `{typ}` | {fin:,} | {minv} | {maxv} | {mean} | {std} | {p01} | {p50} | {p99} | {uniq} | {note} |".format(
                name=name,
                typ=st.ply_type,
                fin=st.finite_count,
                minv=fmt_float(st.min_val),
                maxv=fmt_float(st.max_val),
                mean=fmt_float(st.mean),
                std=fmt_float(st.std),
                p01=fmt_float(st.p01),
                p50=fmt_float(st.p50),
                p99=fmt_float(st.p99),
                uniq="—" if st.unique_count is None else str(st.unique_count),
                note="; ".join(notes) if notes else "—",
            )
        )
    w("")

    # Highlight PBR-related constants for quick debugging
    pbr_fields = (
        "metallic",
        "roughness",
        "ior",
        "specular_factor",
        "specular_color_r",
        "specular_color_g",
        "specular_color_b",
        "opacity",
        "metallicFactor",
        "roughnessFactor",
    )
    present = [n for n in pbr_fields if n in stats_by_name]
    if present:
        w("## PBR / Specular 快速摘要")
        w("")
        for name in present:
            st = stats_by_name[name]
            if is_constant(st):
                w(f"- `{name}`：常量 **{fmt_float(st.min_val)}**（{st.finite_count:,} 行）")
            else:
                w(
                    f"- `{name}`：min={fmt_float(st.min_val)}, max={fmt_float(st.max_val)}, "
                    f"mean={fmt_float(st.mean)}, unique={'many' if st.unique_count is None else st.unique_count}"
                )
        w("")

    return "\n".join(lines)


def print_console_summary(
    header: PlyHeaderInfo,
    stats_by_name: dict[str, FieldStats],
    *,
    analyzed_rows: int,
    sample: int | None,
) -> None:
    prop_names = [n for _, n in header.properties]
    schemas = classify_schema(prop_names)
    print(f"File: {header.path}")
    print(f"  format={header.format}  vertices={header.vertex_count:,}  properties={len(header.properties)}")
    if sample is not None and header.vertex_count > analyzed_rows:
        print(f"  sampled={analyzed_rows:,} rows (--sample {sample})")
    print(f"  schema: {', '.join(schemas)}")
    print("")
    print(f"{'field':24} {'type':8} {'min':>12} {'max':>12} {'mean':>12} {'p50':>12}  notes")
    print("-" * 90)
    for _, name in header.properties:
        st = stats_by_name.get(name)
        if st is None:
            print(f"{name:24} {'?':8} {'—':>12} {'—':>12} {'—':>12} {'—':>12}  (non-numeric)")
            continue
        note = "const" if is_constant(st) else ""
        if st.unique_count is not None and st.unique_count <= 4 and st.top_values:
            note = ",".join(fmt_float(v) for v, _ in st.top_values[:4])
        print(
            f"{name:24} {st.ply_type:8} "
            f"{fmt_float(st.min_val):>12} {fmt_float(st.max_val):>12} "
            f"{fmt_float(st.mean):>12} {fmt_float(st.p50):>12}  {note}"
        )


def main() -> int:
    script_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ply", type=Path, help="Path to .ply file")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Write Markdown report to this path (default: debug/<stem>_ply_stats.md)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Randomly sample N vertices for stats (seed=0). Default: use all rows.",
    )
    parser.add_argument(
        "--max-unique",
        type=int,
        default=4096,
        help="If finite values <= this count, report exact unique values (default: 4096)",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print console summary only; do not write Markdown",
    )
    args = parser.parse_args()

    ply_path = args.ply.resolve()
    if not ply_path.is_file():
        print(f"PLY not found: {ply_path}", file=sys.stderr)
        return 1

    header, arrays = load_vertex_arrays(ply_path, args.sample)
    property_types = {name: typ for typ, name in header.properties}

    stats_by_name: dict[str, FieldStats] = {}
    for ply_type, name in header.properties:
        if name not in arrays:
            continue
        stats_by_name[name] = compute_field_stats(
            name,
            ply_type,
            arrays[name],
            max_unique=args.max_unique,
        )

    analyzed_rows = next(iter(arrays.values())).size if arrays else 0
    print_console_summary(header, stats_by_name, analyzed_rows=analyzed_rows, sample=args.sample)

    if not args.no_write:
        out_path = args.output
        if out_path is None:
            out_path = script_dir / f"{ply_path.stem}_ply_stats.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        report = build_markdown_report(
            header,
            property_types,
            stats_by_name,
            sample=args.sample,
            analyzed_rows=analyzed_rows,
        )
        out_path.write_text(report, encoding="utf-8")
        print("")
        print(f"Report written to {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
