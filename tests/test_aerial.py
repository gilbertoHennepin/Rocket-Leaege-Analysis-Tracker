import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

from rltracker.aerial import (  # noqa: E402
    BOOST_ACCEL,
    BOOST_PER_SECOND,
    GRAVITY,
    MIN_APEX,
    MIN_BOOST,
    MIN_DURATION,
    Aerial,
    aerial_summary,
    boosting_frames,
    find_aerials,
    guidance_command,
    surface_clearance,
)
from rltracker.cli import main  # noqa: E402
from rltracker.coach import _Context, aerial_aim, coach  # noqa: E402
from rltracker.flightcard import flight_card, save_flight_cards  # noqa: E402
from rltracker.stats import player_stats  # noqa: E402
from tests.conftest import FIXTURES  # noqa: E402


def test_guidance_command_is_zero_on_a_ballistic_path():
    pos, vel, t_go = np.array([0.0, 0.0, 100.0]), np.array([500.0, 0.0, 800.0]), 1.2
    target = pos + vel * t_go + 0.5 * GRAVITY * t_go**2  # where it lands by coasting
    zem, command = guidance_command(pos, vel, t_go, target)
    assert np.linalg.norm(zem) == pytest.approx(0, abs=1e-9)
    assert np.linalg.norm(command) == pytest.approx(0, abs=1e-9)


def test_guidance_command_closes_the_miss():
    pos, vel = np.zeros(3), np.zeros(3)
    target = np.array([0.0, 0.0, 1000.0])
    zem, command = guidance_command(pos, vel, 1.0, target)
    # Coasting from rest it would fall 325 uu, so it misses by 1325 and needs 2650 uu/s^2 up.
    assert zem == pytest.approx([0, 0, 1325])
    assert command == pytest.approx([0, 0, 2650])
    # Following that constant acceleration lands exactly on the target.
    assert pos + 0.5 * (command + GRAVITY) * 1.0**2 == pytest.approx(target)


def test_boosting_frames_fill_sparse_updates():
    # Boost updates arrive in bursts: repeated values between drops are still boosting.
    boost = np.array([50, 50, 50, 47, 47, 43, 43, 43, 43, 43, 55, 55])
    assert boosting_frames(boost).tolist() == [False, True, True, True, True, True] + [False] * 6


def test_surface_clearance():
    pos = np.array([[0, 0, 17], [0, 0, 1000], [4000, 0, 1000], [0, 5000, 1000], [0, 0, 2000]], dtype=float)
    assert surface_clearance(pos) == pytest.approx([17, 1000, 96, 120, 44])


def test_thrust_to_weight():
    assert BOOST_ACCEL / -GRAVITY[2] == pytest.approx(1.53, abs=0.01)


def test_find_aerials_meets_definition(all_matches):
    for match in all_matches:
        aerials = find_aerials(match)
        assert aerials == sorted(aerials, key=lambda a: a.start)
        for a in aerials:
            assert a.duration >= MIN_DURATION
            assert a.apex >= MIN_APEX
            assert a.boost_used >= MIN_BOOST
            assert a.delta_v == pytest.approx(a.boost_used / BOOST_PER_SECOND * BOOST_ACCEL)
            if a.thrust_alignment is not None:
                assert 0 <= a.thrust_alignment <= 1
                assert a.wasted_boost == pytest.approx(a.boost_used * (1 - a.thrust_alignment))
            if a.touched:
                assert a.start <= a.touch_time <= a.end + 0.15
                assert a.ballistic_miss is not None and a.miss_distance is None
            else:
                assert a.miss_distance is not None and a.ballistic_miss is None


def test_touched_aerials_meet_the_ball(doubles):
    """The intercept is the car's own position at the touch, so it should be next to the ball."""
    aerials = [a for a in find_aerials(doubles) if a.touched]
    assert len(aerials) >= 20
    gaps = []
    for a in aerials:
        f = a.frames
        ball = [np.interp(a.touch_time, f["time"], f[f"ball_{axis}"]) for axis in "xyz"]
        gaps.append(np.linalg.norm(np.array(ball) - np.array(a.intercept)))
    assert np.median(gaps) < 250


def test_aerial_summary(doubles):
    aerials = find_aerials(doubles)
    summary = aerial_summary(doubles, aerials)
    assert list(summary.index) == [p.name for p in doubles.players]
    assert summary["aerials"].sum() == len(aerials)
    assert summary["touches"].sum() == sum(a.touched for a in aerials)
    assert summary["thrust_alignment"].between(0, 100).all()


def _fake_aerial(player_id: str, start: float, alignment: float) -> Aerial:
    return Aerial(player_id, start, start + 2, start + 1.5, 800, 30, alignment, 40, 500, None, (0, 0, 800), None)


def test_aerial_aim_rule(doubles):
    lobby = player_stats(doubles)
    me, *others = doubles.players
    aerials = [_fake_aerial(me.id, 10.0 * i, 0.4) for i in range(5)]
    aerials += [_fake_aerial(p.id, 10.0 * i + 1, 0.8) for p in others for i in range(4)]
    ctx = _Context(doubles, me, lobby[0], lobby, [], aerials)
    insight = aerial_aim(ctx)
    assert insight is not None and insight.rule == "aerial_aim"
    assert "40%" in insight.message
    assert len(insight.moments) == 5 and all(m.kind == "aerial" for m in insight.moments)

    steady = [_fake_aerial(me.id, 10.0 * i, 0.75) for i in range(5)] + aerials[5:]
    assert aerial_aim(_Context(doubles, me, lobby[0], lobby, [], steady)) is None


def test_coach_reports_aerial_strengths(standard):
    strengths = {r.player.name: [s.title for s in r.strengths] for r in coach(standard).values()}
    assert any("Clean aerial control" in titles for titles in strengths.values())


def test_flight_card(duel, tmp_path):
    aerial = next(a for a in find_aerials(duel) if a.touched)
    fig = flight_card(duel, aerial)
    assert len(fig.axes) == 3
    plt.close(fig)
    paths = save_flight_cards(duel, tmp_path)
    assert len(paths) == len(find_aerials(duel))
    assert all(p.exists() and p.stat().st_size > 10_000 for p in paths)


def test_cli_aerials(tmp_path, capsys):
    assert main(["aerials", str(FIXTURES / "duel.replay"), "--flights", "--cards", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Thrust alignment" in out and "Flights:" in out
    assert list((tmp_path / "duel").glob("aerial-*.png"))
