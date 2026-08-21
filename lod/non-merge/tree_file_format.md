# LOD 层次树二进制格式（`.m2lt`）

本文描述由 `lod/build_center_distance_lod_tree.py` 生成的树文件布局。字节序一律为 **little-endian**。

## 语义约定

- **根结点** id = **0**；其余结点 id **1 … N−1** 连续递增。
- **每层一个 PLY**：目录中 `level01.ply` … `level0L.ply`（或 `level_01.ply`），**序号越小越粗**；`depth = l` 的结点集合恰为该层 PLY 的全部顶点（高斯）。
- **父边**：最细层逐层向上，每个高斯在 **相邻较粗层** 中连到**球心欧氏距离最近**的父高斯；`depth = 1` 的结点父均为根 **0**。
- **`kind`**：**0** = 根，**1** = 高斯。

## FileHeader（固定 64 字节）

| 偏移 | 大小 | 类型 | 字段 | 说明 |
|------|------|------|------|------|
| 0 | 4 | char[4] | `magic` | ASCII `'M2LT'` |
| 4 | 4 | uint32 | `version` | 当前为 **2**（与 v1 的 32 字节结点布局不兼容） |
| 8 | 4 | uint32 | `reserved0` | 保留，写 **0** |
| 12 | 4 | uint32 | `lod_levels` | **L**：最细层 `depth`，即 `level0L` 的 `depth` |
| 16 | 4 | uint32 | `total_nodes` | 结点总数 **N**（含根 + 全部高斯） |
| 20 | 4 | uint32 | `csr_child_count` | `flat_children` 条数；连通树时为 **N−1** |
| 24 | 4 | uint32 | `node_record_bytes` | 单条结点记录字节数，当前为 **12** |
| 28 | 4 | uint32 | `flags` | 保留，写 **0** |
| 32 | 4 | uint32 | `header_bytes` | 本头部长度，当前为 **64** |
| 36 | 28 | uint8[28] | `reserved` | 填 0 |

先 `struct.pack('<4s8I', magic, version, reserved0, lod_levels, total_nodes, csr_child_count, node_record_bytes, flags, header_bytes)`（36 字节），再追加 **28 字节 0**，总长 64。

紧随头部为三段连续数据：**`nodes`**、**`csr_off`**、**`flat_children`**（段间无额外对齐填充）。

## 区段 A：`nodes`（N × 12 字节）

结点按 **id 升序**：文件内第 `i` 条记录对应 id = `i`。

每条 **NodeRecord（12 字节）**：

| 偏移 | 大小 | 类型 | 字段 | 说明 |
|------|------|------|------|------|
| 0 | 4 | uint32 | `parent_id` | 父 id；根的父为 **`0xFFFFFFFF`** |
| 4 | 1 | uint8 | `kind` | **0** = root，**1** = 高斯 |
| 5 | 1 | uint8 | `depth` | 根为 **0**；`level01` 为 **1**；最细层为 **L** |
| 6 | 2 | — | `pad` | 填 **0** |
| 8 | 4 | uint32 | `gauss_index` | **`kind=1`**：该层 PLY 顶点顺序下标（从 0 起）；**根**为 **`0xFFFFFFFF`** |

`struct.pack('<IBB2xI', parent_id, kind, depth, gauss_index)`，共 12 字节。

## 区段 B：`csr_off`（(N+1) × 4 字节）

- `csr_off[i]`：`flat_children` 中结点 `i` 的子列表起始下标。
- `csr_off[N]` = `csr_child_count`。
- 结点 `i` 的子 id 区间为 `flat_children[csr_off[i] … csr_off[i+1])`。
- 每个结点下的子 id **升序**，保证输出确定。

## 区段 C：`flat_children`（`csr_child_count` × 4 字节）

每条为子结点全局 id（uint32）。

## 校验

对任意 `child ≥ 1`：`nodes[child].parent_id == parent`；根：`kind==0` 且 `parent_id==0xFFFFFFFF`。

## 工具脚本

- **`lod/build_center_distance_lod_tree.py`**：从含多层 PLY 的目录生成上述 `.m2lt`。
- 依赖见 **`lod/requirements-lod.txt`**。
