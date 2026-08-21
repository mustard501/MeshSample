"""Write merge LOD .m2lt files (format version 3)."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from kdtree_build import MergeTreeNode

M2LT_MAGIC = b"M2LT"
M2LT_VERSION = 3
M2LT_FLAGS_KDTREE_MERGE = 1
NODE_RECORD_BYTES = 12
HEADER_BYTES = 64
ROOT_PARENT = -1  # 0xFFFFFFFF as int32
KIND_GAUSSIAN = 1


def _pack_node_record(parent_id: int, kind: int, depth: int, gauss_index: int) -> bytes:
    return struct.pack(
        "<IBB2xI",
        parent_id & 0xFFFFFFFF,
        kind & 0xFF,
        depth & 0xFF,
        gauss_index & 0xFFFFFFFF,
    )


def _build_parent_and_csr(
    root: MergeTreeNode, total_nodes: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    parent = np.full(total_nodes, ROOT_PARENT, dtype=np.int32)
    children_lists: list[list[int]] = [[] for _ in range(total_nodes)]

    def walk(n: MergeTreeNode) -> None:
        nid = n.node_id
        for c in n.children:
            parent[c.node_id] = nid
            children_lists[nid].append(c.node_id)
            walk(c)

    walk(root)
    for lst in children_lists:
        lst.sort()

    csr_off = np.zeros(total_nodes + 1, dtype=np.uint32)
    flat: list[int] = []
    pos = 0
    for i in range(total_nodes):
        csr_off[i] = pos
        for c in children_lists[i]:
            flat.append(c)
            pos += 1
    csr_off[total_nodes] = pos
    flat_arr = np.array(flat, dtype=np.uint32) if flat else np.zeros(0, dtype=np.uint32)
    return parent, csr_off, flat_arr


def write_merge_m2lt(
    out_path: Path,
    root: MergeTreeNode,
    lod_levels: int,
    buckets: list[list[MergeTreeNode]],
) -> None:
    total_nodes = sum(len(b) for b in buckets)
    parent, csr_off, flat_children = _build_parent_and_csr(root, total_nodes)
    csr_child_count = int(csr_off[-1])

    hdr = struct.pack(
        "<4s8I",
        M2LT_MAGIC,
        M2LT_VERSION,
        0,
        lod_levels,
        total_nodes,
        csr_child_count,
        NODE_RECORD_BYTES,
        M2LT_FLAGS_KDTREE_MERGE,
        HEADER_BYTES,
    )
    file_header = hdr + b"\x00" * (HEADER_BYTES - len(hdr))

    nodes_blob = bytearray()
    for depth_nodes in buckets:
        for node in depth_nodes:
            pid = int(parent[node.node_id])
            nodes_blob.extend(
                _pack_node_record(pid, KIND_GAUSSIAN, node.depth, node.gauss_index)
            )

    if len(nodes_blob) != total_nodes * NODE_RECORD_BYTES:
        raise RuntimeError("internal: wrong nodes blob size")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        f.write(file_header)
        f.write(nodes_blob)
        f.write(csr_off.tobytes())
        f.write(flat_children.tobytes())
