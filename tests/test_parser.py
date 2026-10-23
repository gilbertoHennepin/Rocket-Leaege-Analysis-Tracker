import math

import pytest

from rltracker import ReplayParseError, Team, parse_replay
from rltracker.field import FIELD_HALF_LENGTH, FIELD_HALF_WIDTH, GOAL_DEPTH


@pytest.mark.parametrize(
    ("fixture", "mode", "score"),
    [("duel", "1v1", (0, 4)), ("doubles", "2v2", (6, 2)), ("standard", "3v3", (2, 1))],
)
def test_metadata(request, fixture, mode, score):
    match = request.getfixturevalue(fixture)
    assert match.mode == mode
    assert (match.score[Team.BLUE], match.score[Team.ORANGE]) == score
    assert len(match.team(Team.BLUE)) == len(match.team(Team.ORANGE)) == match.team_size
    assert match.playlist == "Ranked"
    assert match.played_at is not None


def test_players(doubles):
    names = [p.name for p in doubles.players]
    assert names == ["luhbalenci", "timehake", "2Fum2Tastic", "Ragnar"]
    timehake = doubles.player("TIMEHAKE")
    assert timehake.team == Team.BLUE
    assert timehake.goals == 5
    assert timehake.id.startswith("epic:")
    assert doubles.player("Ragnar").id == "steam:76561198078858520"
    assert len({p.id for p in doubles.players}) == len(doubles.players)
    with pytest.raises(KeyError):
        doubles.player("nobody")


def test_frame_tables_line_up(all_matches):
    for match in all_matches:
        assert len(match.cars) == len(match.ball) * len(match.players)
        assert match.ball["time"].is_monotonic_increasing
        for player in match.players:
            frames = match.player_frames(player.id)
            assert frames["time"].tolist() == match.ball["time"].tolist()


def test_positions_stay_on_the_field(all_matches):
    for match in all_matches:
        for table in (match.ball, match.cars):
            assert table["x"].abs().max() <= FIELD_HALF_WIDTH + 100
            assert table["y"].abs().max() <= FIELD_HALF_LENGTH + GOAL_DEPTH + 100


def test_boost_is_on_the_in_game_scale(all_matches):
    for match in all_matches:
        assert match.cars["boost"].between(0, 100).all()
        assert match.cars["boost"].max() == pytest.approx(100, abs=0.5)
        # Everyone spawns with 33 boost at kickoff.
        first = match.cars[match.cars["frame"] == 0]
        assert first["boost"].round().eq(33).all()


def test_cars_spawn_on_their_own_half(all_matches):
    for match in all_matches:
        first = match.cars[match.cars["frame"] == 0]
        assert (first.loc[first["team"] == Team.BLUE, "y"] < 0).all()
        assert (first.loc[first["team"] == Team.ORANGE, "y"] > 0).all()


def test_goals_match_the_scoreboard(all_matches):
    for match in all_matches:
        assert len(match.goals) == sum(match.score.values())
        for team in Team:
            assert sum(g.team == team for g in match.goals) == match.score[team]
        goals_by_player = {p.id: 0 for p in match.players}
        for g in match.goals:
            goals_by_player[g.scorer_id] += 1
        assert goals_by_player == {p.id: p.goals for p in match.players}


def test_goals_end_up_in_the_right_net(all_matches):
    for match in all_matches:
        for g in match.goals:
            # Blue attacks positive y.
            sign = 1 if g.team == Team.BLUE else -1
            assert g.ball_position[1] * sign > FIELD_HALF_LENGTH - 200


def test_touches_and_pickups(doubles):
    ids = {p.id for p in doubles.players}
    assert len(doubles.touches) > 100
    assert {t.player_id for t in doubles.touches} == ids
    assert all(not math.isnan(t.ball_position[0]) for t in doubles.touches)
    assert [t.time for t in doubles.touches] == sorted(t.time for t in doubles.touches)

    assert {b.pad_type for b in doubles.boost_pickups} <= {"big", "small"}
    big = [b.amount for b in doubles.boost_pickups if b.pad_type == "big"]
    assert big and max(big) <= 100


def test_clock(doubles):
    assert doubles.clock_at(0) == 300
    assert doubles.clock_at(doubles.goals[0].time) < 300


def test_lower_fps_samples_fewer_frames(doubles):
    from tests.conftest import FIXTURES

    low = parse_replay(FIXTURES / "doubles.replay", fps=5)
    assert len(low.ball) == pytest.approx(len(doubles.ball) / 2, rel=0.05)


def test_missing_and_broken_files(tmp_path):
    with pytest.raises(ReplayParseError, match="not found"):
        parse_replay(tmp_path / "nope.replay")
    bad = tmp_path / "bad.replay"
    bad.write_bytes(b"not a replay")
    with pytest.raises(ReplayParseError, match="Could not parse"):
        parse_replay(bad)
