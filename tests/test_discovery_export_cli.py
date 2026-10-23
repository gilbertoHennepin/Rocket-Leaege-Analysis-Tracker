import json
import os
import shutil

import pandas as pd
import pytest

from rltracker.cli import main
from rltracker.discovery import REPLAY_DIR_ENV, candidate_replay_dirs, find_replays
from rltracker.export import export_match
from tests.conftest import FIXTURES


@pytest.fixture
def replay_dir(tmp_path):
    folder = tmp_path / "Demos"
    folder.mkdir()
    for i, name in enumerate(["duel", "doubles"]):
        target = folder / f"{name}.replay"
        shutil.copy(FIXTURES / f"{name}.replay", target)
        os.utime(target, (1_700_000_000 + i, 1_700_000_000 + i))
    return folder


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(REPLAY_DIR_ENV, str(tmp_path))
    assert candidate_replay_dirs() == [tmp_path]


def test_default_dirs_point_at_demos(monkeypatch):
    monkeypatch.delenv(REPLAY_DIR_ENV, raising=False)
    dirs = candidate_replay_dirs()
    assert dirs
    assert all(d.parts[-3:] == ("Rocket League", "TAGame", "Demos") for d in dirs)


def test_find_replays_newest_first(replay_dir):
    assert [p.stem for p in find_replays(replay_dir)] == ["doubles", "duel"]
    assert find_replays(replay_dir / "missing") == []


@pytest.mark.parametrize("fmt", ["parquet", "csv"])
def test_export_round_trip(doubles, tmp_path, fmt):
    out = export_match(doubles, tmp_path / "m", fmt=fmt)
    summary = json.loads((out / "match.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "2v2"
    assert summary["score"] == {"blue": 6, "orange": 2}

    read = pd.read_parquet if fmt == "parquet" else pd.read_csv
    cars = read(out / f"cars.{fmt}")
    goals = read(out / f"goals.{fmt}")
    assert len(cars) == len(doubles.cars)
    assert len(goals) == 8
    assert {"ball_position_x", "ball_position_y", "ball_position_z"} <= set(goals.columns)
    touches = read(out / f"touches.{fmt}")
    assert "tags_kind" in touches.columns


def test_cli_list(replay_dir, capsys):
    assert main(["list", "--dir", str(replay_dir)]) == 0
    out = capsys.readouterr().out
    assert "2 replays" in out
    assert out.index("doubles.replay") < out.index("duel.replay")


def test_cli_parse_latest_and_export(replay_dir, tmp_path, capsys):
    assert main(["parse", "--dir", str(replay_dir), "--export", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "Blue 6 - 2 Orange" in out
    assert "timehake" in out
    assert (tmp_path / "out" / "doubles" / "cars.parquet").exists()


def test_cli_parse_reports_errors(tmp_path, capsys):
    assert main(["parse", str(tmp_path / "missing.replay")]) == 1
    assert "not found" in capsys.readouterr().err


def test_cli_stats(tmp_path, capsys):
    replay = str(FIXTURES / "doubles.replay")
    assert main(["stats", replay, "--csv", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Time under 25 boost" in out
    assert "Last back on team" in out
    assert (tmp_path / "doubles-stats.csv").exists()


def test_cli_stats_hides_team_roles_in_1v1(capsys):
    assert main(["stats", str(FIXTURES / "duel.replay")]) == 0
    assert "Last back" not in capsys.readouterr().out


def test_cli_heatmap(tmp_path, capsys):
    assert main(["heatmap", str(FIXTURES / "duel.replay"), "--out", str(tmp_path)]) == 0
    assert "saved 5 images" in capsys.readouterr().out
    assert (tmp_path / "duel" / "overview.png").exists()
