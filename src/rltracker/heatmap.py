"""Heat maps and touch maps drawn on a top-down field diagram.

The field is drawn landscape, with goal-to-goal running left to right.
Player heat maps are always oriented so that player attacks to the right,
which makes maps from blue and orange games directly comparable. Ball and
touch maps use the real orientation: blue defends the left goal.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # render to files, no display needed

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, to_rgb  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Circle, Polygon  # noqa: E402

from .field import FIELD_HALF_LENGTH, FIELD_HALF_WIDTH, GOAL_DEPTH, GOAL_HALF_WIDTH  # noqa: E402
from .models import Match, Player, Team  # noqa: E402

# Dark theme. Team hues are the first two slots of the categorical palette,
# which conveniently are blue and orange.
SURFACE = "#1a1a19"
FIELD_FILL = "#222221"
FIELD_LINE = "#4a4945"
TEXT_PRIMARY = "#f0efec"
TEXT_SECONDARY = "#c3c2b7"
TEAM_COLORS = {Team.BLUE: "#3987e5", Team.ORANGE: "#d95926"}
BALL_COLOR = "#c98500"

# Player names can be in any script. Fall back to whichever CJK-capable font
# is installed so names like "少 Nix" render instead of showing boxes.
_FONT_FALLBACKS = ["Noto Sans CJK JP", "Noto Sans CJK SC", "Microsoft YaHei", "Yu Gothic", "Malgun Gothic", "MS Gothic"]
_installed = {f.name for f in font_manager.fontManager.ttflist}
FONT_FAMILY = ["DejaVu Sans", *[f for f in _FONT_FALLBACKS if f in _installed]]

CORNER_CUT = 1152.0
BIG_PADS = [(-3072, -4096), (3072, -4096), (-3584, 0), (3584, 0), (-3072, 4096), (3072, 4096)]
SMALL_PADS = [
    (0, -4240), (-1792, -4184), (1792, -4184), (-940, -3308), (940, -3308), (0, -2816),
    (-3584, -2484), (3584, -2484), (-1788, -2300), (1788, -2300), (-2048, -1036), (0, -1024),
    (2048, -1036), (-1024, 0), (1024, 0), (-2048, 1036), (0, 1024), (2048, 1036),
    (-1788, 2300), (1788, 2300), (-3584, 2484), (3584, 2484), (0, 2816), (-940, 3310),
    (940, 3308), (-1792, 4184), (1792, 4184), (0, 4240),
]  # fmt: skip

BIN_SIZE = 100.0  # uu per heat map cell
SMOOTHING = 2.0  # gaussian sigma, in cells


# Field drawing -------------------------------------------------------------


def _to_plot(x: np.ndarray, y: np.ndarray, flip: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Game (x, y) -> plot (horizontal, vertical). ``flip`` rotates 180 degrees."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if flip:
        x, y = -x, -y
    return y, x


def _field_polygon() -> np.ndarray:
    """Outline of the playable floor plus both goal mouths, in plot coordinates."""
    L, W, c = FIELD_HALF_LENGTH, FIELD_HALF_WIDTH, CORNER_CUT
    g, d = GOAL_HALF_WIDTH, GOAL_DEPTH
    # Walk the outline in plot space: horizontal = game y, vertical = game x.
    return np.array(
        [
            (-L + c, -W), (L - c, -W), (L, -W + c),
            (L, -g), (L + d, -g), (L + d, g), (L, g),
            (L, W - c), (L - c, W), (-L + c, W), (-L, W - c),
            (-L, g), (-L - d, g), (-L - d, -g), (-L, -g),
            (-L, -W + c),
        ]
    )  # fmt: skip


def draw_field(ax: Axes, show_pads: bool = True, flip: bool = False) -> Polygon:
    """Draw an empty field on ``ax`` and return its outline (for clipping).

    Goals are colored by the team defending them: blue on the left, or on
    the right when ``flip`` is set (an orange player's attacking-right view).
    """
    ax.set_facecolor(SURFACE)
    outline = Polygon(_field_polygon(), closed=True, facecolor=FIELD_FILL, edgecolor=FIELD_LINE, lw=1.5, zorder=0)
    ax.add_patch(outline)
    ax.plot([0, 0], [-FIELD_HALF_WIDTH, FIELD_HALF_WIDTH], color=FIELD_LINE, lw=1, zorder=1)
    ax.add_patch(Circle((0, 0), 1000, fill=False, edgecolor=FIELD_LINE, lw=1, zorder=1))
    for side, team in ((-1, Team.BLUE), (1, Team.ORANGE)):
        x = side * FIELD_HALF_LENGTH * (-1 if flip else 1)
        ax.plot([x, x], [-GOAL_HALF_WIDTH, GOAL_HALF_WIDTH], color=TEAM_COLORS[team], lw=3, zorder=3)
    if show_pads:
        bx, by = _to_plot(*zip(*BIG_PADS, strict=True))
        sx, sy = _to_plot(*zip(*SMALL_PADS, strict=True))
        ax.scatter(bx, by, s=40, facecolor="none", edgecolor=FIELD_LINE, lw=1.2, zorder=2)
        ax.scatter(sx, sy, s=8, color=FIELD_LINE, zorder=2)
    pad = GOAL_DEPTH + 150
    ax.set_xlim(-FIELD_HALF_LENGTH - pad, FIELD_HALF_LENGTH + pad)
    ax.set_ylim(-FIELD_HALF_WIDTH - 150, FIELD_HALF_WIDTH + 150)
    ax.set_aspect("equal")
    ax.axis("off")
    return outline


# Density -------------------------------------------------------------------


def _gaussian_blur(grid: np.ndarray, sigma: float) -> np.ndarray:
    radius = int(3 * sigma)
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k /= k.sum()
    grid = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 0, grid)
    return np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, grid)


def density(h: np.ndarray, v: np.ndarray, bin_size: float = BIN_SIZE, sigma: float = SMOOTHING) -> np.ndarray:
    """Smoothed share of time per cell over the plot extent, normalized to sum 1.

    Returned with rows = vertical axis, so it can go straight to ``imshow``.
    """
    h_edges = np.arange(-FIELD_HALF_LENGTH - GOAL_DEPTH, FIELD_HALF_LENGTH + GOAL_DEPTH + bin_size, bin_size)
    v_edges = np.arange(-FIELD_HALF_WIDTH, FIELD_HALF_WIDTH + bin_size, bin_size)
    grid, _, _ = np.histogram2d(v, h, bins=[v_edges, h_edges])
    if grid.sum() == 0:
        return grid
    grid = _gaussian_blur(grid, sigma)
    return grid / grid.sum()


def sequential_cmap(color: str, name: str = "seq") -> LinearSegmentedColormap:
    """One-hue ramp that starts transparent (zero) and brightens with density."""
    r, g, b = to_rgb(color)
    light = tuple(c + (1 - c) * 0.75 for c in (r, g, b))
    colors = [(r, g, b, 0.0), (r, g, b, 0.55), (r, g, b, 0.9), (*light, 1.0)]
    return LinearSegmentedColormap.from_list(name, colors)


def _draw_density(ax: Axes, outline: Polygon, h: np.ndarray, v: np.ndarray, color: str) -> None:
    grid = density(h, v)
    if not grid.any():
        return
    extent = (-FIELD_HALF_LENGTH - GOAL_DEPTH, FIELD_HALF_LENGTH + GOAL_DEPTH, -FIELD_HALF_WIDTH, FIELD_HALF_WIDTH)
    # Cap the scale at a high percentile so a few hot spots (like kickoff
    # positions) don't wash out the rest of the map.
    vmax = np.percentile(grid[grid > 0], 99.5)
    image = ax.imshow(
        grid, origin="lower", extent=extent, cmap=sequential_cmap(color), vmin=0, vmax=vmax, interpolation="bilinear"
    )
    image.set_zorder(1.5)
    image.set_clip_path(outline)


def _themed(func):
    """Create a plot's text with the fallback font list active."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with plt.rc_context({"font.family": FONT_FAMILY}):
            return func(*args, **kwargs)

    return wrapper


def _title(ax: Axes, title: str, subtitle: str) -> None:
    ax.text(0, 1.06, title, transform=ax.transAxes, color=TEXT_PRIMARY, fontsize=13, fontweight="bold")
    ax.text(0, 1.0, subtitle, transform=ax.transAxes, color=TEXT_SECONDARY, fontsize=9.5, va="bottom")


def _new_axes(ax: Axes | None) -> tuple[Figure, Axes]:
    if ax is not None:
        return ax.figure, ax
    fig, ax = plt.subplots(figsize=(9, 6.2), facecolor=SURFACE)
    return fig, ax


# Public plots ----------------------------------------------------------------


@_themed
def player_heatmap(match: Match, player: Player | str, ax: Axes | None = None) -> Figure:
    """Where a player's car spent its time, oriented so they attack to the right."""
    player = player if isinstance(player, Player) else match.player(player)
    fig, ax = _new_axes(ax)
    flip = player.team == Team.ORANGE
    outline = draw_field(ax, flip=flip)
    frames = match.player_frames(player.id, live_only=True)
    h, v = _to_plot(frames["x"], frames["y"], flip=flip)
    _draw_density(ax, outline, h, v, TEAM_COLORS[player.team])
    ax.annotate(
        "", xy=(0.97, -0.02), xytext=(0.83, -0.02), xycoords="axes fraction",
        arrowprops={"arrowstyle": "->", "color": TEXT_SECONDARY, "lw": 1.2},
    )  # fmt: skip
    ax.text(0.82, -0.02, "attacking", transform=ax.transAxes, color=TEXT_SECONDARY, fontsize=9, ha="right", va="center")
    _title(ax, player.name, f"{player.team.label} · car position · live play")
    return fig


@_themed
def ball_heatmap(match: Match, ax: Axes | None = None) -> Figure:
    """Where the ball spent its time. Blue defends the left goal."""
    fig, ax = _new_axes(ax)
    outline = draw_field(ax)
    live = match.ball[match.ball["live"]]
    h, v = _to_plot(live["x"], live["y"])
    _draw_density(ax, outline, h, v, BALL_COLOR)
    _title(ax, "Ball", "ball position · live play · blue defends left")
    return fig


@_themed
def touch_map(match: Match, player: Player | str | None = None, ax: Axes | None = None) -> Figure:
    """Every ball touch, colored by team. Blue defends the left goal."""
    fig, ax = _new_axes(ax)
    draw_field(ax, show_pads=False)
    touches = match.touches
    subtitle = "where the ball was hit · blue defends left"
    title = "Touches"
    if player is not None:
        player = player if isinstance(player, Player) else match.player(player)
        touches = [t for t in touches if t.player_id == player.id]
        title = f"{player.name}: touches"
    for team in Team:
        pts = [t.ball_position for t in touches if t.team == team]
        if not pts:
            continue
        xs, ys, _ = zip(*pts, strict=True)
        h, v = _to_plot(xs, ys)
        ax.scatter(h, v, s=36, color=TEAM_COLORS[team], edgecolor=SURFACE, linewidth=1.5, zorder=4, alpha=0.95)
    handles = [
        Line2D([], [], marker="o", ls="", markersize=8, color=TEAM_COLORS[t], markeredgecolor=SURFACE, label=t.label)
        for t in Team
        if any(x.team == t for x in touches)
    ]
    if handles:
        legend = ax.legend(
            handles=handles, loc="upper right", bbox_to_anchor=(1.0, 1.1), ncol=2, frameon=False, fontsize=9.5
        )
        for text in legend.get_texts():
            text.set_color(TEXT_SECONDARY)
    _title(ax, title, subtitle)
    return fig


@_themed
def overview(match: Match) -> Figure:
    """Ball map, touch map and every player's heat map in one figure."""
    n = 2 + len(match.players)
    cols = 2 if n <= 4 else 4 if n == 8 else 3 if n > 4 else 2
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(6.4 * cols, 4.7 * rows), facecolor=SURFACE)
    axes = np.atleast_1d(axes).ravel()
    ball_heatmap(match, ax=axes[0])
    touch_map(match, ax=axes[1])
    for ax, player in zip(axes[2:], match.players, strict=False):
        player_heatmap(match, player, ax=ax)
    for ax in axes[n:]:
        ax.set_visible(False)
    blue, orange = match.score[Team.BLUE], match.score[Team.ORANGE]
    fig.suptitle(f"{match.mode}  ·  Blue {blue} - {orange} Orange", color=TEXT_PRIMARY, fontsize=16, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.97), h_pad=3.0)
    return fig


def _slug(text: str) -> str:
    return re.sub(r"[^\w]+", "-", text.casefold()).strip("-") or "player"


@_themed
def save_heatmaps(match: Match, out_dir: str | Path, dpi: int = 130) -> list[Path]:
    """Write ball, touch, per-player and overview PNGs. Returns the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    figures = {
        "overview": overview(match),
        "ball": ball_heatmap(match),
        "touches": touch_map(match),
    }
    for player in match.players:
        figures[f"player-{_slug(player.name)}"] = player_heatmap(match, player)

    paths = []
    for name, fig in figures.items():
        path = out / f"{name}.png"
        fig.savefig(path, dpi=dpi, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.3)
        plt.close(fig)
        paths.append(path)
    return paths
