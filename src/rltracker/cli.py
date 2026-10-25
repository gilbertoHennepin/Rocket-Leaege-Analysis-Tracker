"""Command line entry point: ``rltracker``."""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

from . import __version__
from .aerial import Aerial, aerial_summary, find_aerials
from .coach import Report, coach, report_to_dict
from .dashboard import write_dashboard
from .discovery import REPLAY_DIR_ENV, default_replay_dir, find_replays
from .export import export_match
from .models import Match, Player, Team
from .parser import DEFAULT_FPS, ReplayParseError, parse_replay
from .stats import player_stats, stats_table
from .viewer_export import export_viewer


def _format_clock(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def _when(match: Match, time: float) -> str:
    label = match.game_time_label(time)
    return label if label == "OT" else f"{label} left"


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
                f"    {_when(match, g.time):>9}  {g.team.label:<7} {scorer}  ({g.ball_speed * 0.036:.0f} km/h)"
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


def _cell(value: float, fmt: str) -> str:
    return "-" if value != value else fmt.format(value)  # NaN for players with no aerials


def format_aerials(match: Match, aerials: list[Aerial], flights: bool = False) -> str:
    summary = aerial_summary(match, aerials)
    width = 12
    names = "".join(f"{name[: width - 1]:>{width}}" for name in summary.index)
    teams = "".join(f"{team:>{width}}" for team in summary["team"])
    lines = [f"{match.source.name}  ({match.mode}, boosted aerials in live play)", f"  {'':<28}{names}"]
    lines.append(f"  {'':<28}{teams}")
    rows = [
        ("Aerials", lambda r: f"{r.aerials}"),
        ("Touched the ball", lambda r: f"{r.touches} ({_cell(r.touch_rate, '{:.0f}%')})" if r.aerials else "-"),
        ("Boost per aerial", lambda r: _cell(r.boost_per_aerial, "{:.0f}")),
        ("Thrust alignment", lambda r: _cell(r.thrust_alignment, "{:.0f}%")),
        ("Boost lost to cosine loss", lambda r: f"{r.wasted_boost:.0f}"),
        ("Median apex (uu)", lambda r: _cell(r.median_apex, "{:.0f}")),
        ("Near misses", lambda r: f"{r.near_misses}"),
    ]
    for label, cell in rows:
        lines.append(f"    {label:<26}" + "".join(f"{cell(r):>{width}}" for r in summary.itertuples()))
    if flights and aerials:
        lines += ["", "  Flights:"]
        for a in aerials:
            name = match.player(a.player_id).name
            result = f"touch at {a.intercept[2]:.0f} uu" if a.touched else f"missed by {a.miss_distance:.0f} uu"
            aim = "" if a.thrust_alignment is None else f"  {a.thrust_alignment:.0%} aligned"
            lines.append(
                f"    {match.game_time_label(a.start):>5}  {name[:16]:<16} {result:<20} "
                f"{a.boost_used:>3.0f} boost  apex {a.apex:>4.0f} uu{aim}"
            )
    return "\n".join(lines)


def _cmd_aerials(args: argparse.Namespace) -> int:
    def handle(match: Match) -> None:
        player_id = match.player(args.player).id if args.player else None
        aerials = find_aerials(match, player_id)
        print(format_aerials(match, aerials, flights=args.flights or bool(args.player)))
        if args.cards:
            from .flightcard import save_flight_cards  # matplotlib is slow to import

            paths = save_flight_cards(match, Path(args.cards) / match.source.stem, args.player)
            print(f"\n  Saved {len(paths)} flight cards to {Path(args.cards) / match.source.stem}")

    return _run_per_match(args, handle)


def _cmd_stream(args: argparse.Namespace) -> int:
    from . import telemetry

    impairment = telemetry.Impairment(args.drop, args.duplicate, args.jitter_ms, args.seed)
    matches = [m for m in _parsed_matches(args) if m is not None]
    if not matches:
        return 1
    address = (args.host, args.port)
    try:
        while True:
            for match in matches:
                datagrams = telemetry.impair(telemetry.schedule(match, args.speed), impairment)
                minutes = datagrams[-1][0] / 60 if datagrams else 0
                print(f"Streaming {match.source.name} to udp://{args.host}:{args.port} ({minutes:.1f} min)")
                sent = telemetry.send(datagrams, address)
                print(f"  sent {sent} packets")
            if not args.loop:
                return 0
    except KeyboardInterrupt:
        return 0


def _cmd_listen(args: argparse.Namespace) -> int:
    from . import telemetry

    def show(listener: telemetry.Listener) -> None:
        print(listener.status(), flush=True)

    print(f"Listening on udp://{args.host}:{args.port} (Ctrl+C to stop)")
    try:
        listener = telemetry.listen(args.port, args.seconds, show, host=args.host)
    except KeyboardInterrupt:
        return 0
    return 0 if listener.stats.received else 1


PLAYER_ENV = "RLTRACKER_PLAYER"


def _pick_players(match: Match, args: argparse.Namespace) -> list[Player]:
    """Who to coach: --all, --player, $RLTRACKER_PLAYER, the replay's owner, or everyone."""
    if args.all:
        return list(match.players)
    for name in (args.player, os.environ.get(PLAYER_ENV)):
        if name:
            try:
                return [match.player(name)]
            except KeyError:
                if name == args.player:
                    print(f"  ({name!r} isn't in this match, showing everyone)")
                    return list(match.players)
    if match.recorded_by:
        return [match.player(match.recorded_by)]
    return list(match.players)


def format_report(match: Match, report: Report) -> str:
    p = report.player
    lines = [f"Coaching report: {p.name} ({p.team.label})", ""]
    if report.insights:
        lines.append("  What to work on")
        for n, insight in enumerate(report.insights, 1):
            lines.append(f"  {n}. [{insight.severity.label.upper()}] {insight.title}")
            lines += [f"     {line}" for line in textwrap.wrap(insight.message, 88)]
            lines += [f"     {line}" for line in textwrap.wrap("Tip: " + insight.tip, 88)]
            if insight.moments:
                shown = insight.moments[:6]
                when = ", ".join(m.label(match) for m in shown)
                more = f" and {len(insight.moments) - len(shown)} more" if len(insight.moments) > len(shown) else ""
                lines += [f"     {line}" for line in textwrap.wrap(f"When (clock): {when}{more}", 88)]
            lines.append("")
    else:
        lines += ["  Nothing major to fix in this match. Nice game.", ""]
    if report.strengths:
        lines.append("  Strengths")
        lines += [f"  + {s.title}: {s.message}" for s in report.strengths]
    return "\n".join(lines)


def _cmd_coach(args: argparse.Namespace) -> int:
    def handle(match: Match) -> None:
        reports = coach(match)
        blue, orange = match.score[Team.BLUE], match.score[Team.ORANGE]
        print(f"{match.source.name}  ·  {match.mode} {match.playlist or ''}  ·  Blue {blue} - {orange} Orange\n")
        players = _pick_players(match, args)
        for player in players:
            print(format_report(match, reports[player.id]))
            print()
        if args.json:
            out = Path(args.json) / f"{match.source.stem}-coaching.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            data = [report_to_dict(match, reports[p.id]) for p in match.players]
            out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  Saved {out}")

    return _run_per_match(args, handle)


def _cmd_dashboard(args: argparse.Namespace) -> int:
    matches = [m for m in _parsed_matches(args) if m is not None]
    if not matches:
        return 1
    name = args.player or os.environ.get(PLAYER_ENV)
    path, entries, skipped = write_dashboard(matches, args.out, name, heatmaps=not args.no_heatmaps)
    if not entries:
        print(f"error: {name!r} isn't in any of these matches", file=sys.stderr)
        return 1
    for match in skipped:
        print(f"  Left out {match.source.name}: {name!r} isn't in it")
    print(f"Wrote {path} ({len(entries)} matches). Open it in a browser.")
    return 0


def _cmd_viewer(args: argparse.Namespace) -> int:
    def handle(match: Match) -> None:
        path = export_viewer(match, args.out)
        print(f"{match.source.name}: wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")

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

    p_coach = sub.add_parser("coach", help="Coaching tips: what to work on, with timestamps")
    _add_replay_args(p_coach)
    p_coach.add_argument("--player", help=f"Who to coach (default: ${PLAYER_ENV}, else the replay's owner)")
    p_coach.add_argument("--all", action="store_true", help="Show reports for every player")
    p_coach.add_argument("--json", type=Path, help="Also save every player's report as JSON in this folder")
    p_coach.set_defaults(func=_cmd_coach)

    p_dash = sub.add_parser("dashboard", help="Write a session review page (HTML) for your recent matches")
    _add_replay_args(p_dash)
    p_dash.set_defaults(latest=10)
    p_dash.add_argument("--player", help=f"Whose dashboard (default: ${PLAYER_ENV}, else each replay's owner)")
    p_dash.add_argument("--out", type=Path, default=Path("rltracker-dashboard.html"), help="Output file")
    p_dash.add_argument("--no-heatmaps", action="store_true", help="Leave out heat maps (faster, smaller file)")
    p_dash.set_defaults(func=_cmd_dashboard)

    p_view = sub.add_parser("viewer-export", help="Export matches for the Unreal 3D replay viewer")
    _add_replay_args(p_view)
    p_view.add_argument(
        "--out",
        type=Path,
        default=Path("viewer/RLReplayViewer/Replays"),
        help="Output folder (default: the viewer project's Replays folder)",
    )
    p_view.set_defaults(func=_cmd_viewer)

    p_air = sub.add_parser("aerials", help="Analyze boosted aerials like flight test data")
    _add_replay_args(p_air)
    p_air.add_argument("--player", help="Only this player's aerials (lists each flight)")
    p_air.add_argument("--flights", action="store_true", help="List every aerial, not just the summary")
    p_air.add_argument("--cards", type=Path, help="Save a flight test card PNG per aerial into this folder")
    p_air.set_defaults(func=_cmd_aerials)

    p_stream = sub.add_parser("stream", help="Stream matches as live UDP telemetry (for the viewer's -Live mode)")
    _add_replay_args(p_stream)
    p_stream.add_argument("--host", default="127.0.0.1", help="Where to send (default: this computer)")
    p_stream.add_argument("--port", type=int, default=7777)
    p_stream.add_argument("--speed", type=float, default=1.0, help="Playback speed (2 = twice as fast)")
    p_stream.add_argument("--loop", action="store_true", help="Start over when the matches end")
    p_stream.add_argument("--drop", type=float, default=0.0, help="Simulate packet loss (0.05 = 5%%)")
    p_stream.add_argument("--duplicate", type=float, default=0.0, help="Simulate duplicated packets")
    p_stream.add_argument("--jitter-ms", type=float, default=0.0, help="Simulate random delay up to this many ms")
    p_stream.add_argument("--seed", type=int, help="Seed for the simulated network trouble")
    p_stream.set_defaults(func=_cmd_stream)

    p_listen = sub.add_parser("listen", help="Receive a telemetry stream and print link statistics")
    p_listen.add_argument("--host", default="127.0.0.1", help="Address to listen on (0.0.0.0 accepts other computers)")
    p_listen.add_argument("--port", type=int, default=7777)
    p_listen.add_argument("--seconds", type=float, help="Stop after this long (default: until the stream ends)")
    p_listen.set_defaults(func=_cmd_listen)

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
