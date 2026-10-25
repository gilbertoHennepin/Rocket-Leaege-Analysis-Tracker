"""Session dashboard: one accessible HTML page about your recent matches.

``rltracker dashboard`` writes a single self-contained HTML file (no server,
no network, no external files) that answers the questions a player has after
a session, in the order they have them:

1. What should I work on next? At most three focus areas, ranked by how many
   matches they showed up in, each with a tip and the clock times to review.
2. Am I getting better? A small chart per stat across the matches, with the
   lobby average for context, and the same numbers as a table.
3. What happened in each match? Result, stats against the lobby, every
   coaching tip, and a heat map.

The design decisions, the accessibility checklist and how each is verified
are in ``docs/hci.md``.
"""

from __future__ import annotations

import base64
import html
import io
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt

from .coach import Report, Severity, coach
from .heatmap import player_heatmap
from .models import Match, Player, Team
from .moments import Moment
from .stats import PlayerStats

MAX_FOCUS = 3  # more than a few priorities is no priority at all
MAX_TIMES = 8  # clock times listed per tip before "and N more"


@dataclass(frozen=True)
class Metric:
    key: str  # PlayerStats field
    label: str
    unit: str  # "%" or ""
    better: str | None  # "higher", "lower", or None when neither is better
    help: str
    decimals: int = 0


METRICS = [
    Metric("avg_boost", "Average boost", "", "higher", "Boost held on average during live play, 0 to 100."),
    Metric("pct_low_boost", "Time low on boost", "%", "lower", "Share of live play with less than 25 boost."),
    Metric("pct_behind_ball", "Time behind the ball", "%", "higher", "Share of live play goal-side of the ball."),
    Metric("touches_per_min", "Touches per minute", "", "higher", "Ball touches per minute of live play.", 1),
    Metric("pct_supersonic", "Time supersonic", "%", "higher", "Share of live play at supersonic speed."),
    Metric("pct_defensive_third", "Time in defensive third", "%", None, "Share of live play in your own third."),
]


@dataclass
class MatchEntry:
    """One match, from the point of view of the player the dashboard is about."""

    match: Match
    player: Player
    report: Report
    lobby: list[PlayerStats]
    number: int  # 1 for the oldest match in the dashboard

    @property
    def anchor(self) -> str:
        return f"match-{self.number}"

    @property
    def name(self) -> str:
        return f"Match {self.number}"

    @property
    def result(self) -> str:
        winner = self.match.winner
        if winner is None:
            return "Draw"
        return "Win" if winner == self.player.team else "Loss"

    @property
    def score_text(self) -> str:
        """Your team's score first, as players say it."""
        mine, theirs = self.match.score[self.player.team], self.match.score[self.player.team.opponent]
        return f"{mine}–{theirs}"

    def lobby_avg(self, key: str) -> float:
        return sum(getattr(s, key) for s in self.lobby) / len(self.lobby)


@dataclass
class Focus:
    """A coaching rule that came up in one or more matches."""

    rule: str
    title: str
    message: str
    tip: str
    severity: Severity
    entries: list[MatchEntry] = field(default_factory=list)
    moments: list[tuple[MatchEntry, Moment]] = field(default_factory=list)


def pick_player(match: Match, name: str | None = None) -> Player | None:
    """The player a dashboard is about: ``name`` if given, else the replay's owner."""
    if name:
        try:
            return match.player(name)
        except KeyError:
            return None
    if match.recorded_by:
        return match.player(match.recorded_by)
    return match.players[0] if match.players else None


def _sort_key(match: Match) -> tuple[float, str]:
    played = match.played_at.timestamp() if match.played_at else float("inf")
    return played, match.source.name


def collect(matches: Iterable[Match], player_name: str | None = None) -> tuple[list[MatchEntry], list[Match]]:
    """Coach every match for its player. Returns the entries, oldest first, and the matches skipped."""
    entries: list[MatchEntry] = []
    skipped: list[Match] = []
    for match in sorted(matches, key=_sort_key):
        player = pick_player(match, player_name)
        if player is None:
            skipped.append(match)
            continue
        reports = coach(match)
        lobby = [r.stats for r in reports.values()]
        entries.append(MatchEntry(match, player, reports[player.id], lobby, len(entries) + 1))
    return entries, skipped


