"""Turn stats and detected moments into coaching feedback.

Each rule looks at one habit, decides whether it is a problem for this
player (comparing against fixed thresholds and the rest of the lobby), and
if so produces an :class:`Insight` with a plain-English explanation, a tip,
and the moments in the match that show it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import IntEnum

from .aerial import Aerial, find_aerials
from .models import Match, Player
from .moments import Moment, all_moments
from .stats import PlayerStats, player_stats


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class Insight:
    rule: str
    severity: Severity
    title: str
    message: str
    tip: str
    moments: list[Moment] = field(default_factory=list)


@dataclass(frozen=True)
class Strength:
    title: str
    message: str


@dataclass
class Report:
    player: Player
    stats: PlayerStats
    insights: list[Insight]
    strengths: list[Strength]


@dataclass(frozen=True)
class _Context:
    match: Match
    player: Player
    stats: PlayerStats
    lobby: list[PlayerStats]
    moments: list[Moment]  # this player's moments only
    aerials: list[Aerial] = field(default_factory=list)  # everyone's

    def of_kind(self, kind: str) -> list[Moment]:
        return [m for m in self.moments if m.kind == kind]

    def per_five_minutes(self, count: int) -> float:
        """Normalize a count to a standard 5 minute game."""
        seconds = self.stats.seconds_played or 1.0
        return count * 300.0 / seconds

    def lobby_avg(self, name: str) -> float:
        values = [getattr(s, name) for s in self.lobby]
        return sum(values) / len(values)

    @property
    def team_mode(self) -> bool:
        return self.match.team_size > 1

    def aerials_of(self, player_id: str) -> list[Aerial]:
        """Aerials with a thrust measurement, for one player."""
        return [a for a in self.aerials if a.player_id == player_id and a.thrust_alignment is not None]


def _thrust_alignment(aerials: list[Aerial]) -> float:
    """Boost-weighted thrust alignment over several aerials, as a percentage."""
    used = sum(a.boost_used for a in aerials)
    return 100.0 * sum(a.thrust_alignment * a.boost_used for a in aerials) / used if used else 0.0


MIN_AERIALS = 4  # aerials with a thrust measurement before aerial feedback is given


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


Rule = Callable[[_Context], Insight | None]
RULES: list[Rule] = []


def rule(func: Rule) -> Rule:
    RULES.append(func)
    return func


# Boost ---------------------------------------------------------------------


@rule
def boost_starved(c: _Context) -> Insight | None:
    s = c.stats
    if s.pct_low_boost < 40:
        return None
    severity = Severity.HIGH if s.pct_low_boost >= 50 else Severity.MEDIUM
    return Insight(
        rule="boost_starved",
        severity=severity,
        title="You're starving on boost",
        message=(
            f"You spent {s.pct_low_boost:.0f}% of live play under 25 boost and {s.pct_zero_boost:.0f}% on empty "
            f"(lobby average: {c.lobby_avg('pct_low_boost'):.0f}% under 25)."
        ),
        tip=(
            "Path through small pads when rotating back (each is worth 12), feather boost instead of holding it, "
            "and stop boosting once you're supersonic."
        ),
        moments=c.of_kind("low_boost"),
    )


@rule
def boost_hoarding(c: _Context) -> Insight | None:
    s = c.stats
    if s.pct_full_boost < 20:
        return None
    return Insight(
        rule="boost_hoarding",
        severity=Severity.LOW,
        title="You sit on full boost",
        message=f"You were at 100 boost for {s.pct_full_boost:.0f}% of live play, so pads you pass over are wasted.",
        tip="Boost is only useful when spent. Use it to get to the ball first or to challenge, then refill.",
    )


@rule
def big_pad_reliance(c: _Context) -> Insight | None:
    s = c.stats
    total = s.big_pads + s.small_pads
    if total < 15 or s.small_pads / total >= 0.55:
        return None
    return Insight(
        rule="big_pad_reliance",
        severity=Severity.LOW,
        title="You rely on big boost pads",
        message=(
            f"Only {s.small_pads / total:.0%} of your pickups were small pads ({s.big_pads} big, {s.small_pads} small)."
        ),
        tip="Detouring to corner boost pulls you out of the play. Chaining small pads keeps you close to the action.",
    )


# Decision making -------------------------------------------------------------


@rule
def over_commit(c: _Context) -> Insight | None:
    lost = c.of_kind("lost_challenge")
    punished = [m for m in lost if m.punished]
    rate = c.per_five_minutes(len(lost))
    if rate < 3 and len(punished) < 2:
        return None
    severity = Severity.HIGH if len(punished) >= 2 or rate >= 6 else Severity.MEDIUM
    who = "as the last player back" if c.team_mode else ""
    return Insight(
        rule="over_commit",
        severity=severity,
        title="You over-commit on defense" if c.team_mode else "You over-commit on challenges",
        message=(
            f"You lost {_plural(len(lost), 'challenge')} {who}".rstrip()
            + f" (whiffed or got beaten to the ball), and {len(punished)} led to an opponent shot or goal."
        ),
        tip=(
            "When you're last back, shadow the ball carrier and wait for a teammate to rotate in before you challenge."
            if c.team_mode
            else "Don't challenge every touch. Shadow back toward net, keep boost, and challenge when they "
            "mishandle it."
        ),
        moments=lost,
    )


@rule
def double_commit(c: _Context) -> Insight | None:
    if not c.team_mode:
        return None
    doubles = c.of_kind("double_commit")
    punished = [m for m in doubles if m.punished]
    rate = c.per_five_minutes(len(doubles))
    if rate < 4 and len(punished) < 2:
        return None
    severity = Severity.HIGH if len(punished) >= 2 or rate >= 7 else Severity.MEDIUM
    return Insight(
        rule="double_commit",
        severity=severity,
        title="You double commit with teammates",
        message=(
            f"{len(doubles)} times you went for the same ball as a teammate who was closer"
            + (f", and {len(punished)} turned into an opponent chance." if punished else ".")
        ),
        tip="If a teammate is closer and already going, peel off and rotate back post-side to cover instead.",
        moments=doubles,
    )


@rule
def ball_chasing(c: _Context) -> Insight | None:
    if not c.team_mode:
        return None
    s = c.stats
    fair_share = 100 / c.match.team_size
    if s.pct_closest_to_ball < fair_share + 12 or s.pct_behind_ball >= 72:
        return None
    return Insight(
        rule="ball_chasing",
        severity=Severity.MEDIUM,
        title="You're ball chasing",
        message=(
            f"You were your team's closest player to the ball {s.pct_closest_to_ball:.0f}% of the time "
            f"(an even split is {fair_share:.0f}%) while only {s.pct_behind_ball:.0f}% of your time was behind it."
        ),
        tip=(
            "After you hit the ball, rotate back toward your net along the far side and let a teammate take the "
            "next touch."
        ),
    )


@rule
def out_of_position(c: _Context) -> Insight | None:
    s = c.stats
    caught = c.of_kind("caught_ahead")
    if s.pct_behind_ball >= 65 and len(caught) < 3:
        return None
    severity = Severity.HIGH if s.pct_behind_ball < 60 and len(caught) >= 3 else Severity.MEDIUM
    return Insight(
        rule="out_of_position",
        severity=severity,
        title="You get caught ahead of the ball",
        message=(
            f"You were upfield of the ball for {_plural(len(caught), 'opponent shot')}"
            + (
                f", and behind the ball for only {s.pct_behind_ball:.0f}% of live play "
                f"(lobby average {c.lobby_avg('pct_behind_ball'):.0f}%)."
                if s.pct_behind_ball < 65
                else "."
            )
        ),
        tip=(
            "Stay between the ball and your net. If you can't see the ball and your goal on screen together, "
            "rotate back."
        ),
        moments=caught,
    )


@rule
def conceded_pattern(c: _Context) -> Insight | None:
    conceded = c.of_kind("conceded")
    if len(conceded) < 2:
        return None
    low_boost = [m for m in conceded if "boost" in m.detail]
    ahead = [m for m in conceded if "ahead of the ball" in m.detail]
    worst, label = max(((low_boost, "low on boost"), (ahead, "ahead of the ball")), key=lambda x: len(x[0]))
    if len(worst) < 2 or len(worst) / len(conceded) < 0.5:
        return None
    return Insight(
        rule="conceded_pattern",
        severity=Severity.MEDIUM,
        title=f"Goals against: you were {label}",
        message=(
            f"On {len(worst)} of the {len(conceded)} goals your team conceded, you were {label} "
            "when the shot was taken."
        ),
        tip=(
            "Refill before rotating back into defense. A save with no boost is a coin flip."
            if label == "low on boost"
            else "When your team loses possession, get back goal-side first, then think about the next challenge."
        ),
        moments=worst,
    )


@rule
def slow_play(c: _Context) -> Insight | None:
    s = c.stats
    if s.pct_supersonic >= 8 or s.avg_speed >= c.lobby_avg("avg_speed") * 0.95:
        return None
    return Insight(
        rule="slow_play",
        severity=Severity.LOW,
        title="You play slowly",
        message=(
            f"Average speed {s.avg_speed:.0f} uu/s (lobby {c.lobby_avg('avg_speed'):.0f}) and supersonic "
            f"only {s.pct_supersonic:.0f}% of the time."
        ),
        tip="Practice wavedashes, half flips and speed flips so you recover and get back up to speed faster.",
    )


@rule
def too_passive(c: _Context) -> Insight | None:
    s = c.stats
    if s.pct_defensive_third < 50 or s.touches_per_min >= c.lobby_avg("touches_per_min") * 0.8:
        return None
    return Insight(
        rule="too_passive",
        severity=Severity.LOW,
        title="You play too deep",
        message=(
            f"You spent {s.pct_defensive_third:.0f}% of live play in your defensive third and touched the ball "
            f"{s.touches_per_min:.1f} times a minute (lobby {c.lobby_avg('touches_per_min'):.1f})."
        ),
        tip="When your team has the ball, push up to midfield to support so you can keep the pressure on.",
    )


# Aerials ---------------------------------------------------------------------


@rule
def aerial_aim(c: _Context) -> Insight | None:
    mine = c.aerials_of(c.player.id)
    if len(mine) < MIN_AERIALS:
        return None
    alignment = _thrust_alignment(mine)
    lobby = [_thrust_alignment(a) for p in c.match.players if len(a := c.aerials_of(p.id)) >= 3]
    lobby_avg = sum(lobby) / len(lobby)
    if alignment >= 60 or alignment > lobby_avg - 5:
        return None
    wasted = sum(a.wasted_boost or 0.0 for a in mine)
    worst = sorted(mine, key=lambda a: a.thrust_alignment)[:6]
    return Insight(
        rule="aerial_aim",
        severity=Severity.MEDIUM if alignment < 50 else Severity.LOW,
        title="You boost off target in the air",
        message=(
            f"Only {alignment:.0f}% of your thrust in the air pushed toward where you met the ball "
            f"(lobby average {lobby_avg:.0f}%). That cost about {wasted:.0f} boost over {len(mine)} aerials."
        ),
        tip=(
            "Before holding boost, point your nose at where you'll meet the ball, not where it is now. "
            "Then feather boost and make small corrections instead of steering hard while boosting."
        ),
        moments=[
            Moment(
                "aerial",
                a.player_id,
                a.start,
                a.duration,
                detail=f"{a.thrust_alignment:.0%} thrust alignment, {a.wasted_boost:.0f} boost wasted",
            )
            for a in sorted(worst, key=lambda a: a.start)
        ],
    )


# Strengths -----------------------------------------------------------------


def _strengths(c: _Context) -> list[Strength]:
    s = c.stats
    out: list[Strength] = []
    if s.pct_low_boost <= 30:
        out.append(Strength("Good boost management", f"Only {s.pct_low_boost:.0f}% of live play under 25 boost."))
    if s.pct_behind_ball >= 78:
        out.append(Strength("Disciplined positioning", f"Behind the ball {s.pct_behind_ball:.0f}% of the time."))
    if s.touches_per_min >= max(x.touches_per_min for x in c.lobby):
        message = f"{s.touches_per_min:.1f} touches per minute, the most in the lobby."
        out.append(Strength("Most involved player", message))
    if s.saves >= 3:
        out.append(Strength("Strong defender", f"{s.saves} saves."))
    if s.goals >= 2:
        out.append(Strength("Finishing", f"{s.goals} goals from {s.shots} shots."))
    aerials = c.aerials_of(c.player.id)
    if len(aerials) >= MIN_AERIALS and (alignment := _thrust_alignment(aerials)) >= 75:
        out.append(Strength("Clean aerial control", f"{alignment:.0f}% thrust alignment over {len(aerials)} aerials."))
    if s.pct_supersonic >= 20:
        out.append(Strength("Fast player", f"Supersonic {s.pct_supersonic:.0f}% of the time."))
    if s.stolen_big_pads >= 8:
        out.append(Strength("Boost denial", f"Stole {s.stolen_big_pads} big pads from the opponent's half."))
    if c.team_mode and not c.of_kind("double_commit") and not c.of_kind("lost_challenge"):
        out.append(Strength("Clean rotations", "No double commits or lost last-man challenges."))
    return out[:3]


# Entry points ----------------------------------------------------------------


def coach(match: Match) -> dict[str, Report]:
    """Coaching reports for every player, keyed by player id."""
    lobby = player_stats(match)
    moments = all_moments(match)
    aerials = find_aerials(match)
    reports = {}
    for player, stats in zip(match.players, lobby, strict=True):
        ctx = _Context(match, player, stats, lobby, [m for m in moments if m.player_id == player.id], aerials)
        insights = [i for i in (r(ctx) for r in RULES) if i is not None]
        insights.sort(key=lambda i: (-i.severity, -len(i.moments)))
        reports[player.id] = Report(player, stats, insights, _strengths(ctx))
    return reports


def report_to_dict(match: Match, report: Report) -> dict:
    """JSON-friendly report, with game-clock times for each moment."""

    def moment(m: Moment) -> dict:
        return {**asdict(m), "clock": match.clock_at(m.time)}

    return {
        "player": report.player.name,
        "team": report.player.team.label,
        "insights": [
            {
                "rule": i.rule,
                "severity": i.severity.label,
                "title": i.title,
                "message": i.message,
                "tip": i.tip,
                "moments": [moment(m) for m in i.moments],
            }
            for i in report.insights
        ],
        "strengths": [asdict(s) for s in report.strengths],
        "stats": asdict(report.stats),
    }
