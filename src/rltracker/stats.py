"""Per-player stats computed from a parsed match.

Everything here is computed from the frame tables and event lists in
:class:`~rltracker.models.Match`, using live play only, so post-goal pauses
and kickoff countdowns don't skew the numbers. Percentages are 0-100.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .field import FIELD_HALF_LENGTH, GOAL_HEIGHT
from .models import Match, Player, Team

# Thresholds, in-game units.
LOW_BOOST = 25.0  # "starving" on boost
FULL_BOOST = 99.0
SUPERSONIC_SPEED = 2200.0  # uu/s
GROUND_HEIGHT = 50.0  # car origin sits at z ~17 when driving on the floor
THIRD = FIELD_HALF_LENGTH * 2 / 3


@dataclass(frozen=True)
class PlayerStats:
    name: str
    team: str
    seconds_played: float

    # Boost
    avg_boost: float
    pct_low_boost: float  # time under LOW_BOOST
    pct_zero_boost: float
    pct_full_boost: float
    boost_collected_per_min: float
    boost_used_per_min: float
    big_pads: int
    small_pads: int
    stolen_big_pads: int  # big pads taken in the opponent's half

    # Positioning (team-relative: "offensive" is always the opponent's end)
    pct_defensive_third: float
    pct_neutral_third: float
    pct_offensive_third: float
    pct_behind_ball: float
    pct_closest_to_ball: float  # closest on their own team
    pct_last_back: float  # furthest back on their own team (0 in 1v1)
    avg_distance_to_ball: float

    # Movement
    avg_speed: float
    pct_supersonic: float
    pct_on_ground: float
    pct_high_air: float  # above crossbar height

    # Ball
    touches: int
    touches_per_min: float
    hard_hits: int
    aerial_touches: int  # touches high in the air, not just off a jump

    # Scoreboard
    score: int
    goals: int
    assists: int
    saves: int
    shots: int


def attack_sign(team: Team | int) -> int:
    """+1 if the team attacks toward positive y (blue), else -1."""
    return 1 if Team(int(team)) == Team.BLUE else -1


def live_frames(match: Match) -> pd.DataFrame:
    """Live car rows joined with ball position and per-frame team context.

    Adds team-relative columns used by the stats and the analysis rules:
    ``rel_y`` (car y, positive toward the opponent's goal), ``ball_rel_y``,
    ``dist_to_ball``, ``closest_on_team`` and ``last_back_on_team``.
    """
    ball = match.ball.loc[match.ball["live"], ["frame", "x", "y", "z"]]
    ball = ball.rename(columns={"x": "ball_x", "y": "ball_y", "z": "ball_z"})
    cars = match.cars[match.cars["live"]].merge(ball, on="frame", how="inner")

    sign = np.where(cars["team"] == int(Team.BLUE), 1.0, -1.0)
    cars["rel_y"] = cars["y"] * sign
    cars["ball_rel_y"] = cars["ball_y"] * sign
    cars["dist_to_ball"] = np.sqrt(
        (cars["x"] - cars["ball_x"]) ** 2 + (cars["y"] - cars["ball_y"]) ** 2 + (cars["z"] - cars["ball_z"]) ** 2
    )
    group = cars.groupby(["frame", "team"], observed=True)
    cars["closest_on_team"] = cars["dist_to_ball"] == group["dist_to_ball"].transform("min")
    cars["last_back_on_team"] = cars["rel_y"] == group["rel_y"].transform("min")
    if match.team_size == 1:
        cars["last_back_on_team"] = False
    return cars


def _pct(mask: pd.Series) -> float:
    return float(mask.mean() * 100) if len(mask) else 0.0


def _boost_used(frames: pd.DataFrame) -> float:
    """Total boost spent: the sum of drops between consecutive live samples."""
    boost = frames["boost"].to_numpy()
    if len(boost) < 2:
        return 0.0
    consecutive = np.diff(frames["frame"].to_numpy()) == 1
    drops = -np.diff(boost)
    return float(drops[consecutive & (drops > 0)].sum())


def _player_stats(match: Match, player: Player, frames: pd.DataFrame) -> PlayerStats:
    seconds = len(frames) / match.fps
    minutes = seconds / 60 if seconds else float("nan")

    pickups = [b for b in match.boost_pickups if b.player_id == player.id]
    sign = attack_sign(player.team)
    stolen = sum(b.pad_type == "big" and b.position[1] * sign > 0 for b in pickups)

    touches = [t for t in match.touches if t.player_id == player.id]
    rel_y = frames["rel_y"]

    return PlayerStats(
        name=player.name,
        team=player.team.label,
        seconds_played=round(seconds, 1),
        avg_boost=float(frames["boost"].mean()),
        pct_low_boost=_pct(frames["boost"] < LOW_BOOST),
        pct_zero_boost=_pct(frames["boost"] < 1),
        pct_full_boost=_pct(frames["boost"] >= FULL_BOOST),
        boost_collected_per_min=sum(b.amount for b in pickups) / minutes,
        boost_used_per_min=_boost_used(frames) / minutes,
        big_pads=sum(b.pad_type == "big" for b in pickups),
        small_pads=sum(b.pad_type == "small" for b in pickups),
        stolen_big_pads=stolen,
        pct_defensive_third=_pct(rel_y < -FIELD_HALF_LENGTH + THIRD),
        pct_neutral_third=_pct(rel_y.abs() <= FIELD_HALF_LENGTH - THIRD),
        pct_offensive_third=_pct(rel_y > FIELD_HALF_LENGTH - THIRD),
        pct_behind_ball=_pct(rel_y < frames["ball_rel_y"]),
        pct_closest_to_ball=_pct(frames["closest_on_team"]),
        pct_last_back=_pct(frames["last_back_on_team"]),
        avg_distance_to_ball=float(frames["dist_to_ball"].mean()),
        avg_speed=float(frames["speed"].mean()),
        pct_supersonic=_pct(frames["speed"] >= SUPERSONIC_SPEED),
        pct_on_ground=_pct(frames["z"] < GROUND_HEIGHT),
        pct_high_air=_pct(frames["z"] > GOAL_HEIGHT),
        touches=len(touches),
        touches_per_min=len(touches) / minutes,
        hard_hits=sum(t.tags.get("kind") == "hard_hit" for t in touches),
        aerial_touches=sum(t.tags.get("height_band") == "high_air" for t in touches),
        score=player.score,
        goals=player.goals,
        assists=player.assists,
        saves=player.saves,
        shots=player.shots,
    )


def player_stats(match: Match) -> list[PlayerStats]:
    frames = live_frames(match)
    by_player = {pid: f for pid, f in frames.groupby("player_id", observed=True)}
    empty = frames.iloc[0:0]
    return [_player_stats(match, p, by_player.get(p.id, empty)) for p in match.players]


def stats_table(match: Match) -> pd.DataFrame:
    """Player stats as a DataFrame indexed by player name."""
    return pd.DataFrame([asdict(s) for s in player_stats(match)]).set_index("name")