def focus_areas(entries: list[MatchEntry], limit: int = MAX_FOCUS) -> list[Focus]:
    """Coaching rules across the session, most widespread first, then most severe."""
    by_rule: dict[str, Focus] = {}
    for entry in entries:
        for insight in entry.report.insights:
            focus = by_rule.get(insight.rule)
            if focus is None:
                focus = by_rule[insight.rule] = Focus(
                    insight.rule, insight.title, insight.message, insight.tip, insight.severity
                )
            # Keep the latest wording (it quotes the latest match's numbers) and the worst severity.
            focus.title, focus.message, focus.tip = insight.title, insight.message, insight.tip
            focus.severity = max(focus.severity, insight.severity)
            focus.entries.append(entry)
            focus.moments += [(entry, m) for m in insight.moments]
    ranked = sorted(by_rule.values(), key=lambda f: (-len(f.entries), -f.severity, -len(f.moments)))
    return ranked[:limit]


def recurring_strengths(entries: list[MatchEntry]) -> list[tuple[str, int]]:
    """Strength titles and how many matches each showed up in, most frequent first."""
    counts = Counter(s.title for e in entries for s in e.report.strengths)
    return counts.most_common()


# Rendering --------------------------------------------------------------------

_e = html.escape

SEVERITY_ICON = {Severity.HIGH: "▲", Severity.MEDIUM: "◆", Severity.LOW: "●"}
RESULT_ICON = {"Win": "✔", "Loss": "✖", "Draw": "●"}


def _fmt(value: float, metric: Metric) -> str:
    return f"{value:.{metric.decimals}f}{metric.unit}"


def _date(match: Match) -> str:
    when = match.played_at
    return f"{when:%b} {when.day}, {when:%H:%M} UTC" if when else match.source.stem


def _compare(value: float, lobby: float, metric: Metric) -> str:
    """'Better than the lobby' style text: color-free, so it reads the same for everyone."""
    if metric.better is None or abs(value - lobby) < 0.05 * max(abs(lobby), 1.0):
        return "About the same as the lobby" if metric.better else ""
    ahead = value > lobby if metric.better == "higher" else value < lobby
    return "Better than the lobby" if ahead else "Worse than the lobby"


def _severity_badge(severity: Severity) -> str:
    name = severity.label.capitalize()
    return (
        f'<span class="badge sev-{severity.label}"><span class="icon" aria-hidden="true">'
        f"{SEVERITY_ICON[severity]}</span>{name} priority</span>"
    )


def _times(moments: list[tuple[MatchEntry, Moment]], multi: bool) -> str:
    """Clock times to review, linked to their match, e.g. "Match 2 at 3:41"."""
    if not moments:
        return ""
    shown = moments[:MAX_TIMES]
    items = []
    for entry, moment in shown:
        label = _e(moment.label(entry.match))
        prefix = f"{entry.name} at " if multi else ""
        items.append(f'<li><a href="#{entry.anchor}">{prefix}{label}</a></li>')
    more = len(moments) - len(shown)
    extra = f'<li class="muted">and {more} more</li>' if more > 0 else ""
    return f'<ul class="times" aria-label="When it happened (game clock)">{"".join(items)}{extra}</ul>'


def _nice_ticks(low: float, high: float, count: int = 3) -> list[float]:
    span = max(high - low, 1e-9)
    raw = span / count
    magnitude = 10 ** int(f"{raw:e}".split("e")[1])
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first, last = math.floor(low / step), math.ceil(high / step)
    if first == last:
        last += 1
    return [round(n * step, 6) for n in range(first, last + 1)]


