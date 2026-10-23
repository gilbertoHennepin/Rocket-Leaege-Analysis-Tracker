"""Command line entry point: ``rltracker``."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .discovery import REPLAY_DIR_ENV, default_replay_dir, find_replays
from .export import export_match
from .models import Match, Team
from .parser import DEFAULT_FPS, ReplayParseError, parse_replay


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


def _cmd_parse(args: argparse.Namespace) -> int:
    replays = _resolve_replays(args)
    if not replays:
        return 1
    failures = 0
    for path in replays:
        try:
            match = parse_replay(path, fps=args.fps)
        except ReplayParseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            failures += 1
            continue
        print(format_match(match))
        if args.export:
            out = export_match(match, Path(args.export) / path.stem, fmt=args.format)
            print(f"\n  Exported to {out}")
        print()
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rltracker", description="Rocket League replay analysis.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List replays in your replay folder")
    p_list.add_argument("--dir", type=Path, help="Replay folder (defaults to the game's Demos folder)")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=_cmd_list)

    p_parse = sub.add_parser("parse", help="Parse replays and print a match summary")
    p_parse.add_argument("replays", nargs="*", help="Replay files. Defaults to your most recent replay.")
    p_parse.add_argument("--dir", type=Path, help="Replay folder to pick the latest replays from")
    p_parse.add_argument("--latest", type=int, default=1, help="How many recent replays to parse")
    p_parse.add_argument("--fps", type=float, default=DEFAULT_FPS, help="Frames per second to sample")
    p_parse.add_argument("--export", type=Path, help="Write tables for each match into this folder")
    p_parse.add_argument("--format", choices=["parquet", "csv"], default="parquet")
    p_parse.set_defaults(func=_cmd_parse)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
