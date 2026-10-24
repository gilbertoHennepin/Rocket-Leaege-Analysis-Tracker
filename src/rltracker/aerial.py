"""Aerial flight analysis: every boosted aerial measured like a flight test.

In the air a Rocket League car is a small rocket. Boost is a fixed-thrust
engine pointed along the car's nose, gravity pulls it down, and air roll,
pitch and yaw steer the thrust vector. That makes aerials a guidance
problem, and this module measures them with the vocabulary of flight
testing and missile guidance:

* **Delta-v budget**: the velocity change the boost that was burned can buy.
* **Guidance command**: the constant acceleration that would carry the car
  from where it is to the point where it met the ball, at the moment it met
  it. This is the zero-effort-miss (ZEM) law: ``a_cmd = 2 * ZEM / t_go**2``,
  where ZEM is how far the car would miss if it coasted from now on.
* **Thrust alignment**: how closely the nose followed that command while
  boosting, as the mean cosine of the aim error. Thrust off the command is
  cosine loss, boost spent fighting your own trajectory.
* **Ballistic miss**: the ZEM at takeoff. How far the car would have missed
  with no boost at all, which shows how much of the aerial was the jump and
  how much was the engine.
* **Miss distance**: for aerials that never touched the ball, the closest
  approach to it.

Replays update car states at roughly 10-15 Hz, so the default 10 fps
sampling already captures all of the information there is.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .field import CEILING_HEIGHT, FIELD_HALF_LENGTH, FIELD_HALF_WIDTH
from .models import Match
from .rotation import nose_vectors

# Game physics, as measured by the Rocket League bot community (RLBot wiki).
GRAVITY = np.array([0.0, 0.0, -650.0])  # uu/s^2
BOOST_ACCEL = 991.667  # uu/s^2 along the nose while boosting
BOOST_PER_SECOND = 33.3  # boost (0-100 scale) burned per second
THRUST_TO_WEIGHT = BOOST_ACCEL / -GRAVITY[2]  # about 1.53

# What counts as a boosted aerial.
AIRBORNE_CLEARANCE = 120.0  # uu from the nearest floor, wall or ceiling
MIN_DURATION = 0.6  # s
MIN_APEX = 350.0  # uu; a plain jump peaks around 230
MIN_BOOST = 5.0  # boost used in the air
MAX_MISS = 1000.0  # uu; flights that never get this close to the ball aren't going for it

# Guidance measurements skip the jump at the start and the last instant before
# contact, where t_go -> 0 and the command blows up.
SKIP_AFTER_TAKEOFF = 0.2  # s
SKIP_BEFORE_INTERCEPT = 0.25  # s
TOUCH_GRACE = 0.15  # s after landing a touch still counts
CONTACT_DISTANCE = 150.0  # car center to ball center at contact, roughly

# Aim error bands, used by the flight cards and the coach.
ON_TARGET_DEG = 20.0
OFF_TARGET_DEG = 45.0


@dataclass(frozen=True)
class Aerial:
    """One boosted aerial. Times are match seconds, distances uu, boost 0-100."""

    player_id: str
    start: float  # takeoff
    end: float  # landing, or the end of the flight
    touch_time: float | None  # first touch in the air, if any
    apex: float
    boost_used: float
    thrust_alignment: float | None  # 0-1, mean cos(aim error) while boosting
    aim_error: float | None  # median aim error while boosting, degrees
    ballistic_miss: float | None  # ZEM at takeoff, touches only
    miss_distance: float | None  # closest approach to the ball, misses only
    intercept: tuple[float, float, float]  # where the car met (or came closest to) the ball
    # Per-frame flight data for the flight cards.
    frames: pd.DataFrame = field(repr=False, compare=False)

    @property
    def touched(self) -> bool:
        return self.touch_time is not None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def delta_v(self) -> float:
        """Velocity change the boost burned could provide, uu/s."""
        return self.boost_used / BOOST_PER_SECOND * BOOST_ACCEL

    @property
    def wasted_boost(self) -> float | None:
        """Boost lost to cosine loss: thrust that didn't push along the command."""
        if self.thrust_alignment is None:
            return None
        return self.boost_used * (1.0 - self.thrust_alignment)