def line_chart(entries: list[MatchEntry], metric: Metric) -> str:
    """One stat across the session: your value per match (solid) and the lobby average (dashed)."""
    width, height = 320, 150
    left, right, top, bottom = 38, 14, 12, 26
    you = [getattr(e.report.stats, metric.key) for e in entries]
    lobby = [e.lobby_avg(metric.key) for e in entries]
    ticks = _nice_ticks(min(you + lobby), max(you + lobby))
    low, high = ticks[0], ticks[-1]
    if metric.unit == "%":
        low, high = max(low, 0.0), min(high, 100.0)
        ticks = [t for t in ticks if low <= t <= high]
    span = (high - low) or 1.0

    def x(i: int) -> float:
        if len(entries) == 1:
            return (left + width - right) / 2
        return left + i * (width - left - right) / (len(entries) - 1)

    def y(v: float) -> float:
        return top + (high - v) / span * (height - top - bottom)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="group" '
        f'aria-label="{_e(metric.label)} per match. Values are also in the table below.">'
    ]
    for t in ticks:
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        parts.append(
            f'<text class="tick" x="{left - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{t:g}{metric.unit}</text>'
        )
    step = max(1, len(entries) // 6)  # at most ~6 date labels
    for i, entry in enumerate(entries):
        if i % step == 0 or i == len(entries) - 1:
            parts.append(
                f'<text class="tick" x="{x(i):.1f}" y="{height - 8}" text-anchor="middle">#{entry.number}</text>'
            )

    def path(values: list[float]) -> str:
        return " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))

    parts.append(f'<path class="lobby" d="{path(lobby)}"/>')
    parts.append(f'<path class="you" d="{path(you)}"/>')
    for i, (entry, value) in enumerate(zip(entries, you, strict=True)):
        tip = (
            f"{entry.name} · {entry.match.mode} {entry.result} {entry.score_text}: "
            f"{_fmt(value, metric)} (lobby {_fmt(lobby[i], metric)})"
        )
        parts.append(f'<circle class="dot" cx="{x(i):.1f}" cy="{y(value):.1f}" r="4"/>')
        # A bigger invisible target than the dot, for the mouse, touch and keyboard.
        parts.append(
            f'<circle class="hit" cx="{x(i):.1f}" cy="{y(value):.1f}" r="14" tabindex="0" role="img" '
            f'data-tip="{_e(tip)}" aria-label="{_e(tip)}"/>'
        )
    last = len(entries) - 1
    parts.append(
        f'<text class="value" x="{x(last):.1f}" y="{y(you[last]) - 9:.1f}" text-anchor="middle">'
        f"{_fmt(you[last], metric)}</text>"
    )
    parts.append("</svg>")

    rows = "".join(
        f"<tr><th scope='row'>{e.name} · {_e(e.match.mode)}</th>"
        f"<td>{_fmt(v, metric)}</td><td>{_fmt(lv, metric)}</td></tr>"
        for e, v, lv in zip(entries, you, lobby, strict=True)
    )
    better = {"higher": "Higher is better.", "lower": "Lower is better."}.get(metric.better or "", "")
    return f"""
<figure class="card chart-card">
  <figcaption><h3>{_e(metric.label)}</h3><p class="muted">{_e(metric.help)} {better}</p></figcaption>
  {"".join(parts)}
  <p class="legend" aria-hidden="true"><span><span class="key"></span>You</span>
    <span><span class="key lobby-key"></span>Lobby average</span></p>
  <details><summary>Show as a table</summary>
    <table><caption class="sr-only">{_e(metric.label)} per match</caption>
      <thead><tr><th scope="col">Match</th><th scope="col">You</th><th scope="col">Lobby average</th></tr></thead>
      <tbody>{rows}</tbody></table>
  </details>
</figure>"""


def _stat_tiles(entry: MatchEntry) -> str:
    tiles = []
    for metric in METRICS:
        value, lobby = getattr(entry.report.stats, metric.key), entry.lobby_avg(metric.key)
        compare = _compare(value, lobby, metric)
        tiles.append(
            f'<div class="card tile"><h3>{_e(metric.label)}</h3><p class="big">{_fmt(value, metric)}</p>'
            f'<p class="muted">Lobby average {_fmt(lobby, metric)}</p>'
            + (f"<p>{compare}</p>" if compare else "")
            + "</div>"
        )
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _heatmap_png(entry: MatchEntry) -> str:
    fig = player_heatmap(entry.match, entry.player)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=80)
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _heatmap_alt(entry: MatchEntry) -> str:
    s = entry.report.stats
    return (
        f"Heat map of where {entry.player.name} drove during live play, attacking to the right. "
        f"{s.pct_defensive_third:.0f}% of the time in the defensive third, {s.pct_neutral_third:.0f}% in the "
        f"middle third and {s.pct_offensive_third:.0f}% in the offensive third."
    )


