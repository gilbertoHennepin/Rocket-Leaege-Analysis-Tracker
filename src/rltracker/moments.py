"""Detect specific moments in a match that coaching tips can point to.

Each detector returns :class:`Moment` objects with the replay time, so a
tip like "you double committed 6 times" can list exactly when.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .field import raw_boost_to_percent
from .models import Match, Team
from .stats import LOW_BOOST, live_frames

# A player "commits" to the ball when close to it and driving at it.
COMMIT_DISTANCE = 900.0  # uu
COMMIT_APPROACH_SPEED = 400.0  # uu/s toward the ball
MIN_DOUBLE_COMMIT = 0.3  # seconds both players must be committed
PUNISH_WINDOW = 5.0  # seconds for the opponent to get a shot off
LOW_BOOST_STRETCH = 8.0  # seconds under LOW_BOOST in a row
MIN_WHIFF_CONFIDENCE = 0.6


@dataclass(frozen=True)
class Moment:
    kind: str  # "double_commit", "lost_challenge", "low_boost", "caught_ahead", "conceded"
    player_id: str
    time: float
    duration: float = 0.0
    punished: bool = False  # the opponent got a shot or goal soon after
    detail: str = ""


def _remote(match: Match) -> dict[str, str]:
    """subtr-actor remote id repr -> our player id, built from the touch stream."""
    ids: dict[str, str] = {}
    by_time = {(round(t.time, 4), t.team): t.player_id for t in match.touches}
    for event in match.raw_events:
        if event["meta"]["stream"] != "touch":
            continue
        payload = event["payload"]["payload"]
        team = Team.BLUE if payload.get("is_team_0") else Team.ORANGE
        pid = by_time.get((round(float(payload["time"]), 4), team))
        if pid is not None:
            ids[repr(payload.get("player"))] = pid
    return ids


def _stream(match: Match, name: str) -> list[dict[str, Any]]:
    return [e["payload"]["payload"] for e in match.raw_events if e["meta"]["stream"] == name]


def _opponent_threats(match: Match, team: Team) -> np.ndarray:
    """Times at which ``team``'s opponents took a shot or scored."""
    times = [t.time for t in match.touches if t.team != team and t.tags.get("action") == "shot"]
    times += [g.time for g in match.goals if g.team != team]
    return np.sort(np.array(times, dtype=float))


def _punished(threats: np.ndarray, time: float, window: float = PUNISH_WINDOW) -> bool:
    i = np.searchsorted(threats, time)
    return bool(i < len(threats) and threats[i] - time <= window)


def _episodes(flags: pd.Series, times: pd.Series, max_gap: int = 2) -> list[tuple[int, int]]:
    """Start/end positions of runs of True, merging gaps up to ``max_gap`` samples."""
    idx = np.flatnonzero(flags.to_numpy())
    if not len(idx):
        return []
    breaks = np.flatnonzero(np.diff(idx) > max_gap + 1)
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]]
    return list(zip(starts, ends, strict=True))


def committing(frames: pd.DataFrame) -> pd.Series:
    """True where a car is close to the ball and driving toward it."""
    dx = frames["ball_x"] - frames["x"]
    dy = frames["ball_y"] - frames["y"]
    dz = frames["ball_z"] - frames["z"]
    dist = np.sqrt(dx**2 + dy**2 + dz**2).clip(lower=1.0)
    approach = (frames["vx"] * dx + frames["vy"] * dy + frames["vz"] * dz) / dist
    return (frames["dist_to_ball"] < COMMIT_DISTANCE) & (approach > COMMIT_APPROACH_SPEED)


def double_commits(match: Match, frames: pd.DataFrame | None = None) -> list[Moment]:
    """Two teammates going for the same ball at the same time.

    The moment is charged to whoever was further from the ball when it
    started: they were the one who should have peeled off.
    """
    if match.team_size < 2:
        return []
    frames = live_frames(match) if frames is None else frames
    frames = frames.assign(committing=committing(frames))
    names = {p.id: p.name for p in match.players}
    moments = []
    for team in Team:
        team_frames = frames[frames["team"] == int(team)]
        per_frame = team_frames.groupby("frame")["committing"].sum()
        flags = (per_frame >= 2).reindex(range(int(frames["frame"].max()) + 1), fill_value=False)
        threats = _opponent_threats(match, team)
        for start, end in _episodes(flags, flags.index.to_series()):
            duration = (end - start + 1) / match.fps
            if duration < MIN_DOUBLE_COMMIT:
                continue
            first = team_frames[(team_frames["frame"] == start) & team_frames["committing"]]
            first = first.sort_values("dist_to_ball")
            if len(first) < 2:
                continue
            closer, later = first.iloc[0], first.iloc[-1]
            moments.append(
                Moment(
                    kind="double_commit",
                    player_id=str(later["player_id"]),
                    time=float(later["time"]),
                    duration=duration,
                    punished=_punished(threats, float(later["time"])),
                    detail=f"with {names.get(str(closer['player_id']), 'a teammate')}",
                )
            )
    return moments


