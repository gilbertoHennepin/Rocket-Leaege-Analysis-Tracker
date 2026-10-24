"""Typed data model for a parsed Rocket League match."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

Vec3 = tuple[float, float, float]


class Team(IntEnum):
    BLUE = 0
    ORANGE = 1

    @property
    def label(self) -> str:
        return self.name.capitalize()

    @property
    def opponent(self) -> Team:
        return Team(1 - self.value)


@dataclass(frozen=True)
class Player:
    """A player in the match, with their end-of-game scoreboard line."""

    id: str
    name: str
    team: Team
    platform: str
    car: str | None = None
    score: int = 0
    goals: int = 0
    assists: int = 0
    saves: int = 0
    shots: int = 0
    is_bot: bool = False


@dataclass(frozen=True)
class Goal:
    time: float
    replay_frame: int  # raw frame index in the replay, not the sampled ``frame``
    team: Team
    scorer_id: str | None
    ball_position: Vec3
    ball_speed: float
    buildup: str | None = None


@dataclass(frozen=True)
class Touch:
    time: float
    replay_frame: int
    player_id: str
    team: Team
    ball_position: Vec3
    player_position: Vec3
    ball_speed_change: float
    role: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BoostPickup:
    time: float
    replay_frame: int
    player_id: str
    team: Team
    pad_type: str  # "big" or "small"
    amount: float  # boost gained, on the 0-100 scale
    is_steal: bool
    position: Vec3


@dataclass
class Match:
    """Everything parsed out of one replay file.

    ``ball`` has one row per sampled frame. ``cars`` is in long format, with
    one row per (frame, player), which makes per-player filtering and heat
    maps simple. Both share the ``frame`` and ``time`` columns, where ``time``
    is seconds since the start of the replay. ``phase`` is the gameplay
    phase (``active_play``, ``kickoff_countdown``, ``post_goal``...) and
    ``live`` is True while the ball is in play.
    """

    source: Path
    played_at: datetime | None
    playlist: str | None
    team_size: int
    duration: float
    score: dict[Team, int]
    players: list[Player]
    ball: pd.DataFrame
    cars: pd.DataFrame
    goals: list[Goal]
    touches: list[Touch]
    boost_pickups: list[BoostPickup]
    fps: float
    # The full analysis event stream from subtr-actor (rotation roles,
    # possession, 50/50s and so on), kept for the analysis steps.
    raw_events: list[dict[str, Any]] = field(default_factory=list, repr=False)
    # Id of the player whose game saved this replay, when it can be told.
    recorded_by: str | None = None

    @property
    def mode(self) -> str:
        return f"{self.team_size}v{self.team_size}"

    @property
    def winner(self) -> Team | None:
        blue, orange = self.score[Team.BLUE], self.score[Team.ORANGE]
        if blue == orange:
            return None
        return Team.BLUE if blue > orange else Team.ORANGE

    def team(self, team: Team) -> list[Player]:
        return [p for p in self.players if p.team == team]

    def player(self, name_or_id: str) -> Player:
        """Look up a player by id, or by name (case-insensitive)."""
        for p in self.players:
            if p.id == name_or_id:
                return p
        lowered = name_or_id.casefold()
        for p in self.players:
            if p.name.casefold() == lowered:
                return p
        raise KeyError(f"No player named {name_or_id!r} in this match")

    def clock_at(self, time: float) -> float:
        """Seconds left on the game clock at a replay ``time``."""
        times = self.ball["time"].to_numpy()
        i = int(np.clip(np.searchsorted(times, time), 0, len(times) - 1))
        return float(self.ball["clock"].iloc[i])

    def game_time_label(self, time: float) -> str:
        """Game clock at ``time`` as shown in game, like "3:41", or "OT" in overtime."""
        clock = self.clock_at(time)
        if clock <= 0:
            return "OT"
        minutes, seconds = divmod(int(round(clock)), 60)
        return f"{minutes}:{seconds:02d}"

    def player_frames(self, name_or_id: str, live_only: bool = False) -> pd.DataFrame:
        """Car rows for a single player, optionally only during live play."""
        player = self.player(name_or_id)
        rows = self.cars["player_id"] == player.id
        if live_only:
            rows &= self.cars["live"]
        return self.cars[rows].reset_index(drop=True)
