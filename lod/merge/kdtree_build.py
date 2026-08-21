"""Build KD-tree merge LOD hierarchy (see merge_rules.md, tree_file_format.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from gaussian_merge import merge_gaussians
from gaussian_ply import BrdfGaussian
from progress_util import iter_progress


@dataclass
class MergeTreeNode:
    depth: int = 0
    children: list[MergeTreeNode] = field(default_factory=list)
    parent: Optional[MergeTreeNode] = None
    source_index: Optional[int] = None
    gaussian: Optional[BrdfGaussian] = None
    node_id: int = -1
    gauss_index: int = -1


def _build_kdtree_recursive(
    indices: np.ndarray,
    positions: np.ndarray,
    depth: int,
    axis: int,
) -> MergeTreeNode:
    node = MergeTreeNode(depth=depth)
    n = int(indices.shape[0])
    if n == 1:
        node.source_index = int(indices[0])
        return node

    coords = positions[indices, axis]
    order = np.argsort(coords, kind="mergesort")
    sorted_idx = indices[order]
    mid = n // 2
    left_idx = sorted_idx[:mid]
    right_idx = sorted_idx[mid:]
    if left_idx.size == 0 or right_idx.size == 0:
        left_idx = sorted_idx[:1]
        right_idx = sorted_idx[1:]

    left = _build_kdtree_recursive(left_idx, positions, depth + 1, (axis + 1) % 3)
    right = _build_kdtree_recursive(right_idx, positions, depth + 1, (axis + 1) % 3)
    left.parent = node
    right.parent = node
    node.children = [left, right]
    return node


def build_kdtree(positions: np.ndarray) -> MergeTreeNode:
    n = positions.shape[0]
    if n == 0:
        raise ValueError("cannot build KD-tree from empty point set")
    indices = np.arange(n, dtype=np.int64)
    return _build_kdtree_recursive(indices, positions, depth=0, axis=0)


def _max_leaf_depth(node: MergeTreeNode) -> int:
    if not node.children:
        return node.depth
    return max(_max_leaf_depth(c) for c in node.children)


def _extend_leaf_to_depth(node: MergeTreeNode, target_depth: int) -> None:
    current = node
    while current.depth < target_depth:
        child = MergeTreeNode(depth=current.depth + 1, source_index=current.source_index)
        child.parent = current
        current.children = [child]
        current = child


def pad_leaf_depths(root: MergeTreeNode) -> int:
    target = _max_leaf_depth(root)

    def walk(n: MergeTreeNode) -> None:
        if not n.children:
            _extend_leaf_to_depth(n, target)
        else:
            for c in n.children:
                walk(c)

    walk(root)
    return target


def _subtree_min_source(node: MergeTreeNode) -> int:
    if node.source_index is not None:
        return node.source_index
    return min(_subtree_min_source(c) for c in node.children)


def collect_nodes_by_depth(root: MergeTreeNode, max_depth: int) -> list[list[MergeTreeNode]]:
    buckets: list[list[MergeTreeNode]] = [[] for _ in range(max_depth + 1)]

    def walk(n: MergeTreeNode) -> None:
        buckets[n.depth].append(n)
        for c in n.children:
            walk(c)

    walk(root)
    for d in range(max_depth + 1):
        if d == max_depth:
            buckets[d].sort(key=lambda n: n.source_index if n.source_index is not None else 0)
        else:
            buckets[d].sort(key=_subtree_min_source)
    return buckets


def assign_node_ids(buckets: list[list[MergeTreeNode]]) -> int:
    nid = 0
    for depth_nodes in buckets:
        for gi, node in enumerate(depth_nodes):
            node.node_id = nid
            node.gauss_index = gi
            nid += 1
    return nid


def _post_order_nodes(root: MergeTreeNode) -> list[MergeTreeNode]:
    out: list[MergeTreeNode] = []

    def walk(n: MergeTreeNode) -> None:
        for c in n.children:
            walk(c)
        out.append(n)

    walk(root)
    return out


def merge_up(
    root: MergeTreeNode,
    source_gaussians: list[BrdfGaussian],
    *,
    progress: bool = False,
) -> None:
    nodes = _post_order_nodes(root)
    for n in iter_progress(
        nodes,
        total=len(nodes),
        desc="Merge Gaussians",
        enabled=progress,
        unit="node",
    ):
        if n.children:
            child_gs = [c.gaussian for c in n.children]
            if any(g is None for g in child_gs):
                raise RuntimeError("child Gaussian missing before merge")
            n.gaussian = merge_gaussians(child_gs)
        else:
            if n.source_index is None:
                raise RuntimeError("leaf without source_index")
            n.gaussian = source_gaussians[n.source_index].copy()

    if root.gaussian is None:
        raise RuntimeError("root Gaussian missing after merge")


def build_merge_tree(
    source_gaussians: list[BrdfGaussian],
    *,
    progress: bool = False,
) -> tuple[MergeTreeNode, int, list[list[MergeTreeNode]]]:
    positions = np.stack([g.position for g in source_gaussians], axis=0)
    root = build_kdtree(positions)
    max_depth = pad_leaf_depths(root)
    merge_up(root, source_gaussians, progress=progress)
    buckets = collect_nodes_by_depth(root, max_depth)
    assign_node_ids(buckets)
    return root, max_depth, buckets
