"""Car and ball orientation: quaternions rebuilt from the parser's Euler angles."""

from __future__ import annotations

import numpy as np


def _axis_quat(axis: int, angle: np.ndarray) -> np.ndarray:
    q = np.zeros((len(angle), 4))
    q[:, axis] = np.sin(angle / 2)
    q[:, 3] = np.cos(angle / 2)
    return q


def _quat_mul(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    px, py, pz, pw = p.T
    qx, qy, qz, qw = q.T
    return np.stack(
        [
            pw * qx + px * qw + py * qz - pz * qy,
            pw * qy - px * qz + py * qw + pz * qx,
            pw * qz + px * qy - py * qx + pz * qw,
            pw * qw - px * qx - py * qy - pz * qz,
        ],
        axis=1,
    )


def euler_to_quat(rot_x: np.ndarray, rot_y: np.ndarray, rot_z: np.ndarray) -> np.ndarray:
    """Rebuild replay quaternions (x, y, z, w) from subtr-actor's Euler angles.

    subtr-actor converts each replay quaternion with glam's ``EulerRot::XYZ``,
    i.e. ``q = Rx(rot_x) * Ry(rot_y) * Rz(rot_z)``, so applying the same
    product recovers the original rotation.
    """
    rx = _axis_quat(0, np.asarray(rot_x, dtype=float))
    ry = _axis_quat(1, np.asarray(rot_y, dtype=float))
    rz = _axis_quat(2, np.asarray(rot_z, dtype=float))
    return _quat_mul(_quat_mul(rx, ry), rz)


def rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate vector v by unit quaternions q (N x 4, xyzw)."""
    u, w = q[:, :3], q[:, 3:4]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def nose_vectors(rot_x: np.ndarray, rot_y: np.ndarray, rot_z: np.ndarray) -> np.ndarray:
    """Unit vector out of the car's nose (its local +x), which is also the boost thrust direction."""
    return rotate(euler_to_quat(rot_x, rot_y, rot_z), np.array([1.0, 0.0, 0.0]))
