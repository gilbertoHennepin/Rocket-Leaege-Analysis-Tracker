"""Command line entry point: ``rltracker``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

from . import __version__
from .discovery import REPLAY_DIR_ENV, default_replay_dir, find_replays
from .export import export_match
from .models import Match, Team
from .parser import DEFAULT_FPS, ReplayParseError, parse_replay
from .stats import player_stats, stats_table


def _format_clock(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def format_match(match: Match) -> str:
    when = match.played_at.strftime("%Y-%m-%d %H:%M UTC") if match.played_at else "unknown date"
    blue, orange = match.score[Team.BLUE], match.score[Team.ORANGE]
    lines = [
        f"{match.source.name}",
        f"  {match.mode} {match.playlist or ''}  ·  {when}  ·  {_format_clock(match.duration)}".rstrip(),
        f"  Blue {blue} - {orange} Orange",
        "",
        f"  {'Player':<20} {'Team':<7} {'Score':>5} {'G':>3} {'A':>3} {'Sv':>3} {'Sh':>3} {'Touches':>8}",
    ]
    touches: dict[str, int] = {}
    for t in match.touches:
        touches[t.player_id] = touches.get(t.player_id, 0) + 1
    for p in match.players:
        lines.append(
            f"  {p.name[:20]:<20} {p.team.label:<7} {p.score:>5} {p.goals:>3} {p.assists:>3}"
            f" {p.saves:>3} {p.shots:>3} {touches.get(p.id, 0):>8}"
        )
    names = {p.id: p.name for p in match.players}
    if match.goals:
        lines += ["", "  Goals:"]
        for g in match.goals:
            scorer = names.get(g.scorer_id or "", "unknown")
            # Ball speed is in uu/s, and 1 uu is 1 cm.
            lines.append(
                f"    {_format_clock(match.clock_at(g.time))} left  {g.team.label:<7} {scorer}"
                f"  ({g.ball_speed * 0.036:.0f} km/h)"
            )
    return "\n".join(lines)


def _cmd_list(args: argparse.Namespace) -> int:
    directory = args.dir or default_replay_dir()
    if directory is None:
        print(f"No replay folder found. Pass --dir or set {REPLAY_DIR_ENV}.", file=sys.stderr)
        return 1
    replays = find_replays(directory)
    if not replays:
        print(f"No .replay files in {directory}")
        return 0
    print(f"{len(replays)} replays in {directory}")
    for path in replays[: args.limit]:
        modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {modified}  {path.name}")
    return 0


def _resolve_replays(args: argparse.Namespace) -> list[Path]:
    if args.replays:
        return [Path(p) for p in args.replays]
    latest = find_replays(args.dir)[: args.latest]
    if not latest:
        print(f"No replays given and none found. Pass a file, --dir, or set {REPLAY_DIR_ENV}.", file=sys.stderr)
    return latest


def _parsed_matches(args: argparse.Namespace) -> Iterator[Match | None]:
    """Parse each requested replay, yielding None (after reporting) on failure."""
    for path in _resolve_replays(args):
        try:
            yield parse_replay(path, fps=args.fps)
        except ReplayParseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            yield None


def _run_per_match(args: argparse.Namespace, handle: Callable[[Match], None]) -> int:
    seen = failures = 0
    for match in _parsed_matches(args):
        seen += 1
        if match is None:
            failures += 1
            continue
        handle(match)
        print()
    return 1 if failures or not seen else 0


def _cmd_parse(args: argparse.Namespace) -> int:
    def handle(match: Match) -> None:
        print(format_match(match))
        if args.export:
            out = export_match(match, Path(args.export) / match.source.stem, fmt=args.format)
            print(f"\n  Exported to {out}")

    return _run_per_match(args, handle)


# (label, column, format) for the stats table, grouped into sections.
_STAT_ROWS = [
    ("Boost", None, None),
    ("Average boost", "avg_boost", "{:.0f}"),
    ("Time under 25 boost", "pct_low_boost", "{:.0f}%"),
    ("Time on 0 boost", "pct_zero_boost", "{:.0f}%"),
    ("Time at 100 boost", "pct_full_boost", "{:.0f}%"),
    ("Boost collected / min", "boost_collected_per_min", "{:.0f}"),
    ("Boost used / min", "boost_used_per_min", "{:.0f}"),
    ("Big / small pads", ("big_pads", "small_pads"), "{} / {}"),
    ("Stolen big pads", "stolen_big_pads", "{}"),
    ("Positioning", None, None),
    ("Defensive third", "pct_defensive_third", "{:.0f}%"),
    ("Neutral third", "pct_neutral_third", "{:.0f}%"),
    ("Offensive third", "pct_offensive_third", "{:.0f}%"),
    ("Behind the ball", "pct_behind_ball", "{:.0f}%"),
    ("Closest to ball on team", "pct_closest_to_ball", "{:.0f}%"),
    ("Last back on team", "pct_last_back", "{:.0f}%"),
    ("Avg distance to ball", "avg_distance_to_ball", "{:.0f}"),
    ("Movement", None, None),
    ("Average speed", "avg_speed", "{:.0f}"),
    ("Supersonic", "pct_supersonic", "{:.0f}%"),
    ("On the ground", "pct_on_ground", "{:.0f}%"),
    ("High in the air", "pct_high_air", "{:.0f}%"),
    ("Ball", None, None),
    ("Touches", "touches", "{}"),
    ("Touches / min", "touches_per_min", "{:.1f}"),
    ("Hard hits", "hard_hits", "{}"),
    ("Aerial touches", "aerial_touches", "{}"),
]


def format_stats(match: Match) -> str:
    stats = player_stats(match)
    if match.team_size == 1:
        rows = [r for r in _STAT_ROWS if r[1] not in ("pct_closest_to_ball", "pct_last_back")]
    else:
        rows = _STAT_ROWS
    width = 12
    header = f"  {'':<26}" + "".join(f"{s.name[: width - 1]:>{width}}" for s in stats)
    teams = f"  {'':<26}" + "".join(f"{s.team:>{width}}" for s in stats)
    lines = [f"{match.source.name}  ({match.mode}, live play only)", header, teams]
    for label, column, fmt in rows:
        if column is None:
            lines.append(f"  {label}")
            continue
        cells = []
        for s in stats:
            values = [getattr(s, c) for c in column] if isinstance(column, tuple) else [getattr(s, column)]
            cells.append(f"{fmt.format(*values):>{width}}")
        lines.append(f"    {label:<24}" + "".join(cells))
    return "\n".join(lines)


def _cmd_stats(args: argparse.Namespace) -> int:
    def handle(match: Match) -> None:
        print(format_stats(match))
        if args.csv:
            out = Path(args.csv) / f"{match.source.stem}-stats.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            stats_table(match).to_csv(out)
            print(f"\n  Saved {out}")

    return _run_per_match(args, handle)


def _cmd_heatmap(args: argparse.Namespace) -> int:
    from .heatmap import save_heatmaps  # matplotlib is slow to import

    def handle(match: Match) -> None:
        paths = save_heatmaps(match, Path(args.out) / match.source.stem)
        print(f"{match.source.name}: saved {len(paths)} images to {paths[0].parent}")

    return _run_per_match(args, handle)


def _add_replay_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("replays", nargs="*", help="Replay files. Defaults to your most recent replay.")
    parser.add_argument("--dir", type=Path, help="Replay folder to pick the latest replays from")
    parser.add_argument("--latest", type=int, default=1, help="How many recent replays to use")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help="Frames per second to sample")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rltracker", description="Rocket League replay analysis.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List replays in your replay folder")
    p_list.add_argument("--dir", type=Path, help="Replay folder (defaults to the game's Demos folder)")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=_cmd_list)

    p_parse = sub.add_parser("parse", help="Parse replays and print a match summary")
    _add_replay_args(p_parse)
    p_parse.add_argument("--export", type=Path, help="Write tables for each match into this folder")
    p_parse.add_argument("--format", choices=["parquet", "csv"], default="parquet")
    p_parse.set_defaults(func=_cmd_parse)

    p_stats = sub.add_parser("stats", help="Per-player boost, positioning and movement stats")
    _add_replay_args(p_stats)
    p_stats.add_argument("--csv", type=Path, help="Also save the stats as CSV in this folder")
    p_stats.set_defaults(func=_cmd_stats)

    p_heat = sub.add_parser("heatmap", help="Save heat maps and touch maps as PNG images")
    _add_replay_args(p_heat)
    p_heat.add_argument("--out", type=Path, default=Path("heatmaps"), help="Output folder (default: ./heatmaps)")
    p_heat.set_defaults(func=_cmd_heatmap)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
