"""Turn a .replay file into a :class:`~rltracker.models.Match`.

Decoding the binary replay format is handled by subtr-actor
(https://github.com/rlrml/subtr-actor). This module reshapes its output
into tidy pandas tables and typed event lists.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import subtr_actor

from .field import raw_boost_to_percent
from .models import BoostPickup, Goal, Match, Player, Team, Touch, Vec3

DEFAULT_FPS = 10.0

GLOBAL_FEATURES = ["BallRigidBody", "CurrentTime", "SecondsRemaining"]
PLAYER_FEATURES = ["PlayerRigidBody", "PlayerBoost", "PlayerAnyJump"]

# subtr-actor header text -> our column name.
_COLUMN_NAMES = {
    "position x": "x",
    "position y": "y",
    "position z": "z",
    "rotation x": "rot_x",
    "rotation y": "rot_y",
    "rotation z": "rot_z",
    "linear velocity x": "vx",
    "linear velocity y": "vy",
    "linear velocity z": "vz",
    "angular velocity x": "ang_vx",
    "angular velocity y": "ang_vy",
    "angular velocity z": "ang_vz",
    "boost level (raw replay units)": "boost",
    "any_jump_active": "jumping",
    "current time": "time",
    "seconds remaining": "clock",
}


class ReplayParseError(Exception):
    """Raised when a replay file cannot be read or decoded."""


def parse_replay(path: str | Path, fps: float = DEFAULT_FPS) -> Match:
    """Parse a replay file into a :class:`Match`.

    ``fps`` sets how many frames per second are sampled into the ``ball`` and
    ``cars`` tables. Events (goals, touches, boost pickups) keep their exact
    times regardless of ``fps``.
    """
    path = Path(path)
    if not path.is_file():
        raise ReplayParseError(f"Replay not found: {path}")

    try:
        meta, array = subtr_actor.get_ndarray_with_info_from_replay_filepath(
            str(path),
            global_feature_adders=GLOBAL_FEATURES,
            player_feature_adders=PLAYER_FEATURES,
            fps=fps,
            dtype="float32",
        )
        # The timeline carries the same event stream as get_stats_events, plus
        # the gameplay phase of each sample, so one call gives us both.
        timeline = subtr_actor.get_stats_timeline(str(path), frame_step_seconds=1.0 / fps)
    except Exception as exc:  # the bindings raise plain exceptions
        raise ReplayParseError(f"Could not parse {path.name}: {exc}") from exc

    replay_meta = meta["replay_meta"]
    headers = dict(replay_meta["all_headers"])
    players = _build_players(replay_meta)
    ids_by_remote = {_remote_key(p_meta): p.id for p_meta, p in zip(_player_metas(replay_meta), players, strict=True)}

    events = timeline["events"]["events"]
    ball, cars = _build_frames(array, meta["column_headers"], players)
    _add_phases(ball, cars, timeline["frames"])
    goals = _build_goals(events, headers, ids_by_remote, players)
    team_size = int(headers.get("TeamSize") or max(len(replay_meta["team_zero"]), len(replay_meta["team_one"])))

    return Match(
        source=path,
        played_at=_parse_epoch(headers.get("MatchStartEpoch")),
        playlist=replay_meta.get("game_type", {}).get("game_type"),
        team_size=team_size,
        duration=float(headers.get("TotalSecondsPlayed") or (ball["time"].iloc[-1] - ball["time"].iloc[0])),
        score={
            Team.BLUE: int(headers.get("Team0Score") or 0),
            Team.ORANGE: int(headers.get("Team1Score") or 0),
        },
        players=players,
        ball=ball,
        cars=cars,
        goals=goals,
        touches=_build_touches(events, ids_by_remote),
        boost_pickups=_build_boost_pickups(events, ids_by_remote),
        fps=fps,
        raw_events=events,
    )


# Players ---------------------------------------------------------------------


def _player_metas(replay_meta: dict[str, Any]) -> list[dict[str, Any]]:
    # This order matches the order of player blocks in the ndarray.
    return list(replay_meta["team_zero"]) + list(replay_meta["team_one"])


def _remote_key(player_meta: dict[str, Any]) -> str:
    return repr(player_meta.get("remote_id"))


def _player_id(remote_id: Any, name: str) -> tuple[str, str]:
    """Return a stable ``platform:id`` string and the platform name."""
    if isinstance(remote_id, dict) and len(remote_id) == 1:
        platform, value = next(iter(remote_id.items()))
        if isinstance(value, dict):
            value = value.get("online_id") or value.get("id") or value.get("name") or name
        if value not in (None, "", 0, "0"):
            return f"{platform.lower()}:{value}", platform
        return f"{platform.lower()}:{_slug(name)}", platform
    return f"local:{_slug(name)}", "Unknown"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "player"


def _build_players(replay_meta: dict[str, Any]) -> list[Player]:
    players: list[Player] = []
    used: set[str] = set()
    for team, metas in ((Team.BLUE, replay_meta["team_zero"]), (Team.ORANGE, replay_meta["team_one"])):
        for p_meta in metas:
            name = p_meta.get("name") or "Unknown"
            player_id, platform = _player_id(p_meta.get("remote_id"), name)
            if player_id in used:  # split-screen guests, bots
                player_id = f"{player_id}#{len(used)}"
            used.add(player_id)
            stats = p_meta.get("stats") or {}
            players.append(
                Player(
                    id=player_id,
                    name=name,
                    team=team,
                    platform=platform,
                    car=p_meta.get("car_hitbox_family"),
                    score=int(stats.get("Score", 0)),
                    goals=int(stats.get("Goals", 0)),
                    assists=int(stats.get("Assists", 0)),
                    saves=int(stats.get("Saves", 0)),
                    shots=int(stats.get("Shots", 0)),
                    is_bot=bool(stats.get("bBot", False)),
                )
            )
    return players


# Frames ----------------------------------------------------------------------


def _column(header: str) -> str:
    header = header.removeprefix("Ball - ")
    return _COLUMN_NAMES.get(header, _slug(header).replace("-", "_"))


def _build_frames(
    array: np.ndarray, headers: dict[str, list[str]], players: list[Player]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    global_cols = [_column(h) for h in headers["global_headers"]]
    player_cols = [_column(h) for h in headers["player_headers"]]
    n_global = len(global_cols)
    n_player = len(player_cols)
    expected = n_global + n_player * len(players)
    if array.shape[1] != expected:
        raise ReplayParseError(f"Unexpected frame layout: {array.shape[1]} columns, expected {expected}")

    ball = pd.DataFrame(array[:, :n_global].astype("float64"), columns=global_cols)
    ball.insert(0, "frame", np.arange(len(ball)))
    ball["speed"] = np.sqrt(ball["vx"] ** 2 + ball["vy"] ** 2 + ball["vz"] ** 2)

    blocks = []
    for i, player in enumerate(players):
        start = n_global + i * n_player
        block = pd.DataFrame(array[:, start : start + n_player].astype("float64"), columns=player_cols)
        block.insert(0, "player_id", player.id)
        block.insert(1, "team", int(player.team))
        block.insert(0, "time", ball["time"].to_numpy())
        block.insert(0, "frame", ball["frame"].to_numpy())
        blocks.append(block)
    cars = pd.concat(blocks, ignore_index=True)
    cars["boost"] = raw_boost_to_percent(cars["boost"])
    cars["speed"] = np.sqrt(cars["vx"] ** 2 + cars["vy"] ** 2 + cars["vz"] ** 2)
    if "jumping" in cars:
        cars["jumping"] = cars["jumping"] > 0.5
    cars["player_id"] = cars["player_id"].astype("category")
    return ball, cars


# Kickoffs (cars racing to a stationary ball) are left out of "live" play:
# they pile up at the same spots every time and are analyzed separately.
LIVE_PHASES = frozenset({"active_play"})


def _add_phases(ball: pd.DataFrame, cars: pd.DataFrame, timeline_frames: list[dict[str, Any]]) -> None:
    """Tag each sampled frame with its gameplay phase and whether play is live.

    Live play excludes kickoff countdowns and the pause after each goal, which
    would otherwise pile up fake "positions" at kickoff spots and in the net.
    """
    if not timeline_frames:
        ball["phase"] = "active_play"
    else:
        phases = pd.DataFrame(
            {
                "time": [float(f["time"]) for f in timeline_frames],
                "phase": [f.get("gameplay_phase") or "unknown" for f in timeline_frames],
            }
        ).sort_values("time")
        ball["phase"] = pd.merge_asof(ball[["time"]], phases, on="time", direction="nearest")["phase"].to_numpy()
    ball["live"] = ball["phase"].isin(LIVE_PHASES)
    by_frame = ball.set_index("frame")
    cars["phase"] = by_frame["phase"].reindex(cars["frame"]).to_numpy()
    cars["live"] = by_frame["live"].reindex(cars["frame"]).to_numpy()


# Events ----------------------------------------------------------------------


def _stream(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in events if e["meta"]["stream"] == name]


def _vec(value: Any) -> Vec3:
    if isinstance(value, dict):
        return (float(value["x"]), float(value["y"]), float(value["z"]))
    if value is None:
        return (float("nan"),) * 3
    x, y, z = value
    return (float(x), float(y), float(z))


def _lookup(ids_by_remote: dict[str, str], remote_id: Any) -> str | None:
    return ids_by_remote.get(repr(remote_id)) if remote_id is not None else None


def _build_goals(
    events: list[dict[str, Any]],
    headers: dict[str, Any],
    ids_by_remote: dict[str, str],
    players: list[Player],
) -> list[Goal]:
    goals = []
    for event in _stream(events, "goal_context"):
        payload = event["payload"]["payload"]
        goals.append(
            Goal(
                time=float(event["meta"]["timing"]["time"]),
                replay_frame=int(payload["frame"]),
                team=Team.BLUE if event["meta"].get("team_is_team_0") else Team.ORANGE,
                scorer_id=_lookup(ids_by_remote, payload.get("scorer")),
                ball_position=_vec(payload.get("ball_position")),
                ball_speed=float(payload.get("ball_speed_at_goal") or 0.0),
                buildup=payload.get("goal_buildup"),
            )
        )
    if goals:
        return sorted(goals, key=lambda g: g.time)

    # Older replays may lack goal context events, so fall back to the header.
    by_name = {p.name: p.id for p in players}
    nan3 = (float("nan"),) * 3
    return [
        Goal(
            time=float("nan"),
            replay_frame=int(g["frame"]),
            team=Team(int(g["PlayerTeam"])),
            scorer_id=by_name.get(g.get("PlayerName")),
            ball_position=nan3,
            ball_speed=float("nan"),
        )
        for g in headers.get("Goals") or []
    ]


def _build_touches(events: list[dict[str, Any]], ids_by_remote: dict[str, str]) -> list[Touch]:
    touches = []
    for event in _stream(events, "touch"):
        payload = event["payload"]["payload"]
        player_id = _lookup(ids_by_remote, payload.get("player"))
        if player_id is None:
            continue
        touches.append(
            Touch(
                time=float(payload["time"]),
                replay_frame=int(payload["frame"]),
                player_id=player_id,
                team=Team.BLUE if payload.get("is_team_0") else Team.ORANGE,
                ball_position=_vec(payload.get("ball_position")),
                player_position=_vec(payload.get("player_position")),
                ball_speed_change=float(payload.get("ball_speed_change") or 0.0),
                role=payload.get("role"),
                tags={t["group"]: t["value"] for t in payload.get("tags", [])},
            )
        )
    return sorted(touches, key=lambda t: t.time)


def _build_boost_pickups(events: list[dict[str, Any]], ids_by_remote: dict[str, str]) -> list[BoostPickup]:
    pickups = []
    for event in _stream(events, "boost_pickups"):
        payload = event["payload"]["payload"]
        player_id = _lookup(ids_by_remote, payload.get("player_id"))
        if player_id is None:
            continue
        pickups.append(
            BoostPickup(
                time=float(payload["time"]),
                replay_frame=int(payload["frame"]),
                player_id=player_id,
                team=Team.BLUE if payload.get("is_team_0") else Team.ORANGE,
                pad_type=payload.get("pad_type", "unknown"),
                amount=raw_boost_to_percent(float(payload.get("collected_amount") or 0.0)),
                is_steal=bool(payload.get("is_steal")),
                position=_vec(payload.get("player_position")),
            )
        )
    return sorted(pickups, key=lambda b: b.time)


def _parse_epoch(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
