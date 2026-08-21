"""Merge BRDF Gaussians (see merge_rules.md)."""

from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from gaussian_ply import BrdfGaussian

_EPS = 1e-16
_NORMAL_EPS = 1e-8


def sigma_linear(scale_log: np.ndarray) -> np.ndarray:
    return np.exp(scale_log)


def area_proxy_from_sigma(sig: np.ndarray) -> float:
    sx, sy, sz = float(sig[0]), float(sig[1]), float(sig[2])
    return sx * sy + sy * sz + sz * sx


def area_proxy(g: BrdfGaussian) -> float:
    return area_proxy_from_sigma(sigma_linear(g.scale_log))


def _quat_wxyz_to_rotmat(rot_wxyz: np.ndarray) -> np.ndarray:
    q = np.asarray(rot_wxyz, dtype=np.float64)
    n = np.linalg.norm(q)
    if n < 1e-12:
        raise ValueError("degenerate quaternion")
    q = q / n
    quat_xyzw = np.array([q[1], q[2], q[3], q[0]], dtype=np.float64)
    return Rotation.from_quat(quat_xyzw).as_matrix()


def _rotmat_to_quat_wxyz(rmat: np.ndarray) -> np.ndarray:
    quat_xyzw = Rotation.from_matrix(rmat).as_quat()
    q = np.array(
        [quat_xyzw[3], quat_xyzw[0], quat_xyzw[1], quat_xyzw[2]], dtype=np.float64
    )
    n = np.linalg.norm(q)
    if n < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    return q / n


def covariance_matrix(g: BrdfGaussian) -> np.ndarray:
    sig = sigma_linear(g.scale_log)
    rmat = _quat_wxyz_to_rotmat(g.rot_wxyz)
    s2 = sig**2
    return (rmat * s2[np.newaxis, :]) @ rmat.T


def scale_rot_from_covariance(sigma: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    evals, evecs = np.linalg.eigh(sigma)
    evals = np.maximum(evals, _EPS)
    order = np.argsort(evals)[::-1]
    evals = evals[order]
    evecs = evecs[:, order]
    if np.linalg.det(evecs) < 0.0:
        evecs[:, 2] *= -1.0
    sig = np.sqrt(evals)
    scale_log = np.log(sig)
    rot_wxyz = _rotmat_to_quat_wxyz(evecs)
    return scale_log, rot_wxyz


def _clamp01(x: float) -> float:
    return float(np.clip(x, 0.0, 1.0))


def _merge_normal(children: list[BrdfGaussian], alpha: np.ndarray) -> np.ndarray:
    n_acc = np.zeros(3, dtype=np.float64)
    for a, g in zip(alpha, children):
        n_acc += a * g.normal
    norm = np.linalg.norm(n_acc)
    if norm < _NORMAL_EPS:
        best = int(np.argmax(alpha))
        fallback = children[best].normal.copy()
        fn = np.linalg.norm(fallback)
        if fn < _NORMAL_EPS:
            return np.array([0.0, 0.0, 1.0], dtype=np.float64)
        return fallback / fn
    return n_acc / norm


def merge_gaussians(children: list[BrdfGaussian]) -> BrdfGaussian:
    if len(children) == 0:
        raise ValueError("merge_gaussians requires at least one child")
    if len(children) == 1:
        return children[0].copy()

    weights = np.array(
        [g.opacity * area_proxy(g) for g in children], dtype=np.float64
    )
    w_sum = float(weights.sum())
    if w_sum <= 0.0:
        alpha = np.full(len(children), 1.0 / len(children), dtype=np.float64)
    else:
        alpha = weights / w_sum

    position = sum(a * g.position for a, g in zip(alpha, children))
    basecolor = np.clip(
        sum(a * g.basecolor for a, g in zip(alpha, children)), 0.0, 1.0
    )
    metallic = _clamp01(sum(a * g.metallic for a, g in zip(alpha, children)))
    roughness = _clamp01(sum(a * g.roughness for a, g in zip(alpha, children)))
    normal = _merge_normal(children, alpha)

    sigma = np.zeros((3, 3), dtype=np.float64)
    for a, g in zip(alpha, children):
        diff = g.position - position
        cov = covariance_matrix(g)
        sigma += a * (cov + np.outer(diff, diff))

    scale_log, rot_wxyz = scale_rot_from_covariance(sigma)
    a_parent = area_proxy_from_sigma(sigma_linear(scale_log))
    if a_parent <= 0.0:
        opacity = 0.0
    else:
        opacity = _clamp01(w_sum / a_parent)

    return BrdfGaussian(
        position=position,
        normal=normal,
        basecolor=basecolor,
        metallic=metallic,
        roughness=roughness,
        opacity=opacity,
        scale_log=scale_log,
        rot_wxyz=rot_wxyz,
    )