def _angle_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(a, axis=-1) * np.linalg.norm(b, axis=-1)
    cos = np.sum(a * b, axis=-1) / np.maximum(norms, 1e-9)
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def surface_clearance(pos: np.ndarray) -> np.ndarray:
    """Distance from each position to the nearest flat surface: floor, side or back wall, ceiling."""
    x, y, z = pos[:, 0], pos[:, 1], pos[:, 2]
    return np.minimum.reduce([z, FIELD_HALF_WIDTH - np.abs(x), FIELD_HALF_LENGTH - np.abs(y), CEILING_HEIGHT - z])


def boosting_frames(boost: np.ndarray) -> np.ndarray:
    """Frames where the car is boosting, inferred from its boost going down.

    Replays update the boost amount in bursts, so a frame that repeats the
    previous value is still boosting if the next update shows a drop.
    """
    on = np.zeros(len(boost), dtype=bool)
    last = 0
    for k in range(1, len(boost)):
        if boost[k] != boost[last]:
            if boost[k] < boost[last] - 0.5:
                on[last + 1 : k + 1] = True
            last = k
    return on


def guidance_command(
    pos: np.ndarray, vel: np.ndarray, t_go: np.ndarray, target: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Zero-effort miss and the constant acceleration that cancels it in t_go seconds.

    ``ZEM = target - (pos + vel * t_go + g * t_go**2 / 2)`` is where the car
    would miss if it coasted from here. ``a_cmd = 2 * ZEM / t_go**2`` closes
    that miss exactly at t_go.
    """
    t = np.asarray(t_go, dtype=float)[..., None]
    zem = target - (pos + vel * t + 0.5 * GRAVITY * t**2)
    return zem, 2.0 * zem / np.maximum(t, 1e-3) ** 2


def _segments(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive (start, end) index pairs of runs of True."""
    padded = np.concatenate([[False], mask, [False]]).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return [(int(s), int(e) - 1) for s, e in zip(edges[::2], edges[1::2], strict=True)]


def find_aerials(match: Match, player_id: str | None = None) -> list[Aerial]:
    """All boosted aerials in live play, in time order, for one player or everyone."""
    players = [match.player(player_id)] if player_id else match.players
    ball = match.ball.set_index("frame")[["x", "y", "z"]]
    out: list[Aerial] = []
    for player in players:
        cars = match.cars[match.cars["player_id"] == player.id].sort_values("frame").reset_index(drop=True)
        if cars.empty:
            continue
        touches = sorted(
            (t for t in match.touches if t.player_id == player.id and t.tags.get("surface") == "air"),
            key=lambda t: t.time,
        )
        out.extend(_player_aerials(player.id, cars, ball, touches))
    return sorted(out, key=lambda a: a.start)


def _player_aerials(player_id: str, cars: pd.DataFrame, ball: pd.DataFrame, touches: list) -> list[Aerial]:
    t = cars["time"].to_numpy()
    pos = cars[["x", "y", "z"]].to_numpy()
    vel = cars[["vx", "vy", "vz"]].to_numpy()
    boost = cars["boost"].to_numpy()
    nose = nose_vectors(cars["rot_x"], cars["rot_y"], cars["rot_z"])
    on = boosting_frames(boost)
    airborne = (surface_clearance(pos) > AIRBORNE_CLEARANCE) & cars["live"].to_numpy()
    ball_pos = ball.reindex(cars["frame"]).to_numpy()

    aerials = []
    for s, e in _segments(airborne):
        if t[e] - t[s] < MIN_DURATION or pos[s : e + 1, 2].max() < MIN_APEX:
            continue
        used = float(max(0.0, boost[s] - boost[s : e + 1].min()))
        if used < MIN_BOOST:
            continue

        k = np.arange(s, e + 1)
        touch = next((x for x in touches if t[s] <= x.time <= t[e] + TOUCH_GRACE), None)
        gap = np.linalg.norm(ball_pos[k] - pos[k], axis=1)
        if touch is not None:
            # Where this car was at the touch (the touch event's own player
            # position is unreliable), interpolated between frames.
            target_time = touch.time
            target = np.array([np.interp(target_time, t[k], pos[k, axis]) for axis in range(3)])
        else:
            # A miss: judge it against where the ball was at the closest approach.
            closest = int(np.nanargmin(gap))
            if gap[closest] - CONTACT_DISTANCE > MAX_MISS:
                continue  # flying to a wall, a recovery or a demo, not an aerial at the ball
            target_time, target = t[k[closest]], ball_pos[k[closest]]

        t_go = target_time - t[k]
        _, command = guidance_command(pos[k], vel[k], t_go, target)
        aim = _angle_deg(nose[k], command)
        window = (t[k] > t[s] + SKIP_AFTER_TAKEOFF) & (t_go > SKIP_BEFORE_INTERCEPT)
        measured = window & on[k]
        alignment = aim_median = None
        if measured.any():
            alignment = float(np.mean(np.maximum(np.cos(np.radians(aim[measured])), 0.0)))
            aim_median = float(np.median(aim[measured]))

        ballistic = miss = None
        if touch is not None:
            zem, _ = guidance_command(pos[s], vel[s], target_time - t[s], target)
            ballistic = float(np.linalg.norm(zem))
        else:
            miss = float(max(0.0, np.nanmin(gap) - CONTACT_DISTANCE))

        frames = pd.DataFrame(
            {
                "time": t[k],
                "x": pos[k, 0],
                "y": pos[k, 1],
                "z": pos[k, 2],
                "speed": np.linalg.norm(vel[k], axis=1),
                "nose_x": nose[k, 0],
                "nose_y": nose[k, 1],
                "nose_z": nose[k, 2],
                "boosting": on[k],
                "aim_error": np.where(window, aim, np.nan),
                "ball_x": ball_pos[k, 0],
                "ball_y": ball_pos[k, 1],
                "ball_z": ball_pos[k, 2],
            }
        )
        aerials.append(
            Aerial(
                player_id=player_id,
                start=float(t[s]),
                end=float(t[e]),
                touch_time=float(touch.time) if touch is not None else None,
                apex=float(pos[k, 2].max()),
                boost_used=used,
                thrust_alignment=alignment,
                aim_error=aim_median,
                ballistic_miss=ballistic,
                miss_distance=miss,
                intercept=(float(target[0]), float(target[1]), float(target[2])),
                frames=frames,
            )
        )
    return aerials


def aerial_summary(match: Match, aerials: list[Aerial] | None = None) -> pd.DataFrame:
    """One row per player: how often they go up, how often they connect, and how well they steer."""
    aerials = find_aerials(match) if aerials is None else aerials
    rows = []
    for player in match.players:
        mine = [a for a in aerials if a.player_id == player.id]
        aligned = [a for a in mine if a.thrust_alignment is not None]
        weights = np.array([a.boost_used for a in aligned])
        alignment = (
            float(np.average([a.thrust_alignment for a in aligned], weights=weights)) if aligned else float("nan")
        )
        touched = [a for a in mine if a.touched]
        misses = [a for a in mine if not a.touched]
        rows.append(
            {
                "player": player.name,
                "team": player.team.label,
                "aerials": len(mine),
                "touches": len(touched),
                "touch_rate": 100.0 * len(touched) / len(mine) if mine else float("nan"),
                "boost_per_aerial": float(np.mean([a.boost_used for a in mine])) if mine else float("nan"),
                "thrust_alignment": 100.0 * alignment,
                "wasted_boost": float(sum(a.wasted_boost for a in aligned)),
                "median_apex": float(np.median([a.apex for a in mine])) if mine else float("nan"),
                "near_misses": sum(1 for a in misses if a.miss_distance is not None and a.miss_distance < 200),
            }
        )
    return pd.DataFrame(rows).set_index("player")