def lost_challenges(match: Match, frames: pd.DataFrame | None = None) -> list[Moment]:
    """Challenges a player lost (whiffed, or beaten to the ball) while last back.

    In 1v1 every challenge is a last-man challenge. In team modes we only
    count it when no teammate was behind the player to cover.
    """
    frames = live_frames(match) if frames is None else frames
    ids = _remote(match)
    last_back = frames.set_index(["frame", "player_id"])["last_back_on_team"]
    times = match.ball.loc[match.ball["live"], ["frame", "time"]]
    team_of = {p.id: p.team for p in match.players}

    raw = [(p, "whiffed") for p in _stream(match, "whiff") if p.get("confidence", 0) >= MIN_WHIFF_CONFIDENCE]
    raw += [(p, "beaten to the ball") for p in _stream(match, "beaten_to_ball")]
    moments = []
    for payload, what in raw:
        player_id = ids.get(repr(payload.get("player")))
        time = float(payload.get("time", np.nan))
        if player_id is None or np.isnan(time):
            continue
        i = int(np.clip(np.searchsorted(times["time"].to_numpy(), time), 0, max(len(times) - 1, 0)))
        if not len(times) or abs(float(times["time"].iloc[i]) - time) > 0.2:
            continue  # not during live play
        frame = int(times["frame"].iloc[i])
        if match.team_size > 1 and not bool(last_back.get((frame, player_id), False)):
            continue
        threats = _opponent_threats(match, team_of[player_id])
        punished = _punished(threats, time)
        moments.append(Moment(kind="lost_challenge", player_id=player_id, time=time, punished=punished, detail=what))
    return _dedupe(sorted(moments, key=lambda m: m.time))


def _dedupe(moments: list[Moment], window: float = 3.0) -> list[Moment]:
    """Collapse repeats for the same player within ``window`` seconds (one scramble)."""
    kept: list[Moment] = []
    last: dict[str, Moment] = {}
    for m in moments:
        prev = last.get(m.player_id)
        if prev is not None and m.time - prev.time <= window:
            if m.punished and not prev.punished:
                kept[kept.index(prev)] = m
                last[m.player_id] = m
            continue
        kept.append(m)
        last[m.player_id] = m
    return kept


def low_boost_stretches(match: Match, frames: pd.DataFrame | None = None) -> list[Moment]:
    """Long stretches of live play spent under LOW_BOOST."""
    frames = live_frames(match) if frames is None else frames
    moments = []
    for player_id, pf in frames.groupby("player_id", observed=True):
        pf = pf.sort_values("frame").reset_index(drop=True)
        low = pf["boost"] < LOW_BOOST
        # Split runs at gaps in live play too.
        breaks = (pf["frame"].diff() != 1) | (low != low.shift())
        for _, run in pf[low].groupby(breaks.cumsum()[low]):
            duration = len(run) / match.fps
            if duration >= LOW_BOOST_STRETCH:
                moments.append(
                    Moment(
                        kind="low_boost",
                        player_id=str(player_id),
                        time=float(run["time"].iloc[0]),
                        duration=duration,
                        detail=f"averaging {run['boost'].mean():.0f} boost",
                    )
                )
    return sorted(moments, key=lambda m: m.time)


def caught_ahead(match: Match, frames: pd.DataFrame | None = None) -> list[Moment]:
    """Opponent shots taken while the player was upfield of the ball."""
    frames = live_frames(match) if frames is None else frames
    times = frames[["frame", "time"]].drop_duplicates("frame").sort_values("time")
    moments = []
    for touch in match.touches:
        if touch.tags.get("action") != "shot":
            continue
        i = int(np.clip(np.searchsorted(times["time"].to_numpy(), touch.time), 0, max(len(times) - 1, 0)))
        if not len(times):
            break
        frame = int(times["frame"].iloc[i])
        defenders = frames[(frames["frame"] == frame) & (frames["team"] != int(touch.team))]
        for _, d in defenders.iterrows():
            if d["rel_y"] > d["ball_rel_y"] + 300:
                player_id = str(d["player_id"])
                moments.append(Moment(kind="caught_ahead", player_id=player_id, time=touch.time, punished=True))
    return _dedupe(sorted(moments, key=lambda m: m.time))


def conceded_goals(match: Match) -> list[Moment]:
    """One moment per defender per goal against, describing their situation.

    Positions and boost are taken at the scorer's last touch (the shot), not
    when the ball crossed the line, because by then everyone is upfield of it.
    """
    ids = _remote(match)
    moments = []
    for g in _stream(match, "goal_context"):
        scoring_blue = bool(g.get("scoring_team_is_team_0"))
        shot = g.get("scorer_last_touch") or g
        ball = shot.get("ball_position")
        ball_y = float(ball["y"]) if isinstance(ball, dict) else float("nan")
        for p in shot.get("players", []):
            if bool(p.get("is_team_0")) == scoring_blue:
                continue
            player_id = ids.get(repr(p.get("player")))
            if player_id is None:
                continue
            notes = []
            boost = raw_boost_to_percent(float(p.get("boost_amount") or 0))
            if boost < LOW_BOOST:
                notes.append(f"{boost:.0f} boost")
            if match.team_size > 1 and p.get("is_most_back"):
                notes.append("last back")
            pos = p.get("position")
            if isinstance(pos, dict):
                # The defending team attacks the other way: blue attacks +y.
                sign = -1.0 if scoring_blue else 1.0
                if (float(pos["y"]) - ball_y) * sign > 300:
                    notes.append("ahead of the ball")
            moments.append(
                Moment(
                    kind="conceded",
                    player_id=player_id,
                    time=float(g["time"]),
                    punished=True,
                    detail=", ".join(notes),
                )
            )
    return moments


def all_moments(match: Match) -> list[Moment]:
    frames = live_frames(match)
    moments = (
        double_commits(match, frames)
        + lost_challenges(match, frames)
        + low_boost_stretches(match, frames)
        + caught_ahead(match, frames)
        + conceded_goals(match)
    )
    return sorted(moments, key=lambda m: m.time)
