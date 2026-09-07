# input 数据集材质扩展调查（IOR / Specular）

- 生成时间：2026-08-23 16:28 UTC
- 输入目录：`C:/Users/31866/Desktop/specular_debug/saccharinum`
- 调查脚本：`debug/survey_gltf_material_extensions.py`

## 背景

glTF 2.0 在 core metallic-roughness 之外，常用 Khronos 扩展控制介质与镜面反射：

| 扩展 | 主要字段 | 含义 |
|------|----------|------|
| `KHR_materials_ior` | `ior` | 折射率；缺省 glTF 约定为 **1.5** |
| `KHR_materials_specular` | `specularFactor`, `specularColorFactor`, `specularTexture`, `specularColorTexture` | 调节 dielectric 镜面强度与 F0 颜色（linear RGB，可 >1） |

当前 mesh2splat 仅在 C++ 侧解析 `KHR_materials_ior` 并写入 BRDF PLY 的 `ior` 字段；**未读取 specular 扩展**。

## 汇总

| 指标 | 数值 |
|------|------|
| 扫描文件数 | 3 |
| 解析失败 | 0 |
| 材质总数 | 9 |
| 含 `KHR_materials_ior` 的材质 | 3 |
| 含 `KHR_materials_specular` 的材质 | 6 |
| 含 `KHR_materials_pbrSpecularGlossiness` 的材质 | 0 |

### 文件级 `extensionsUsed`

- `KHR_materials_specular`：2 个文件
- `KHR_materials_ior`：1 个文件

### Specular 字段使用（仅限已启用扩展的材质）

| 字段 | 出现次数 |
|------|----------|
| `specularFactor` | 0 |
| `specularColorFactor` | 6 |
| `specularTexture` | 0 |
| `specularColorTexture` | 0 |
| `specularColorFactor` 任一分量 > 1 | 6 |
| `specularColorFactor` 非默认 [1,1,1] | 6 |
| `specularColorFactor` 恰为 [2,2,2] | 6 |

- `specularColorFactor` 最大分量范围：2 … 2

## 对 mesh2splat 的影响（初步）

- **IOR**：3/9 个材质有显式 IOR，需确保解析与 BRDF 管线一致。
- **Specular**：6/9 个材质（66.7%）携带 `KHR_materials_specular`，当前转换流程会忽略这些参数，dielectric 镜面/F0 可能与源资产不一致。
  - 其中 6 个材质使用 `specularColorFactor = [2,2,2]`（常见于 reflectance→specular 换算），相当于在 IOR=1.5 下提高 F0。

## 逐文件明细

### `saccharinum1.glb`

- 大小：286.57 MiB
- 材质数：3
- `extensionsUsed`：（无）
- `extensionsRequired`：（无）

| # | 材质名 | metallic | roughness | IOR | specularFactor | specularColorFactor | specularTexture | specularColorTexture | 其他扩展 |
|---|--------|----------|-----------|-----|----------------|---------------------|-----------------|----------------------|----------|
| 0 | trunk | 0.7 | 0.3 | — | — | — | — | — | — |
| 1 | branch | 0.3 | 0.5 | — | — | — | — | — | — |
| 2 | vray Material #28 | 0.1 | 0.6 | — | — | — | — | — | — |

### `saccharinum2.glb`

- 大小：286.57 MiB
- 材质数：3
- `extensionsUsed`：['KHR_materials_specular']
- `extensionsRequired`：（无）

| # | 材质名 | metallic | roughness | IOR | specularFactor | specularColorFactor | specularTexture | specularColorTexture | 其他扩展 |
|---|--------|----------|-----------|-----|----------------|---------------------|-----------------|----------------------|----------|
| 0 | trunk | 0.7 | 0.3 | — | — | [2, 2, 2] | — | — | — |
| 1 | branch | 0.3 | 0.5 | — | — | [2, 2, 2] | — | — | — |
| 2 | vray Material #28 | 0.1 | 0.6 | — | — | [2, 2, 2] | — | — | — |

### `saccharinum3.glb`

- 大小：286.57 MiB
- 材质数：3
- `extensionsUsed`：['KHR_materials_specular', 'KHR_materials_ior']
- `extensionsRequired`：（无）

| # | 材质名 | metallic | roughness | IOR | specularFactor | specularColorFactor | specularTexture | specularColorTexture | 其他扩展 |
|---|--------|----------|-----------|-----|----------------|---------------------|-----------------|----------------------|----------|
| 0 | trunk | 0.7 | 0.3 | 1.7999999523162842 | — | [2, 2, 2] | — | — | — |
| 1 | branch | 0.3 | 0.5 | 1.7999999523162842 | — | [2, 2, 2] | — | — | — |
| 2 | vray Material #28 | 0.1 | 0.6 | 1.7999999523162842 | — | [2, 2, 2] | — | — | — |

## 附录：仅含 Specular 扩展的材质清单

### `saccharinum2.glb`

- **#0 `trunk`**
  - `specularColorFactor`: `[2.0, 2.0, 2.0]`
- **#1 `branch`**
  - `specularColorFactor`: `[2.0, 2.0, 2.0]`
- **#2 `vray Material #28`**
  - `specularColorFactor`: `[2.0, 2.0, 2.0]`

### `saccharinum3.glb`

- **#0 `trunk`**
  - `specularColorFactor`: `[2.0, 2.0, 2.0]`
- **#1 `branch`**
  - `specularColorFactor`: `[2.0, 2.0, 2.0]`
- **#2 `vray Material #28`**
  - `specularColorFactor`: `[2.0, 2.0, 2.0]`