def _match_section(entry: MatchEntry, heatmaps: bool) -> str:
    m, p, r = entry.match, entry.player, entry.report
    s = r.stats
    rows = []
    for metric in METRICS:
        value, lobby = getattr(s, metric.key), entry.lobby_avg(metric.key)
        rows.append(
            f"<tr><th scope='row'>{_e(metric.label)}</th><td>{_fmt(value, metric)}</td>"
            f"<td>{_fmt(lobby, metric)}</td><td>{_compare(value, lobby, metric)}</td></tr>"
        )
    insights = []
    for insight in r.insights:
        times = _times([(entry, mo) for mo in insight.moments], multi=False)
        insights.append(
            f"<li class='insight'>{_severity_badge(insight.severity)}<h4>{_e(insight.title)}</h4>"
            f"<p>{_e(insight.message)}</p><p><strong>Tip:</strong> {_e(insight.tip)}</p>{times}</li>"
        )
    insight_html = (
        f"<ol class='insights'>{''.join(insights)}</ol>"
        if insights
        else "<p>Nothing major to fix in this match. Nice game.</p>"
    )
    strengths = "".join(f"<li><strong>{_e(st.title)}.</strong> {_e(st.message)}</li>" for st in r.strengths)
    strengths_html = f"<h4>Strengths</h4><ul class='strengths'>{strengths}</ul>" if strengths else ""
    heatmap = ""
    if heatmaps:
        heatmap = (
            f'<figure class="heatmap"><img src="data:image/png;base64,{_heatmap_png(entry)}" '
            f'alt="{_e(_heatmap_alt(entry))}" width="100%"><figcaption class="muted">Where you drove, '
            f"attacking to the right.</figcaption></figure>"
        )
    team = "Blue" if p.team == Team.BLUE else "Orange"
    return f"""
<section class="match" id="{entry.anchor}" aria-labelledby="{entry.anchor}-title" tabindex="-1">
  <h3 id="{entry.anchor}-title">{entry.name}: <span class="result">{RESULT_ICON[entry.result]}</span>
    {entry.result} {entry.score_text} · {_e(m.mode)} {_e(m.playlist or "")}</h3>
  <p class="muted">{_e(_date(m))} · {_e(m.source.name)} · you played {team} ·
    {s.goals} goals, {s.assists} assists, {s.saves} saves, {s.shots} shots</p>
  <div class="match-grid">
    <div>
      <h4>What to work on</h4>
      {insight_html}
      {strengths_html}
    </div>
    <div>
      <table class="stats">
        <caption>Your stats against the lobby average</caption>
        <thead><tr><th scope="col">Stat</th><th scope="col">You</th><th scope="col">Lobby</th>
          <th scope="col">Compared</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
      {heatmap}
    </div>
  </div>
  <p><a href="#matches">Back to all matches</a></p>
</section>"""


def _focus_section(entries: list[MatchEntry]) -> str:
    focus = focus_areas(entries)
    multi = len(entries) > 1
    if not focus:
        body = "<p>No coaching issues came up. Keep doing what you're doing.</p>"
    else:
        cards = []
        for n, f in enumerate(focus, 1):
            seen = f"In {len(f.entries)} of {len(entries)} matches" if multi else "In this match"
            cards.append(
                f'<li class="card focus"><p class="eyebrow">{n} · {seen}</p>{_severity_badge(f.severity)}'
                f"<h3>{_e(f.title)}</h3><p>{_e(f.message)}</p><p><strong>Tip:</strong> {_e(f.tip)}</p>"
                + (
                    f"<details><summary>{len(f.moments)} moments to review</summary>{_times(f.moments, multi)}"
                    "</details>"
                    if f.moments
                    else ""
                )
                + "</li>"
            )
        body = f'<ol class="focus-list">{"".join(cards)}</ol>'
    strengths = recurring_strengths(entries)
    if strengths:
        items = "".join(
            f"<li>{_e(title)}" + (f" <span class='muted'>({count} of {len(entries)})</span>" if multi else "") + "</li>"
            for title, count in strengths[:5]
        )
        body += f"<h3 class='sub'>What you do well</h3><ul class='strength-chips'>{items}</ul>"
    return f'<section id="focus" aria-labelledby="focus-title"><h2 id="focus-title">Work on next</h2>{body}</section>'


