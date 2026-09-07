#!/usr/bin/env python3
"""Patch mesh2splat / meshsample PLY files to match mesh_to_3dgs_tree_ply_params schema.

For each .ply under an input directory, verify header fields against the expected
BRDF export layout. Non-compliant files are rewritten with ior + specular channels
(5 float properties) inserted after roughness; missing values use CLI defaults.

Examples:
  python debug/patch_ply_ior_specular.py output/old_ply --output output/patched
  python debug/patch_ply_ior_specular.py output/old_ply -o output/patched \\
      --ior 1.5 --specular-factor 1.0 --specular-color 1.0 1.0 1.0
  python debug/patch_ply_ior_specular.py output/old_ply -o output/patched --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    from plyfile import PlyData, PlyElement
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: pip install numpy plyfile\n"
        f"Original error: {exc}"
    ) from exc


EXPECTED_FORMAT = "binary_little_endian"

EXPECTED_PROPERTIES: tuple[str, ...] = (
    "x",
    "y",
    "z",
    "nx",
    "ny",
    "nz",
    "base_r",
    "base_g",
    "base_b",
    "metallic",
    "roughness",
    "ior",
    "specular_factor",
    "specular_color_r",
    "specular_color_g",
    "specular_color_b",
    "opacity",
    "scale_0",
    "scale_1",
    "scale_2",
    "rot_0",
    "rot_1",
    "rot_2",
    "rot_3",
)

PATCH_FIELDS: frozenset[str] = frozenset(
    {
        "ior",
        "specular_factor",
        "specular_color_r",
        "specular_color_g",
        "specular_color_b",
    }
)

BASE_FIELDS: tuple[str, ...] = tuple(n for n in EXPECTED_PROPERTIES if n not in PATCH_FIELDS)

OUTPUT_DTYPE = np.dtype([(name, "<f4") for name in EXPECTED_PROPERTIES])


@dataclass
class PlyHeaderInfo:
    path: Path
    format: str
    vertex_count: int
    properties: list[tuple[str, str]]  # (ply_type, name)


@dataclass
class ComplianceResult:
    compliant: bool
    reason: str


@dataclass
class SpecularDefaults:
    ior: float
    specular_factor: float
    specular_color_r: float
    specular_color_g: float
    specular_color_b: float

    def value_for(self, name: str) -> float:
        return {
            "ior": self.ior,
            "specular_factor": self.specular_factor,
            "specular_color_r": self.specular_color_r,
            "specular_color_g": self.specular_color_g,
            "specular_color_b": self.specular_color_b,
        }[name]


def parse_ply_header(path: Path) -> PlyHeaderInfo:
    properties: list[tuple[str, str]] = []
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
                raise ValueError(f"Unexpected EOF while reading PLY header: {path}")
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
                elif current_element is not None:
                    raise ValueError(
                        f"Unsupported non-vertex element '{current_element}' in {path.name}"
                    )
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
    )


def check_compliance(header: PlyHeaderInfo) -> ComplianceResult:
    if header.format != EXPECTED_FORMAT:
        return ComplianceResult(
            compliant=False,
            reason=f"format={header.format!r}, expected {EXPECTED_FORMAT!r}",
        )

    names = [name for _, name in header.properties]
    types = [typ for typ, _ in header.properties]

    if names != list(EXPECTED_PROPERTIES):
        return ComplianceResult(
            compliant=False,
            reason="property names or order mismatch",
        )

    if not all(typ == "float" for typ in types):
        bad = [(typ, name) for typ, name in header.properties if typ != "float"]
        return ComplianceResult(
            compliant=False,
            reason=f"non-float vertex properties: {bad}",
        )

    return ComplianceResult(compliant=True, reason="")


def patch_vertex_array(vertex: np.ndarray, defaults: SpecularDefaults) -> np.ndarray:
    src_names = set(vertex.dtype.names or ())
    missing_base = [name for name in BASE_FIELDS if name not in src_names]
    if missing_base:
        raise ValueError(f"missing required fields: {', '.join(missing_base)}")

    n = len(vertex)
    out = np.empty(n, dtype=OUTPUT_DTYPE)

    for name in BASE_FIELDS:
        out[name] = np.asarray(vertex[name], dtype=np.float32)

    for name in PATCH_FIELDS:
        if name in src_names:
            out[name] = np.asarray(vertex[name], dtype=np.float32)
        else:
            out[name] = np.float32(defaults.value_for(name))

    return out


def write_ply(path: Path, vertex_array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    element = PlyElement.describe(vertex_array, "vertex")
    PlyData([element], text=False).write(str(path))


def iter_ply_files(input_dir: Path, recursive: bool) -> list[Path]:
    pattern = "**/*.ply" if recursive else "*.ply"
    files = sorted(input_dir.glob(pattern))
    return [p for p in files if p.is_file()]


def process_file(
    ply_path: Path,
    output_dir: Path,
    defaults: SpecularDefaults,
    *,
    dry_run: bool,
    copy_compliant: bool,
) -> str:
    header = parse_ply_header(ply_path)
    compliance = check_compliance(header)

    rel_name = ply_path.name
    out_path = output_dir / rel_name

    if compliance.compliant:
        if dry_run:
            return f"OK  {rel_name} (already compliant, skip write)"
        if copy_compliant:
            output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ply_path, out_path)
            return f"OK  {rel_name} (already compliant, copied)"
        return f"OK  {rel_name} (already compliant, skipped)"

    ply = PlyData.read(str(ply_path))
    if "vertex" not in ply:
        raise ValueError(f"no 'vertex' element in {ply_path.name}")

    patched = patch_vertex_array(ply["vertex"].data, defaults)

    if dry_run:
        return f"PATCH {rel_name} ({compliance.reason})"

    write_ply(out_path, patched)
    return f"PATCH {rel_name} -> {out_path} ({compliance.reason})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing .ply files to inspect and patch",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="Directory to write patched .ply files",
    )
    parser.add_argument(
        "--ior",
        type=float,
        default=1.5,
        help="Default ior when the field is missing (default: 1.5)",
    )
    parser.add_argument(
        "--specular-factor",
        type=float,
        default=1.0,
        help="Default specular_factor when missing (default: 1.0)",
    )
    parser.add_argument(
        "--specular-color",
        type=float,
        nargs=3,
        metavar=("R", "G", "B"),
        default=(1.0, 1.0, 1.0),
        help="Default specular_color RGB when missing (linear, default: 1 1 1)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Include .ply files in subdirectories",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report compliance; do not write files",
    )
    parser.add_argument(
        "--copy-compliant",
        action="store_true",
        help="Copy already-compliant files to --output (default: skip them)",
    )
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    output_dir = args.output.resolve()

    if not input_dir.is_dir():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 1

    defaults = SpecularDefaults(
        ior=args.ior,
        specular_factor=args.specular_factor,
        specular_color_r=args.specular_color[0],
        specular_color_g=args.specular_color[1],
        specular_color_b=args.specular_color[2],
    )

    ply_files = iter_ply_files(input_dir, args.recursive)
    if not ply_files:
        print(f"No .ply files found under {input_dir}")
        return 0

    print(f"Found {len(ply_files)} PLY file(s) in {input_dir}")
    if args.dry_run:
        print("Dry run: no files will be written")
    else:
        print(f"Output directory: {output_dir}")
    print(
        "Defaults for missing ior/specular fields: "
        f"ior={defaults.ior}, specular_factor={defaults.specular_factor}, "
        f"specular_color=({defaults.specular_color_r}, {defaults.specular_color_g}, {defaults.specular_color_b})"
    )
    print("")

    ok_count = 0
    patch_count = 0
    error_count = 0

    for ply_path in ply_files:
        try:
            message = process_file(
                ply_path,
                output_dir,
                defaults,
                dry_run=args.dry_run,
                copy_compliant=args.copy_compliant,
            )
            print(message)
            if message.startswith("OK"):
                ok_count += 1
            else:
                patch_count += 1
        except Exception as exc:
            error_count += 1
            print(f"ERROR {ply_path.name}: {exc}", file=sys.stderr)

    print("")
    print(
        f"Done: {ok_count} compliant/skipped, {patch_count} patched"
        + (f", {error_count} errors" if error_count else "")
    )
    return 1 if error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
