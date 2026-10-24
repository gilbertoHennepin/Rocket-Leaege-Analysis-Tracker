"""Export a match for the Unreal Engine 3D replay viewer (``viewer/``).

The format is a single JSON file. Per-frame data is stored as flat number
arrays (``[x0, y0, z0, x1, y1, z1, ...]``) to keep files small and parsing
fast. Positions are Unreal units and rotations are quaternions in Unreal's
coordinate system, which Rocket League also uses (it is built on Unreal
Engine 3), so the viewer can apply them without any axis conversion.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .coach import coach
from .models import Match

FORMAT = "rltracker-viewer"
VERSION = 1


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


def _flat(values: np.ndarray, decimals: int) -> list[float]:
    values = np.nan_to_num(np.asarray(values, dtype=float), nan=0.0)
    return np.round(values, decimals).ravel().tolist()


def _body(table: pd.DataFrame) -> dict[str, list[float]]:
    return {
        "pos": _flat(table[["x", "y", "z"]].to_numpy(), 1),
        "rot": _flat(euler_to_quat(table["rot_x"], table["rot_y"], table["rot_z"]), 4),
    }


def viewer_data(match: Match) -> dict:
    """Build the viewer JSON document for a match."""
    ball = match.ball
    cars = []
    for player in match.players:
        frames = match.cars[match.cars["player_id"] == player.id].sort_values("frame")
        cars.append({**_body(frames), "boost": _flat(frames["boost"].to_numpy(), 0)})

    moments = []
    for report in coach(match).values():
        for insight in report.insights:
            for m in insight.moments:
                moments.append(
                    {
                        "time": round(m.time, 2),
                        "clock": match.game_time_label(m.time),
                        "playerId": m.player_id,
                        "kind": m.kind,
                        "rule": insight.rule,
                        "title": insight.title,
                        "severity": insight.severity.label,
                        "detail": m.detail,
                        "punished": m.punished,
                    }
                )
    moments.sort(key=lambda m: m["time"])

    return {
        "format": FORMAT,
        "version": VERSION,
        "source": match.source.name,
        "mode": match.mode,
        "playlist": match.playlist,
        "score": {team.label.lower(): goals for team, goals in match.score.items()},
        "fps": match.fps,
        "recordedBy": match.recorded_by,
        "players": [{"id": p.id, "name": p.name, "team": int(p.team), "car": p.car} for p in match.players],
        "frames": {
            "time": _flat(ball["time"].to_numpy(), 3),
            "clock": _flat(ball["clock"].to_numpy(), 0),
            "live": ball["live"].astype(int).tolist(),
            "ball": _body(ball),
            "cars": cars,
        },
        "goals": [
            {
                "time": round(g.time, 2),
                "team": int(g.team),
                "scorerId": g.scorer_id,
                "clock": match.game_time_label(g.time),
            }
            for g in match.goals
        ],
        "touches": [
            {"time": round(t.time, 2), "playerId": t.player_id, "pos": [round(v, 1) for v in t.ball_position]}
            for t in match.touches
        ],
        "moments": moments,
    }


def export_viewer(match: Match, out_dir: str | Path) -> Path:
    """Write ``<replay name>.rlview.json`` into ``out_dir`` and return its path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{match.source.stem}.rlview.json"
    path.write_text(json.dumps(viewer_data(match), separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return path