def _trends_section(entries: list[MatchEntry]) -> str:
    if len(entries) == 1:
        title, body = "Your stats", _stat_tiles(entries[0])
    else:
        intro = (
            f'<p class="muted">Each chart runs from your oldest match (#1) to your newest (#{entries[-1].number}). '
            "Point at a dot, or move to it with Tab, for the match and the exact numbers.</p>"
        )
        charts = "".join(line_chart(entries, m) for m in METRICS)
        title, body = "Trends", f'{intro}<div class="charts">{charts}</div>'
    return f'<section id="trends" aria-labelledby="trends-title"><h2 id="trends-title">{title}</h2>{body}</section>'


def _matches_section(entries: list[MatchEntry], heatmaps: bool) -> str:
    rows = "".join(
        f"<tr><th scope='row'><a href='#{e.anchor}'>{e.name}</a></th><td>{_e(_date(e.match))}</td>"
        f"<td>{_e(e.match.mode)}</td>"
        f"<td><span aria-hidden='true'>{RESULT_ICON[e.result]}</span> {e.result}</td><td>{e.score_text}</td>"
        f"<td>{e.report.stats.goals}</td><td>{e.report.stats.saves}</td><td>{len(e.report.insights)}</td></tr>"
        for e in reversed(entries)
    )
    sections = "".join(_match_section(e, heatmaps) for e in reversed(entries))
    return f"""
<section id="matches" aria-labelledby="matches-title">
  <h2 id="matches-title">Matches</h2>
  <div class="table-wrap"><table>
    <caption class="sr-only">Every match in this dashboard, newest first</caption>
    <thead><tr><th scope="col">Match</th><th scope="col">Played</th><th scope="col">Mode</th><th scope="col">Result</th>
      <th scope="col">Score</th><th scope="col">Goals</th><th scope="col">Saves</th>
      <th scope="col">Tips</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
  {sections}
</section>"""


def render(
    entries: list[MatchEntry], skipped: list[Match] | None = None, heatmaps: bool = True, now: datetime | None = None
) -> str:
    """The dashboard as one HTML document."""
    if not entries:
        raise ValueError("No matches to show")
    names = Counter(e.player.name for e in entries)
    name = names.most_common(1)[0][0]
    results = Counter(e.result for e in entries)
    record = f"{results['Win']}W – {results['Loss']}L" + (f" – {results['Draw']}D" if results["Draw"] else "")
    goals = sum(e.report.stats.goals for e in entries)
    generated = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    note = ""
    if skipped:
        listed = ", ".join(_e(m.source.name) for m in skipped)
        note = f'<p class="note" role="note">Left out {len(skipped)} matches you weren\'t in: {listed}.</p>'
    count = f"{len(entries)} match" + ("es" if len(entries) != 1 else "")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="rltracker">
<title>Session review: {_e(name)}</title>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#main">Skip to the dashboard</a>
<header class="top">
  <div class="wrap">
    <p class="eyebrow">rltracker session review</p>
    <h1>{_e(name)}</h1>
    <ul class="summary" aria-label="Session summary">
      <li><span class="big">{count.split()[0]}</span> {count.split()[1]}</li>
      <li><span class="big">{record}</span> record</li>
      <li><span class="big">{goals}</span> goals</li>
    </ul>
    <nav aria-label="Sections"><a href="#focus">Work on next</a>
      <a href="#trends">{"Trends" if len(entries) > 1 else "Stats"}</a><a href="#matches">Matches</a></nav>
  </div>
