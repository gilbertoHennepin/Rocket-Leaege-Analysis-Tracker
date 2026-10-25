"""Flight test cards: one figure per aerial, in the style of a flight test report.

Each card has three panels sharing the flight's timeline:

1. **Trajectory**: a side view along the line from takeoff to the intercept,
   with the car's nose (its thrust vector) drawn wherever it was boosting,
   colored by aim error against the guidance command.
2. **Altitude**: car and ball height over time, with boost burns shaded.
3. **Aim error**: the angle between the nose and the guidance command while
   boosting, against the on-target and off-target bands.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from .aerial import OFF_TARGET_DEG, ON_TARGET_DEG, Aerial, find_aerials
from .heatmap import BALL_COLOR, SURFACE, TEAM_COLORS, TEXT_PRIMARY, TEXT_SECONDARY, _slug, _themed
from .models import Match

MUTED = "#898781"
GRID = "#2c2c2a"
BASELINE = "#383835"
# Status palette: aim quality is a state, so it never borrows a series color.
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_CRITICAL = "#d03b3b"
AFTER_TOUCH = 0.6  # s of flight shown after the touch


def _aim_color(error: float) -> str:
    if np.isnan(error):
        return MUTED
    if error <= ON_TARGET_DEG:
        return STATUS_GOOD
    return STATUS_WARNING if error <= OFF_TARGET_DEG else STATUS_CRITICAL


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)
    ax.tick_params(colors=MUTED, labelsize=8.5)
    ax.grid(color=GRID, lw=0.6)
    ax.set_axisbelow(True)


def _label(ax, text: str) -> None:
    ax.set_title(text, loc="left", color=TEXT_SECONDARY, fontsize=10, pad=6)


def _boost_spans(ax, times: np.ndarray, boosting: np.ndarray, color: str) -> None:
    padded = np.concatenate([[False], boosting, [False]]).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    step = np.median(np.diff(times)) if len(times) > 1 else 0.1
    for s, e in zip(edges[::2], edges[1::2], strict=True):
        ax.axvspan(times[s] - step / 2, times[e - 1] + step / 2, color=color, alpha=0.14, lw=0)


@_themed
def flight_card(match: Match, aerial: Aerial) -> Figure:
    """The flight test card for one aerial."""
    player = match.player(aerial.player_id)
    team = TEAM_COLORS[player.team]
    f = aerial.frames
    if aerial.touch_time is not None:
        f = f[f["time"] <= aerial.touch_time + AFTER_TOUCH]  # the fall afterwards isn't part of the aerial
    t = f["time"].to_numpy()
    pos = f[["x", "y", "z"]].to_numpy()
    nose = f[["nose_x", "nose_y", "nose_z"]].to_numpy()
    ball = f[["ball_x", "ball_y", "ball_z"]].to_numpy()
    boosting = f["boosting"].to_numpy()
    aim = f["aim_error"].to_numpy()
    intercept = np.array(aerial.intercept)

    # Side view: horizontal distance along the takeoff -> intercept bearing.
    bearing = (intercept - pos[0])[:2]
    bearing = bearing / max(np.linalg.norm(bearing), 1e-6)

    def along(points: np.ndarray) -> np.ndarray:
        return (points[:, :2] - pos[0, :2]) @ bearing

    fig = plt.figure(figsize=(9, 9.4), facecolor=SURFACE)
    grid = fig.add_gridspec(3, 1, height_ratios=[1.5, 0.8, 0.8], hspace=0.45, left=0.09, right=0.97, top=0.86)
    side, alt, err = (fig.add_subplot(grid[i]) for i in range(3))
    for ax in (side, alt, err):
        _style(ax)

    clock = match.game_time_label(aerial.start)
    outcome = "Touch" if aerial.touched else "No touch"
    fig.text(0.09, 0.955, f"Aerial  ·  {player.name}  ·  {clock}", color=TEXT_PRIMARY, fontsize=14, fontweight="bold")
    stats = [f"{outcome}", f"{aerial.duration:.1f} s", f"apex {aerial.apex:.0f} uu", f"boost {aerial.boost_used:.0f}"]
    stats.append(f"Δv budget {aerial.delta_v:.0f} uu/s")
    fig.text(0.09, 0.928, "  ·  ".join(stats), color=TEXT_SECONDARY, fontsize=9.5)
    guidance = []
    if aerial.thrust_alignment is not None:
        guidance.append(f"thrust alignment {aerial.thrust_alignment:.0%}")
        guidance.append(f"cosine loss {aerial.wasted_boost:.0f} boost")
    if aerial.ballistic_miss is not None:
        guidance.append(f"ballistic miss at takeoff {aerial.ballistic_miss:.0f} uu")
    if aerial.miss_distance is not None:
        guidance.append(f"missed the ball by {aerial.miss_distance:.0f} uu")
    fig.text(0.09, 0.905, "  ·  ".join(guidance), color=TEXT_SECONDARY, fontsize=9.5)

    # 1. Trajectory -------------------------------------------------------
    h, h_ball = along(pos), along(ball)
    side.axhline(0, color=BASELINE, lw=1.2)
    side.plot(h_ball, ball[:, 2], color=BALL_COLOR, lw=1.5, ls=(0, (4, 3)), label="ball")
    side.plot(h, pos[:, 2], color=team, lw=2, label=player.name)
    arrow = 220.0
    for k in np.flatnonzero(boosting):
        dh, dz = nose[k, :2] @ bearing, nose[k, 2]
        side.annotate(
            "",
            xy=(h[k] + dh * arrow, pos[k, 2] + dz * arrow),
            xytext=(h[k], pos[k, 2]),
            arrowprops={"arrowstyle": "-|>", "color": _aim_color(aim[k]), "lw": 1.6, "mutation_scale": 9},
        )
    side.scatter([h[0]], [pos[0, 2]], s=40, color=team, edgecolor=SURFACE, lw=2, zorder=4)
    side.annotate("takeoff", (h[0], pos[0, 2]), xytext=(6, -12), textcoords="offset points", color=MUTED, fontsize=8)
    target_h = (intercept[:2] - pos[0, :2]) @ bearing
    side.scatter([target_h], [intercept[2]], s=90, facecolor="none", edgecolor=TEXT_PRIMARY, lw=1.6, zorder=5)
    label = "intercept" if aerial.touched else "closest approach"
    side.annotate(
        label, (target_h, intercept[2]), xytext=(8, 6), textcoords="offset points", color=TEXT_PRIMARY, fontsize=8.5
    )
    side.set_aspect("equal", adjustable="datalim")
    side.set_xlabel("distance along flight path (uu)", color=MUTED, fontsize=8.5)
    side.set_ylabel("altitude (uu)", color=MUTED, fontsize=8.5)
    _label(side, "Trajectory, side view. Arrows show the nose (thrust) while boosting")
    handles = [
        Line2D([], [], color=team, lw=2, label=player.name),
        Line2D([], [], color=BALL_COLOR, lw=1.5, ls=(0, (4, 3)), label="ball"),
        Line2D([], [], color=STATUS_GOOD, lw=2, marker=">", label=f"on target (≤{ON_TARGET_DEG:.0f}°)"),
        Line2D([], [], color=STATUS_WARNING, lw=2, marker=">", label=f"drifting (≤{OFF_TARGET_DEG:.0f}°)"),
        Line2D([], [], color=STATUS_CRITICAL, lw=2, marker=">", label=f"off target (>{OFF_TARGET_DEG:.0f}°)"),
        Line2D([], [], color=MUTED, lw=2, marker=">", label="not measured (jump, final instant)"),
    ]
    legend = side.legend(handles=handles, loc="lower right", fontsize=8, frameon=False, labelcolor=TEXT_SECONDARY)
    legend.set_zorder(6)

    # 2. Altitude ---------------------------------------------------------
    _boost_spans(alt, t, boosting, team)
    alt.plot(t, ball[:, 2], color=BALL_COLOR, lw=1.5, ls=(0, (4, 3)))
    alt.plot(t, pos[:, 2], color=team, lw=2)
    if aerial.touch_time is not None:
        alt.axvline(aerial.touch_time, color=TEXT_SECONDARY, lw=1, ls=":")
    alt.set_ylim(bottom=0)
    alt.set_ylabel("altitude (uu)", color=MUTED, fontsize=8.5)
    _label(alt, "Altitude (car solid, ball dashed). Shaded: boosting")

    # 3. Aim error --------------------------------------------------------
    for level in (ON_TARGET_DEG, OFF_TARGET_DEG):
        err.axhline(level, color=BASELINE, lw=1)
        err.annotate(
            f"{level:.0f}°",
            (t[-1], level),
            xytext=(4, 0),
            textcoords="offset points",
            color=MUTED,
            fontsize=8,
            va="center",
        )
    err.plot(t, aim, color=MUTED, lw=1)
    shown = boosting & ~np.isnan(aim)
    err.scatter(t[shown], aim[shown], s=36, c=[_aim_color(a) for a in aim[shown]], edgecolor=SURFACE, lw=1.5, zorder=3)
    if aerial.touch_time is not None:
        err.axvline(aerial.touch_time, color=TEXT_SECONDARY, lw=1, ls=":")
    err.set_ylim(0, 180)
    err.set_yticks([0, 45, 90, 135, 180])
    err.set_xlabel("match time (s)", color=MUTED, fontsize=8.5)
    err.set_ylabel("aim error (°)", color=MUTED, fontsize=8.5)
    _label(err, "Aim error: nose vs. guidance command (dots: boosting)")
    for ax in (alt, err):
        ax.set_xlim(t[0], t[-1])
    return fig


def save_flight_cards(match: Match, out_dir: str | Path, player: str | None = None, dpi: int = 130) -> list[Path]:
    """Write one flight card PNG per aerial. Returns the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for aerial in find_aerials(match, match.player(player).id if player else None):
        name = match.player(aerial.player_id).name
        clock = match.game_time_label(aerial.start).replace(":", "-")
        path = out / f"aerial-{_slug(name)}-{clock}-{aerial.start:.0f}s.png"
        fig = flight_card(match, aerial)
        fig.savefig(path, dpi=dpi, facecolor=SURFACE)
        plt.close(fig)
        paths.append(path)
    return paths
