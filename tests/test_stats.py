import pytest

from rltracker import Team
from rltracker.stats import live_frames, player_stats, stats_table


def test_live_play_excludes_pauses(all_matches):
    for match in all_matches:
        phases = set(match.ball.loc[~match.ball["live"], "phase"])
        assert "active_play" not in phases
        assert {"kickoff_countdown", "post_goal"} & phases or not match.goals
        assert set(match.ball.loc[match.ball["live"], "phase"]) == {"active_play"}
        # Most of a match is live play.
        assert 0.6 < match.ball["live"].mean() < 0.95


def test_one_row_per_player(all_matches):
    for match in all_matches:
        table = stats_table(match)
        assert list(table.index) == [p.name for p in match.players]
        assert (table["seconds_played"] > 60).all()


def test_percentages_are_sane(all_matches):
    for match in all_matches:
        for s in player_stats(match):
            thirds = s.pct_defensive_third + s.pct_neutral_third + s.pct_offensive_third
            assert thirds == pytest.approx(100, abs=0.01)
            assert s.pct_zero_boost <= s.pct_low_boost
            for name in ("pct_low_boost", "pct_full_boost", "pct_behind_ball", "pct_supersonic", "pct_on_ground"):
                assert 0 <= getattr(s, name) <= 100
            assert 0 <= s.avg_boost <= 100
            assert 0 < s.avg_speed <= 2300


def test_team_roles_split_between_teammates(doubles):
    stats = player_stats(doubles)
    for team in Team:
        mine = [s for s in stats if s.team == team.label]
        # Someone on each team is always closest to the ball and someone is last back.
        assert sum(s.pct_closest_to_ball for s in mine) == pytest.approx(100, abs=2)
        assert sum(s.pct_last_back for s in mine) == pytest.approx(100, abs=2)


def test_no_last_back_role_in_1v1(duel):
    assert all(s.pct_last_back == 0 for s in player_stats(duel))
    assert all(s.pct_closest_to_ball == 100 for s in player_stats(duel))


def test_counts_match_events(doubles):
    stats = {s.name: s for s in player_stats(doubles)}
    for player in doubles.players:
        s = stats[player.name]
        assert s.touches == sum(t.player_id == player.id for t in doubles.touches)
        assert s.big_pads + s.small_pads == sum(b.player_id == player.id for b in doubles.boost_pickups)
        assert s.goals == player.goals
        assert s.boost_used_per_min > 0


def test_live_frames_are_team_relative(doubles):
    frames = live_frames(doubles)
    blue = frames[frames["team"] == int(Team.BLUE)]
    orange = frames[frames["team"] == int(Team.ORANGE)]
    assert (blue["rel_y"] == blue["y"]).all()
    assert (orange["rel_y"] == -orange["y"]).all()
    assert (frames["dist_to_ball"] >= 0).all()
