import json
from dataclasses import replace

import pytest

from rltracker import Team
from rltracker.cli import main
from rltracker.coach import Severity, _Context, boost_starved, coach, double_commit, over_commit, report_to_dict
from rltracker.moments import Moment, _dedupe, all_moments, committing, conceded_goals, double_commits
from rltracker.stats import live_frames, player_stats
from tests.conftest import FIXTURES


def test_recorder_is_detected(duel, doubles, standard):
    assert doubles.player(doubles.recorded_by).name == "2Fum2Tastic"
    assert standard.player(standard.recorded_by).name == "X2 ACID"
    # 1v1 replay names don't include the player, so fall back to PrimaryPlayerTeam.
    assert duel.player(duel.recorded_by).team == Team.ORANGE


def test_game_time_label(doubles, standard):
    assert doubles.game_time_label(0) == "5:00"
    assert standard.game_time_label(standard.ball["time"].iloc[-1]) == "OT"


def test_moments_belong_to_known_players(all_matches):
    for match in all_matches:
        ids = {p.id for p in match.players}
        moments = all_moments(match)
        assert moments
        assert {m.player_id for m in moments} <= ids
        assert [m.time for m in moments] == sorted(m.time for m in moments)


def test_no_double_commits_in_1v1(duel):
    assert double_commits(duel) == []


def test_double_commits_need_two_committed_teammates(doubles):
    frames = live_frames(doubles)
    frames = frames.assign(committing=committing(frames))
    moments = double_commits(doubles, live_frames(doubles))
    assert moments
    by_time = {round(t, 3): f for t, f in frames.groupby("time")}
    team_of = {p.id: int(p.team) for p in doubles.players}
    for m in moments:
        at = by_time[round(m.time, 3)]
        team = at[at["team"] == team_of[m.player_id]]
        assert team["committing"].sum() >= 2
        assert m.detail.startswith("with ")


def test_conceded_goals_one_moment_per_defender(doubles):
    moments = conceded_goals(doubles)
    # 8 goals, 2 defenders each.
    assert len(moments) == 16
    team_of = {p.id: p.team for p in doubles.players}
    for goal in doubles.goals:
        defenders = [m for m in moments if abs(m.time - goal.time) < 0.5]
        assert {team_of[m.player_id] for m in defenders} == {goal.team.opponent}


def test_dedupe_keeps_punished_moment():
    moments = [
        Moment("lost_challenge", "a", 10.0),
        Moment("lost_challenge", "a", 11.0, punished=True),
        Moment("lost_challenge", "b", 11.5),
        Moment("lost_challenge", "a", 20.0),
    ]
    kept = _dedupe(moments)
    expected = [("a", 11.0, True), ("b", 11.5, False), ("a", 20.0, False)]
    assert [(m.player_id, m.time, m.punished) for m in kept] == expected


def _ctx(match, name, moments=(), **stat_overrides):
    lobby = player_stats(match)
    player = match.player(name)
    stats = next(s for s in lobby if s.name == player.name)
    return _Context(match, player, replace(stats, **stat_overrides), lobby, list(moments))


def test_boost_rule_thresholds(doubles):
    assert boost_starved(_ctx(doubles, "Ragnar", pct_low_boost=20)) is None
    assert boost_starved(_ctx(doubles, "Ragnar", pct_low_boost=45)).severity == Severity.MEDIUM
    insight = boost_starved(_ctx(doubles, "Ragnar", pct_low_boost=60, pct_zero_boost=30))
    assert insight.severity == Severity.HIGH
    assert "60%" in insight.message and "30%" in insight.message


def test_over_commit_escalates_when_punished(doubles):
    pid = doubles.player("Ragnar").id
    quiet = [Moment("lost_challenge", pid, t) for t in (20.0, 60.0)]
    assert over_commit(_ctx(doubles, "Ragnar", quiet)) is None
    punished = [Moment("lost_challenge", pid, t, punished=True) for t in (20.0, 60.0)]
    insight = over_commit(_ctx(doubles, "Ragnar", punished))
    assert insight.severity == Severity.HIGH
    assert insight.title == "You over-commit on defense"
    assert len(insight.moments) == 2


def test_double_commit_rule_is_team_only(duel, doubles):
    pid = duel.players[0].id
    many = [Moment("double_commit", pid, float(t)) for t in range(10, 100, 10)]
    assert double_commit(_ctx(duel, duel.players[0].name, many)) is None
    pid = doubles.player("timehake").id
    many = [Moment("double_commit", pid, float(t)) for t in range(10, 100, 10)]
    assert double_commit(_ctx(doubles, "timehake", many)).severity == Severity.HIGH


def test_reports_for_every_player(all_matches):
    for match in all_matches:
        reports = coach(match)
        assert set(reports) == {p.id for p in match.players}
        for report in reports.values():
            severities = [i.severity for i in report.insights]
            assert severities == sorted(severities, reverse=True)
            assert len(report.strengths) <= 3
            assert len({i.rule for i in report.insights}) == len(report.insights)


def test_known_habits_are_flagged(doubles):
    reports = {r.player.name: r for r in coach(doubles).values()}
    assert "ball_chasing" in {i.rule for i in reports["2Fum2Tastic"].insights}
    assert "boost_starved" in {i.rule for i in reports["timehake"].insights}


def test_report_json(doubles):
    report = coach(doubles)[doubles.recorded_by]
    data = report_to_dict(doubles, report)
    json.dumps(data)
    assert data["player"] == "2Fum2Tastic"
    for insight in data["insights"]:
        assert insight["severity"] in {"low", "medium", "high"}
        for m in insight["moments"]:
            assert 0 <= m["clock"] <= 300


def test_cli_coach_defaults_to_recorder(capsys, monkeypatch):
    monkeypatch.delenv("RLTRACKER_PLAYER", raising=False)
    assert main(["coach", str(FIXTURES / "doubles.replay")]) == 0
    out = capsys.readouterr().out
    assert "Coaching report: 2Fum2Tastic" in out
    assert "Coaching report: timehake" not in out
    assert "What to work on" in out


@pytest.mark.parametrize("how", ["flag", "env"])
def test_cli_coach_player_choice(capsys, monkeypatch, how):
    args = ["coach", str(FIXTURES / "doubles.replay")]
    if how == "flag":
        args += ["--player", "timehake"]
    else:
        monkeypatch.setenv("RLTRACKER_PLAYER", "timehake")
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "Coaching report: timehake" in out
    assert "Coaching report: 2Fum2Tastic" not in out


def test_cli_coach_all_and_json(tmp_path, capsys):
    assert main(["coach", str(FIXTURES / "standard.replay"), "--all", "--json", str(tmp_path)]) == 0
    assert capsys.readouterr().out.count("Coaching report:") == 6
    data = json.loads((tmp_path / "standard-coaching.json").read_text(encoding="utf-8"))
    assert len(data) == 6
