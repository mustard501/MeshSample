# LOD 层次树二进制格式（`.m2lt`）— KD-tree 合并生成

本文描述由 `lod/merge/build_kdtree_merge_lod.py` 生成的树文件布局。字节序一律为 **little-endian**。

与 `lod/tree_file_format.md`（格式 version **2**，虚根 + 事后连边）不同，合并树使用 **version 3**：根结点为 **真实高斯**，所有结点 `kind=1`。

## 语义约定

- **根结点** id = **0**，`depth = 0`，对应 **`level00.ply`** 第 **0** 行（唯一顶点）。
- 其余结点 id **1 … N−1** 按 **depth 升序、同 depth 内 `gauss_index` 升序** 连续编号。
- **每层一个 PLY**：`level00.ply` … `level0L.ply`（两位 depth，`level06.ply` 表示 depth = 6）。**depth 越小越粗**；`depth = l` 的结点集合恰为该层 PLY 的全部顶点（高斯）。
- **PLY 行序**：`level0l.ply` 第 `k` 行（`k` 从 0 起）对应满足 `depth = l` 且 `gauss_index = k` 的结点。遍历树读取属性时，用 `(depth, gauss_index)` 定位到 `level0{depth:02d}.ply` 的第 `gauss_index` 行。
- **父边**：KD-tree 合并树的父子关系；内结点可有 **1 或 2** 个子结点（单子深度补齐为 unary 链）。`flat_children` 中每个父结点下的子 id **升序**。
- **最细层** `depth = L`：每个原始输入高斯恰出现一次；`gauss_index` 等于输入 PLY 的顶点下标（`source_index`），故 **`level0L.ply` 与输入 PLY 逐顶点一致**（含顺序）。
- **`kind`**：合并树中 **恒为 1**（高斯）。version 2 的 `kind=0` 虚根不再使用。
- **`lod_levels`**：**L**，最细层 depth。

## FileHeader（固定 64 字节）

| 偏移 | 大小 | 类型 | 字段 | 说明 |
|------|------|------|------|------|
| 0 | 4 | char[4] | `magic` | ASCII `'M2LT'` |
| 4 | 4 | uint32 | `version` | 合并树为 **3** |
| 8 | 4 | uint32 | `reserved0` | 保留，写 **0** |
| 12 | 4 | uint32 | `lod_levels` | **L**：最细层 `depth` |
| 16 | 4 | uint32 | `total_nodes` | 结点总数 **N**（全部为高斯结点） |
| 20 | 4 | uint32 | `csr_child_count` | `flat_children` 条数；连通树时为 **N−1** |
| 24 | 4 | uint32 | `node_record_bytes` | 单条结点记录字节数，**12** |
| 28 | 4 | uint32 | `flags` | bit0 = **1** 表示 KD-tree 合并树；其余保留 **0** |
| 32 | 4 | uint32 | `header_bytes` | 本头部长度，**64** |
| 36 | 28 | uint8[28] | `reserved` | 填 0 |

先 `struct.pack('<4s8I', …)`（36 字节），再追加 **28 字节 0**，总长 64。

紧随头部为三段连续数据：**`nodes`**、**`csr_off`**、**`flat_children`**（段间无额外对齐填充）。

## 区段 A：`nodes`（N × 12 字节）

结点按 **id 升序**：文件内第 `i` 条记录对应 id = `i`。

每条 **NodeRecord（12 字节）**：

| 偏移 | 大小 | 类型 | 字段 | 说明 |
|------|------|------|------|------|
| 0 | 4 | uint32 | `parent_id` | 父 id；**id=0** 的父为 **`0xFFFFFFFF`** |
| 4 | 1 | uint8 | `kind` | **1** = 高斯 |
| 5 | 1 | uint8 | `depth` | **0 … L**；与 `level0{depth:02d}.ply` 对应 |
| 6 | 2 | — | `pad` | 填 **0** |
| 8 | 4 | uint32 | `gauss_index` | 该 depth 层 PLY 内的顶点下标（从 0 起） |

`struct.pack('<IBB2xI', parent_id, kind, depth, gauss_index)`，共 12 字节。

## 区段 B：`csr_off`（(N+1) × 4 字节）

- `csr_off[i]`：`flat_children` 中结点 `i` 的子列表起始下标。
- `csr_off[N]` = `csr_child_count`。
- 结点 `i` 的子 id 区间为 `flat_children[csr_off[i] … csr_off[i+1])`。
- 每个结点下的子 id **升序**。

## 区段 C：`flat_children`（`csr_child_count` × 4 字节）

每条为子结点全局 id（uint32）。

## 校验

- id = **0**：`parent_id == 0xFFFFFFFF`，`depth == 0`，`kind == 1`，`gauss_index == 0`。
- 任意 `child ≥ 1`：`nodes[child].parent_id == parent`。
- 每个 `depth = l` 的结点 `gauss_index` 取值 **0 … (N_l − 1)**，且同 depth 内不重不漏。

## 工具脚本

- **`lod/merge/build_kdtree_merge_lod.py`**：从单份精细 PLY 建树、合并并导出各层 PLY + `.m2lt`。
- 合并规则见 **`lod/merge/merge_rules.md`**。
- 依赖见 **`lod/requirements-lod.txt`**。