</header>
<main id="main" class="wrap" tabindex="-1">
{note}
{_focus_section(entries)}
{_trends_section(entries)}
{_matches_section(entries, heatmaps)}
</main>
<footer class="wrap muted"><p>Made by rltracker on {generated} from {count}. Stats use live play only.
Times are the game clock, as shown in the replay.</p></footer>
<div id="tip" role="tooltip" hidden></div>
<script>{SCRIPT}</script>
</body>
</html>
"""


def write_dashboard(
    matches: Iterable[Match], out: str | Path, player_name: str | None = None, heatmaps: bool = True
) -> tuple[Path, list[MatchEntry], list[Match]]:
    """Build and save the dashboard (unless no match has the player). Returns its path, the matches shown and
    those skipped."""
    entries, skipped = collect(matches, player_name)
    out = Path(out)
    if entries:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(entries, skipped, heatmaps), encoding="utf-8")
    return out, entries, skipped


# Colors are tokens, light and dark, chosen so every text color clears WCAG AA
# (4.5:1) on every surface it sits on. tests/test_dashboard.py measures them.
LIGHT = {
    "bg": "#f4f4f2",
    "surface": "#fcfcfb",
    "text": "#0b0b0b",
    "text-2": "#52514e",
    "line": "#d6d5d0",
    "grid": "#e4e3df",
    "series": "#2a78d6",
    "link": "#1c5cab",
    "focus": "#1c5cab",
    "sev-high": "#d03b3b",
    "sev-medium": "#c45f1f",
    "sev-low": "#52514e",
}
DARK = {
    "bg": "#121211",
    "surface": "#1a1a19",
    "text": "#f0efec",
    "text-2": "#c3c2b7",
    "line": "#3a3936",
    "grid": "#2c2c2a",
    "series": "#3987e5",
    "link": "#86b6ef",
    "focus": "#86b6ef",
    "sev-high": "#e66767",
    "sev-medium": "#ec835a",
    "sev-low": "#c3c2b7",
}
# Pairs that must reach 4.5:1 (text) or 3:1 (marks and focus rings).
TEXT_PAIRS = [("text", "bg"), ("text", "surface"), ("text-2", "surface"), ("text-2", "bg"), ("link", "surface")]
MARK_PAIRS = [("series", "surface"), ("focus", "surface"), ("focus", "bg")] + [
    (f"sev-{s}", "surface") for s in ("high", "medium", "low")
]


def _tokens(values: dict[str, str]) -> str:
    return "".join(f"--{k}:{v};" for k, v in values.items())


CSS = (
    f":root{{color-scheme:light;{_tokens(LIGHT)}}}"
    f"@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;{_tokens(DARK)}}}}}"
    """
*{box-sizing:border-box}
html{font-size:100%}
body{margin:0;background:var(--bg);color:var(--text);
  font:1rem/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans","Noto Sans CJK JP",sans-serif}
.wrap{max-width:72rem;margin:0 auto;padding:0 1rem}
a{color:var(--link);text-underline-offset:.15em}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px;border-radius:4px}
.skip{position:absolute;left:-999px;top:.5rem;background:var(--surface);padding:.5rem 1rem;z-index:10}
.skip:focus{left:.5rem}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.muted{color:var(--text-2)}
.eyebrow{margin:0;color:var(--text-2);font-size:.85rem;letter-spacing:.04em;text-transform:uppercase}
h1{font-size:2rem;margin:.1rem 0 .5rem;overflow-wrap:anywhere}
h2{font-size:1.4rem;margin:2.5rem 0 1rem}
h3{font-size:1.1rem;margin:.25rem 0}
h3.sub{margin-top:1.5rem}
h4{font-size:1rem;margin:.75rem 0 .25rem}
.top{background:var(--surface);border-bottom:1px solid var(--line);padding:1.5rem 0 0}
.summary{display:flex;flex-wrap:wrap;gap:1.5rem;list-style:none;padding:0;margin:0 0 1rem}
.big{font-size:1.6rem;font-weight:700;font-variant-numeric:tabular-nums;margin:0}
nav{display:flex;gap:.25rem;flex-wrap:wrap}
nav a{padding:.6rem .9rem;text-decoration:none;font-weight:600;border-bottom:3px solid transparent}
nav a:hover{border-bottom-color:var(--line)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:1rem 1.1rem}
.focus-list{list-style:none;padding:0;margin:0;display:grid;gap:1rem;
  grid-template-columns:repeat(auto-fit,minmax(18rem,1fr))}
