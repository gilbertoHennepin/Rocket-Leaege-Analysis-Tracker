import json

import numpy as np
import pytest

from rltracker.cli import main
from rltracker.viewer_export import FORMAT, VERSION, euler_to_quat, export_viewer, viewer_data
from tests.conftest import FIXTURES


def _rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate vector v by unit quaternions q (N x 4, xyzw)."""
    u, w = q[:, :3], q[:, 3:4]
    t = 2 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def test_euler_to_quat_identity_and_yaw():
    q = euler_to_quat(np.zeros(1), np.zeros(1), np.zeros(1))
    assert q[0] == pytest.approx([0, 0, 0, 1])

    # A quarter turn about z points the car's nose (+x) along +y.
    q = euler_to_quat(np.zeros(1), np.zeros(1), np.array([np.pi / 2]))
    assert _rotate(q, np.array([1.0, 0, 0]))[0] == pytest.approx([0, 1, 0], abs=1e-9)


def test_viewer_data_shape(all_matches):
    for match in all_matches:
        data = viewer_data(match)
        assert data["format"] == FORMAT
        assert data["version"] == VERSION
        assert data["mode"] == match.mode

        frames = data["frames"]
        n = len(frames["time"])
        assert n > 0
        assert frames["time"] == sorted(frames["time"])
        assert len(frames["clock"]) == len(frames["live"]) == n
        assert len(frames["ball"]["pos"]) == 3 * n
        assert len(frames["ball"]["rot"]) == 4 * n

        # One car track per player, in player order, the same length as the ball's.
        assert len(frames["cars"]) == len(data["players"]) == len(match.players)
        for car in frames["cars"]:
            assert len(car["pos"]) == 3 * n
            assert len(car["rot"]) == 4 * n
            assert len(car["boost"]) == n
            assert all(0 <= b <= 100 for b in car["boost"])

        ids = {p["id"] for p in data["players"]}
        assert data["recordedBy"] in ids
        assert len(data["goals"]) == sum(data["score"].values())
        times = [m["time"] for m in data["moments"]]
        assert times == sorted(times)
        assert all(m["playerId"] in ids for m in data["moments"])
        assert all(m["severity"] in {"low", "medium", "high"} for m in data["moments"])


def test_car_rotations_face_the_direction_of_travel(doubles):
    """The exported quaternions must point the car's nose where it is driving."""
    cars = doubles.cars
    fast = cars[(cars["speed"] > 1000) & (cars["z"] < 30) & cars["live"]]
    q = euler_to_quat(fast["rot_x"], fast["rot_y"], fast["rot_z"])
    forward = _rotate(q, np.array([1.0, 0, 0]))
    velocity = fast[["vx", "vy", "vz"]].to_numpy()
    velocity = velocity / np.linalg.norm(velocity, axis=1, keepdims=True)
    alignment = np.sum(forward * velocity, axis=1)
    assert np.median(alignment) > 0.95


def test_export_viewer_writes_compact_json(duel, tmp_path):
    path = export_viewer(duel, tmp_path / "Replays")
    assert path.name == "duel.rlview.json"
    text = path.read_text(encoding="utf-8")
    assert ", " not in text[:200]  # compact separators keep files small
    assert json.loads(text)["format"] == FORMAT


def test_cli_viewer_export(tmp_path, capsys):
    out = tmp_path / "Replays"
    assert main(["viewer-export", str(FIXTURES / "standard.replay"), "--out", str(out)]) == 0
    assert (out / "standard.rlview.json").exists()
    assert "standard.rlview.json" in capsys.readouterr().out
