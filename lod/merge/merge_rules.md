# KD-tree 合并 LOD — 高斯合并规则

本文描述 `lod/merge/gaussian_merge.py` 中 `merge_gaussians()` 的语义。PLY 字段布局见 `docs/mesh_to_3dgs_tree_ply_params.md`。

## 输入 / 输出

- **输入**：1 个或 2 个子高斯（unary 补齐时仅 1 个；二叉合并时 2 个）。每个子高斯含坐标、法线、BRDF、opacity、对数 scale、四元数旋转。
- **输出**：1 个父高斯，字段类型与 PLY 一致。

实现上将 scale/rot 转为协方差 **Σ = R diag(σ²) Rᵀ**（σᵢ = exp(scale_i)）合并，再特征分解回 scale/rot。

## 权重

对第 i 个子高斯：

```text
σ_i = (exp(scale_0), exp(scale_1), exp(scale_2))
A_i = σ_x σ_y + σ_y σ_z + σ_z σ_x          # 表面积相关量
w_i = opacity_i × A_i
α_i = w_i / Σ_j w_j                         # 归一化权重
```

若 Σ w_j = 0（退化），令 α_i = 1 / n。

## 线性混合属性（使用 α）

| 属性 | 合并 |
|------|------|
| `x, y, z` | Σ α_i · position_i |
| `base_r, base_g, base_b` | Σ α_i · basecolor_i，再 **clamp 到 [0, 1]** |
| `metallic`, `roughness` | Σ α_i · value_i，再 **clamp 到 [0, 1]** |
| `nx, ny, nz` | **n** = Σ α_i · normal_i；若 ‖**n**‖ < 1e-8，取 **α 最大**子节点的法线；否则 **n** ← **n** / ‖**n**‖ |

## 协方差（moment matching）

先算加权中心 **μ** = Σ α_i **μ**_i，再：

```text
Σ_parent = Σ_i α_i ( Σ_i + (μ_i − μ)(μ_i − μ)ᵀ )
```

对称存储为 6 元 `[xx, xy, xz, yy, yz, zz]`。对 **Σ_parent** 做特征分解：

- 特征值 λ ← max(λ, 1e-16)
- 按 λ **降序** 排列，得 σ = √λ，scale_k = log(σ_k)
- 特征向量转四元数 `(rot_0…3)` = `(w,x,y,z)`，并归一化

## Opacity

```text
A_parent = σ_x σ_y + σ_y σ_z + σ_z σ_x    # 由合并后的 scale 计算
opacity_parent = (Σ_i w_i) / A_parent
opacity_parent = clamp(opacity_parent, 0, 1)
```

## Unary pass-through

仅 1 个子结点时，不调用上述公式，**原样拷贝**子高斯（深度补齐链）。

## 与建树的关系

- **建树**（`kdtree_build.py`）：KD 划分 → 叶深度补齐 → 后序遍历调用 `merge_gaussians`。
- **合并**（`gaussian_merge.py`）：纯函数，不依赖树结构。
- **导出**：各 depth 的 PLY 按 `gauss_index` 升序写入；最细层 `gauss_index == source_index`。