.focus h3{font-size:1.15rem}
.badge{display:inline-flex;gap:.35rem;align-items:center;font-size:.85rem;font-weight:600;margin:.35rem 0}
.badge .icon{font-size:.8rem}
.sev-high .icon{color:var(--sev-high)}
.sev-medium .icon{color:var(--sev-medium)}
.sev-low .icon{color:var(--sev-low)}
.times{display:flex;flex-wrap:wrap;gap:.4rem;list-style:none;padding:0;margin:.5rem 0 0}
.times a{display:inline-block;min-height:2rem;padding:.25rem .6rem;border:1px solid var(--line);
  border-radius:999px;text-decoration:none;font-variant-numeric:tabular-nums}
.times .muted{padding:.25rem 0}
details{margin-top:.5rem}
summary{cursor:pointer;min-height:2rem;padding:.25rem 0;font-weight:600;color:var(--link)}
.strength-chips{display:flex;flex-wrap:wrap;gap:.5rem;list-style:none;padding:0}
.strength-chips li{background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:.3rem .8rem}
.charts,.tiles{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(17rem,1fr))}
.chart-card{margin:0}
.chart-card p{margin:.2rem 0 .4rem;font-size:.9rem}
.chart{width:100%;height:auto;display:block;overflow:visible}
.chart .grid{stroke:var(--grid);stroke-width:1}
.chart .tick{fill:var(--text-2);font-size:11px;font-variant-numeric:tabular-nums}
.chart .value{fill:var(--text);font-size:12px;font-weight:700}
.chart .you{fill:none;stroke:var(--series);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.chart .lobby{fill:none;stroke:var(--text-2);stroke-width:1.5;stroke-dasharray:4 4}
.chart .dot{fill:var(--series);stroke:var(--surface);stroke-width:2}
.chart .hit{fill:transparent;cursor:default}
.chart .hit:focus-visible{outline:none;stroke:var(--focus);stroke-width:3}
.legend{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem 1rem;font-size:.85rem;color:var(--text-2)}
.legend span{white-space:nowrap}
.key{display:inline-block;width:1.4rem;height:0;margin-right:.4rem;vertical-align:middle;
  border-top:2px solid var(--series)}
.key.lobby-key{border-top:2px dashed var(--text-2)}
.tile h3{font-size:.95rem;color:var(--text-2);font-weight:600}
.tile p{margin:.15rem 0}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
caption{text-align:left;font-weight:600;padding:.25rem 0 .5rem}
th,td{text-align:left;padding:.45rem .6rem;border-bottom:1px solid var(--line)}
thead th{color:var(--text-2);font-size:.85rem;font-weight:600}
.table-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px}
.match{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:1rem 1.1rem;
  margin:1.5rem 0;scroll-margin-top:1rem}
.match:target{outline:3px solid var(--focus)}
.match-grid{display:grid;gap:1.5rem;grid-template-columns:repeat(auto-fit,minmax(20rem,1fr))}
.insights{padding-left:1.2rem}
.insight{margin-bottom:1rem}
.insight h4{margin:.1rem 0}
.insight p{margin:.25rem 0}
.heatmap{margin:1rem 0 0}
.heatmap img{border-radius:8px;display:block;height:auto}
.note{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:.6rem 1rem;margin-top:1.5rem}
#tip{position:fixed;z-index:20;max-width:18rem;background:var(--text);color:var(--bg);font-size:.85rem;
  padding:.4rem .6rem;border-radius:6px;pointer-events:none}
footer{padding:2rem 1rem 3rem;font-size:.9rem}
@media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
@media print{nav,.skip,#tip{display:none}details{display:block}.match{break-inside:avoid}}
@media (forced-colors:active){.chart .you,.chart .dot{stroke:CanvasText}.chart .dot{fill:CanvasText}}
"""
)

# Tooltips for chart points, on hover and on keyboard focus; Escape hides them.
SCRIPT = """
(() => {
  const tip = document.getElementById("tip");
  const show = (el) => {
    tip.textContent = el.dataset.tip;
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
    tip.style.top = (r.top - h - 8 < 8 ? r.bottom + 8 : r.top - h - 8) + "px";
  };
  const hide = () => { tip.hidden = true; };
  for (const el of document.querySelectorAll("[data-tip]")) {
    el.addEventListener("pointerenter", () => show(el));
    el.addEventListener("pointerleave", hide);
    el.addEventListener("focus", () => show(el));
    el.addEventListener("blur", hide);
  }
  addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  addEventListener("scroll", hide, { passive: true });
})();
"""
