#!/usr/bin/env python3
"""Survey GLB/GLTF assets for KHR_materials_ior and KHR_materials_specular usage.

Reads all .glb / .gltf files under ../input (or a custom directory), writes a
Markdown report to debug/input_material_extensions_report.md by default.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IOR_EXT = "KHR_materials_ior"
SPECULAR_EXT = "KHR_materials_specular"
LEGACY_SPEC_GLOSS = "KHR_materials_pbrSpecularGlossiness"

SPECULAR_FIELDS = (
    "specularFactor",
    "specularColorFactor",
    "specularTexture",
    "specularColorTexture",
)


@dataclass
class MaterialRecord:
    index: int
    name: str
    metallic_factor: float | None
    roughness_factor: float | None
    alpha_mode: str | None
    double_sided: bool
    material_extensions: list[str]
    ior: dict[str, Any] | None
    specular: dict[str, Any] | None
    legacy_spec_gloss: bool
    other_extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileRecord:
    path: Path
    extensions_used: list[str]
    extensions_required: list[str]
    material_count: int
    materials: list[MaterialRecord]
    parse_error: str | None = None


def read_glb_json(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError("file too small to be GLB")
    magic = data[:4]
    if magic != b"glTF":
        raise ValueError(f"invalid GLB magic: {magic!r}")
    json_chunk_length = struct.unpack_from("<I", data, 12)[0]
    json_chunk_type = struct.unpack_from("<I", data, 16)[0]
    if json_chunk_type != 0x4E4F534A:  # JSON
        raise ValueError(f"first chunk is not JSON (type=0x{json_chunk_type:08X})")
    json_start = 20
    json_end = json_start + json_chunk_length
    return json.loads(data[json_start:json_end].decode("utf-8"))


def read_gltf_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_gltf_root(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".glb":
        return read_glb_json(path)
    if suffix == ".gltf":
        return read_gltf_json(path)
    raise ValueError(f"unsupported extension: {suffix}")


def get_pbr_factors(material: dict[str, Any]) -> tuple[float | None, float | None]:
    pbr = material.get("pbrMetallicRoughness") or {}
    return pbr.get("metallicFactor"), pbr.get("roughnessFactor")


def summarize_specular(spec: dict[str, Any] | None) -> dict[str, Any]:
    if not spec:
        return {}
    out: dict[str, Any] = {}
    if "specularFactor" in spec:
        out["specularFactor"] = spec["specularFactor"]
    if "specularColorFactor" in spec:
        rgb = spec["specularColorFactor"]
        out["specularColorFactor"] = rgb
        if isinstance(rgb, list) and len(rgb) == 3:
            out["specularColorFactor_max"] = max(rgb)
            out["specularColorFactor_min"] = min(rgb)
            out["specularColorFactor_is_default_white"] = all(abs(c - 1.0) < 1e-6 for c in rgb)
            out["specularColorFactor_above_one"] = any(c > 1.0 + 1e-6 for c in rgb)
    if "specularTexture" in spec:
        out["specularTexture"] = spec["specularTexture"]
    if "specularColorTexture" in spec:
        out["specularColorTexture"] = spec["specularColorTexture"]
    return out


def analyze_file(path: Path) -> FileRecord:
    try:
        root = load_gltf_root(path)
    except Exception as exc:  # noqa: BLE001 - survey tool should continue
        return FileRecord(
            path=path,
            extensions_used=[],
            extensions_required=[],
            material_count=0,
            materials=[],
            parse_error=str(exc),
        )

    materials_json = root.get("materials") or []
    records: list[MaterialRecord] = []

    for idx, mat in enumerate(materials_json):
        extensions = mat.get("extensions") or {}
        metallic, roughness = get_pbr_factors(mat)
        ior_payload = extensions.get(IOR_EXT)
        spec_payload = extensions.get(SPECULAR_EXT)
        other = {
            k: v
            for k, v in extensions.items()
            if k not in (IOR_EXT, SPECULAR_EXT, LEGACY_SPEC_GLOSS)
        }
        records.append(
            MaterialRecord(
                index=idx,
                name=mat.get("name") or f"material_{idx}",
                metallic_factor=metallic,
                roughness_factor=roughness,
                alpha_mode=mat.get("alphaMode"),
                double_sided=bool(mat.get("doubleSided", False)),
                material_extensions=list(extensions.keys()),
                ior=ior_payload,
                specular=summarize_specular(spec_payload),
                legacy_spec_gloss=LEGACY_SPEC_GLOSS in extensions,
                other_extensions=other,
            )
        )

    return FileRecord(
        path=path,
        extensions_used=list(root.get("extensionsUsed") or []),
        extensions_required=list(root.get("extensionsRequired") or []),
        material_count=len(materials_json),
        materials=records,
    )


def fmt_rgb(rgb: list[float] | None) -> str:
    if not rgb:
        return "—"
    return f"[{rgb[0]:.6g}, {rgb[1]:.6g}, {rgb[2]:.6g}]"


def texture_ref_label(tex_info: dict[str, Any] | None) -> str:
    if not tex_info:
        return "—"
    parts = [f"index={tex_info.get('index')}"]
    if "texCoord" in tex_info:
        parts.append(f"texCoord={tex_info['texCoord']}")
    ext = tex_info.get("extensions") or {}
    if ext:
        parts.append(f"ext={','.join(ext.keys())}")
    return ", ".join(parts)


def build_report(files: list[FileRecord], input_dir: Path) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    w = lines.append

    w("# input 数据集材质扩展调查（IOR / Specular）")
    w("")
    w(f"- 生成时间：{now}")
    w(f"- 输入目录：`{input_dir.as_posix()}`")
    w("- 调查脚本：`debug/survey_gltf_material_extensions.py`")
    w("")
    w("## 背景")
    w("")
    w("glTF 2.0 在 core metallic-roughness 之外，常用 Khronos 扩展控制介质与镜面反射：")
    w("")
    w("| 扩展 | 主要字段 | 含义 |")
    w("|------|----------|------|")
    w("| `KHR_materials_ior` | `ior` | 折射率；缺省 glTF 约定为 **1.5** |")
    w("| `KHR_materials_specular` | `specularFactor`, `specularColorFactor`, `specularTexture`, `specularColorTexture` | 调节 dielectric 镜面强度与 F0 颜色（linear RGB，可 >1） |")
    w("")
    w("当前 mesh2splat 仅在 C++ 侧解析 `KHR_materials_ior` 并写入 BRDF PLY 的 `ior` 字段；**未读取 specular 扩展**。")
    w("")

    ok_files = [f for f in files if f.parse_error is None]
    err_files = [f for f in files if f.parse_error is not None]

    total_materials = sum(f.material_count for f in ok_files)
    ior_materials = sum(1 for f in ok_files for m in f.materials if m.ior is not None)
    spec_materials = sum(1 for f in ok_files for m in f.materials if m.specular)
    legacy_sg = sum(1 for f in ok_files for m in f.materials if m.legacy_spec_gloss)

    ext_used_counter: Counter[str] = Counter()
    other_ext_counter: Counter[str] = Counter()
    for f in ok_files:
        ext_used_counter.update(f.extensions_used)
        for m in f.materials:
            other_ext_counter.update(m.other_extensions.keys())

    w("## 汇总")
    w("")
    w(f"| 指标 | 数值 |")
    w(f"|------|------|")
    w(f"| 扫描文件数 | {len(files)} |")
    w(f"| 解析失败 | {len(err_files)} |")
    w(f"| 材质总数 | {total_materials} |")
    w(f"| 含 `{IOR_EXT}` 的材质 | {ior_materials} |")
    w(f"| 含 `{SPECULAR_EXT}` 的材质 | {spec_materials} |")
    w(f"| 含 `{LEGACY_SPEC_GLOSS}` 的材质 | {legacy_sg} |")
    w("")

    if ext_used_counter:
        w("### 文件级 `extensionsUsed`")
        w("")
        for name, count in ext_used_counter.most_common():
            w(f"- `{name}`：{count} 个文件")
        w("")

    if other_ext_counter:
        w("### 材质级其他扩展（除 IOR / Specular / 旧 SpecGloss）")
        w("")
        for name, count in other_ext_counter.most_common():
            w(f"- `{name}`：{count} 个材质")
        w("")

    # Specular field usage among materials that have the extension
    spec_with_factor = spec_with_color = spec_with_tex = spec_with_col_tex = 0
    color_above_one = color_not_white = color_exact_two = 0
    spec_factor_values: list[float] = []
    color_max_values: list[float] = []

    for f in ok_files:
        for m in f.materials:
            s = m.specular
            if not s:
                continue
            if "specularFactor" in s:
                spec_with_factor += 1
                spec_factor_values.append(float(s["specularFactor"]))
            if "specularColorFactor" in s:
                spec_with_color += 1
                rgb = s["specularColorFactor"]
                color_max_values.append(max(rgb))
                if s.get("specularColorFactor_above_one"):
                    color_above_one += 1
                if not s.get("specularColorFactor_is_default_white"):
                    color_not_white += 1
                if all(abs(c - 2.0) < 1e-4 for c in rgb):
                    color_exact_two += 1
            if "specularTexture" in s:
                spec_with_tex += 1
            if "specularColorTexture" in s:
                spec_with_col_tex += 1

    w("### Specular 字段使用（仅限已启用扩展的材质）")
    w("")
    w(f"| 字段 | 出现次数 |")
    w(f"|------|----------|")
    w(f"| `specularFactor` | {spec_with_factor} |")
    w(f"| `specularColorFactor` | {spec_with_color} |")
    w(f"| `specularTexture` | {spec_with_tex} |")
    w(f"| `specularColorTexture` | {spec_with_col_tex} |")
    w(f"| `specularColorFactor` 任一分量 > 1 | {color_above_one} |")
    w(f"| `specularColorFactor` 非默认 [1,1,1] | {color_not_white} |")
    w(f"| `specularColorFactor` 恰为 [2,2,2] | {color_exact_two} |")
    w("")

    if spec_factor_values:
        w(
            f"- `specularFactor` 范围：{min(spec_factor_values):.6g} … {max(spec_factor_values):.6g}"
        )
    if color_max_values:
        w(
            f"- `specularColorFactor` 最大分量范围：{min(color_max_values):.6g} … {max(color_max_values):.6g}"
        )
    w("")

    w("## 对 mesh2splat 的影响（初步）")
    w("")
    if ior_materials == 0:
        w(
            f"- **IOR**：本批输入中没有任何材质显式携带 `{IOR_EXT}`；导出时将全部使用代码默认值 **1.5**。"
        )
    else:
        w(
            f"- **IOR**：{ior_materials}/{total_materials} 个材质有显式 IOR，需确保解析与 BRDF 管线一致。"
        )
    if spec_materials > 0:
        pct = 100.0 * spec_materials / total_materials if total_materials else 0.0
        w(
            f"- **Specular**：{spec_materials}/{total_materials} 个材质（{pct:.1f}%）携带 `{SPECULAR_EXT}`，"
            "当前转换流程会忽略这些参数，dielectric 镜面/F0 可能与源资产不一致。"
        )
        if color_exact_two > 0:
            w(
                f"  - 其中 {color_exact_two} 个材质使用 `specularColorFactor = [2,2,2]`（常见于 reflectance→specular 换算），"
                "相当于在 IOR=1.5 下提高 F0。"
            )
        if spec_with_col_tex > 0:
            w(
                f"  - {spec_with_col_tex} 个材质还绑定了 `specularColorTexture`，空间变化 F0 同样未被采样。"
            )
    else:
        w(f"- **Specular**：本批输入未使用 `{SPECULAR_EXT}`。")
    w("")

    w("## 逐文件明细")
    w("")

    for f in sorted(files, key=lambda x: x.path.name.lower()):
        w(f"### `{f.path.name}`")
        w("")
        if f.parse_error:
            w(f"**解析失败**：{f.parse_error}")
            w("")
            continue

        rel = f.path.stat().st_size / (1024 * 1024)
        w(f"- 大小：{rel:.2f} MiB")
        w(f"- 材质数：{f.material_count}")
        w(f"- `extensionsUsed`：{f.extensions_used or '（无）'}")
        w(f"- `extensionsRequired`：{f.extensions_required or '（无）'}")
        w("")

        if not f.materials:
            w("（无材质）")
            w("")
            continue

        w("| # | 材质名 | metallic | roughness | IOR | specularFactor | specularColorFactor | specularTexture | specularColorTexture | 其他扩展 |")
        w("|---|--------|----------|-----------|-----|----------------|---------------------|-----------------|----------------------|----------|")

        for m in f.materials:
            ior_cell = "—"
            if m.ior is not None:
                ior_val = m.ior.get("ior")
                ior_cell = str(ior_val) if ior_val is not None else json.dumps(m.ior, ensure_ascii=False)

            spec = m.specular
            sf = spec.get("specularFactor", "—") if spec else "—"
            scf = fmt_rgb(spec.get("specularColorFactor")) if spec else "—"
            st = texture_ref_label(spec.get("specularTexture")) if spec else "—"
            sct = texture_ref_label(spec.get("specularColorTexture")) if spec else "—"
            other = ", ".join(m.other_extensions.keys()) if m.other_extensions else "—"
            if m.legacy_spec_gloss:
                other = (other + ", " if other != "—" else "") + LEGACY_SPEC_GLOSS

            metallic = "—" if m.metallic_factor is None else f"{m.metallic_factor:g}"
            roughness = "—" if m.roughness_factor is None else f"{m.roughness_factor:g}"

            w(
                f"| {m.index} | {m.name} | {metallic} | {roughness} | {ior_cell} | {sf} | {scf} | {st} | {sct} | {other} |"
            )
        w("")

    w("## 附录：仅含 Specular 扩展的材质清单")
    w("")
    any_spec = False
    for f in sorted(ok_files, key=lambda x: x.path.name.lower()):
        spec_mats = [m for m in f.materials if m.specular]
        if not spec_mats:
            continue
        any_spec = True
        w(f"### `{f.path.name}`")
        w("")
        for m in spec_mats:
            w(f"- **#{m.index} `{m.name}`**")
            for key in SPECULAR_FIELDS:
                if key in (m.specular or {}):
                    val = m.specular[key]
                    if key.endswith("Texture"):
                        w(f"  - `{key}`: {texture_ref_label(val)}")
                    else:
                        w(f"  - `{key}`: `{val}`")
        w("")

    if not any_spec:
        w("（无）")
        w("")

    return "\n".join(lines)


def discover_assets(input_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in ("*.glb", "*.gltf"):
        paths.extend(input_dir.glob(pattern))
    return sorted(paths, key=lambda p: p.name.lower())


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    default_input = script_dir.parent / "../specular_debug/saccharinum"
    default_output = script_dir / "input_material_extensions_report.md"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=default_input,
        help=f"Directory containing GLB/GLTF files (default: {default_input})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help=f"Markdown report path (default: {default_output})",
    )
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    if not input_dir.is_dir():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 1

    assets = discover_assets(input_dir)
    if not assets:
        print(f"No .glb/.gltf files under {input_dir}", file=sys.stderr)
        return 1

    records = [analyze_file(p) for p in assets]
    report = build_report(records, input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")

    ok = sum(1 for r in records if r.parse_error is None)
    spec = sum(1 for r in records if r.parse_error is None for m in r.materials if m.specular)
    ior = sum(1 for r in records if r.parse_error is None for m in r.materials if m.ior)
    print(f"Scanned {len(records)} file(s) ({ok} ok). Materials with specular={spec}, ior={ior}.")
    print(f"Report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
