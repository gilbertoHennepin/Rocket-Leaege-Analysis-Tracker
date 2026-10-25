import struct
import threading

import pytest

from rltracker import telemetry as t
from rltracker.cli import main
from rltracker.viewer_export import viewer_data
from tests.conftest import FIXTURES

# The same packet is checked byte for byte by the C++ receiver's tests
# (viewer/core-tests/test_telemetry_core.cpp), so the two sides can't drift.
GOLDEN_FRAME = bytes.fromhex(
    "524c5431010234122a000000dc050000"
    "0000484100a08f4301020000c8420000"
    "48c30080b942000000000000ff7f0000"
    "0000000090c50000884100000000825a"
    "825a2100000000000090450000884100"
    "0000007ea5825a64"
)
HALF = 0.7071068  # sin and cos of 45 degrees: a quarter turn about z


def golden_frame() -> t.Frame:
    return t.Frame(
        time=12.5,
        clock=287.25,
        live=True,
        ball=t.Body((100.0, -200.0, 92.75), (0, 0, 0, 1)),
        cars=[t.Body((0.0, -4608.0, 17.0), (0, 0, HALF, HALF)), t.Body((0.0, 4608.0, 17.0), (0, 0, -HALF, HALF))],
        boost=[33, 100],
    )


def test_golden_frame_bytes():
    assert t.encode_frame(0x1234, 42, 1500, golden_frame()) == GOLDEN_FRAME


def test_frame_round_trip():
    packet = t.decode(GOLDEN_FRAME)
    assert (packet.type, packet.stream, packet.sequence, packet.sent_ms) == (t.FRAME, 0x1234, 42, 1500)
    frame = packet.frame
    assert frame.time == 12.5 and frame.clock == 287.25 and frame.live
    assert frame.ball.pos == (100.0, -200.0, 92.75)
    assert frame.boost == [33, 100]
    assert frame.cars[1].pos == (0.0, 4608.0, 17.0)
    assert frame.cars[1].rot == pytest.approx((0, 0, -HALF, HALF), abs=1e-4)


def test_json_packets_round_trip():
    hello = t.decode(t.encode_hello(7, 0, 0, {"mode": "1v1", "players": [{"name": "少 Nix"}]}))
    assert (hello.type, hello.stream, hello.data["players"][0]["name"]) == (t.HELLO, 7, "少 Nix")

    event = t.decode(t.encode_event(7, 5, 250, 3, {"event": "goal", "team": 1}))
    assert (event.type, event.event_id, event.data) == (t.EVENT, 3, {"event": "goal", "team": 1})

    bye = t.decode(t.encode_bye(7, 9, 900))
    assert (bye.type, bye.sequence, bye.sent_ms) == (t.BYE, 9, 900)


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        GOLDEN_FRAME[:15],  # shorter than the header
        b"XXXX" + GOLDEN_FRAME[4:],  # wrong magic
        GOLDEN_FRAME[:4] + b"\x02" + GOLDEN_FRAME[5:],  # unknown version
        GOLDEN_FRAME[:5] + b"\x09" + GOLDEN_FRAME[6:],  # unknown type
        GOLDEN_FRAME[:-1],  # truncated car
        GOLDEN_FRAME[:25] + b"\x03" + GOLDEN_FRAME[26:],  # claims a third car
        t.encode_bye(1, 1, 1)[:5] + bytes([t.EVENT]) + t.encode_bye(1, 1, 1)[6:] + b"\x01",  # event without an id
        t.encode_hello(1, 0, 0, {})[:-2] + b"{",  # broken JSON
        t.encode_bye(1, 0, 0)[:5] + bytes([t.HELLO]) + t.encode_bye(1, 0, 0)[6:] + b"[1, 2]",  # JSON but not an object
    ],
)
def test_decode_rejects_bad_packets(packet):
    with pytest.raises(t.ProtocolError):
        t.decode(packet)


def test_payloads_must_fit_one_datagram():
    with pytest.raises(t.ProtocolError, match="too large"):
        t.encode_hello(1, 0, 0, {"players": ["x" * 2000]})


def test_stats_count_loss_reordering_and_duplicates():
    stats = t.StreamStats()
    for sequence in [0, 1, 2, 4, 3, 3, 7, 8]:  # 5 and 6 lost, 3 late and doubled
        stats.add(sequence, sequence * 100, sequence * 0.1)
    assert (stats.received, stats.reordered, stats.duplicates) == (7, 1, 1)
    assert (stats.expected, stats.lost) == (9, 2)
    assert stats.loss_pct == pytest.approx(200 / 9)


def test_stats_reordering_before_the_first_packet_is_not_loss():
    stats = t.StreamStats()
    for sequence in [5, 3, 4]:
        stats.add(sequence, 0, 0.0)
    assert (stats.expected, stats.lost, stats.reordered) == (3, 0, 2)


def test_stats_duplicate_window():
    stats = t.StreamStats()
    for sequence in range(3000):
        assert stats.add(sequence, sequence, sequence / 1000)
    assert not stats.add(2999 - t.DUPLICATE_WINDOW, 0, 3.0)  # too old to tell: ignored as late
    assert not stats.add(2999 - t.DUPLICATE_WINDOW + 1, 0, 3.0)  # still remembered: a duplicate
    assert (stats.late, stats.duplicates, stats.lost) == (1, 1, 0)


def test_stats_jitter():
    steady = t.StreamStats()
    for i in range(50):
        steady.add(i, i * 100, i * 0.1 + 0.020)  # always 20 ms in flight
    assert steady.jitter_ms == pytest.approx(0.0, abs=1e-6)

    bumpy = t.StreamStats()
    for i in range(200):
        bumpy.add(i, i * 100, i * 0.1 + (0.010 if i % 2 else 0.0))  # alternately 0 and 10 ms late
    assert bumpy.jitter_ms == pytest.approx(10.0, abs=0.01)  # RFC 3550 converges to the mean |difference|


def test_schedule(doubles):
    datagrams = t.schedule(doubles, speed=1.0, stream=99)
    packets = [t.decode(p) for _, p in datagrams]
    data = viewer_data(doubles)

    assert [p.sequence for p in packets] == list(range(len(packets)))
    assert {p.stream for p in packets} == {99}
    times = [at for at, _ in datagrams]
    assert times == sorted(times)
    assert packets[0].type == t.HELLO and packets[-1].type == t.BYE

    frames = [p for p in packets if p.type == t.FRAME]
    assert len(frames) == len(data["frames"]["time"])
    assert max(len(p) for _, p in datagrams) <= t.MAX_DATAGRAM

    events = [p for p in packets if p.type == t.EVENT]
    assert len(events) == t.COPIES * (len(data["goals"]) + len(data["moments"]))
    assert sum(p.type == t.BYE for p in packets) == t.COPIES
    goals = {p.event_id: p.data for p in events if p.data["event"] == "goal"}
    final = goals[max(goals)]["score"]
    assert final == data["score"]

    # Match info carries the score so far, never the final result.
    hellos = [p.data for p in packets if p.type == t.HELLO]
    assert hellos[0]["score"] == {"blue": 0, "orange": 0}
    assert hellos[-1]["score"] == final
    assert "frames" not in hellos[0] and "goals" not in hellos[0]


def test_listener_copes_with_a_bad_network(duel):
    # At 10x speed frames are 10 ms apart, so 30 ms of jitter reorders them too.
    datagrams = t.schedule(duel, speed=10.0, stream=5)
    impaired = t.impair(datagrams, t.Impairment(drop=0.05, duplicate=0.02, jitter_ms=30, seed=2))
    listener = t.Listener()
    for at, packet in impaired:
        listener.handle(packet, at + 0.002)

    s = listener.stats
    assert 3 < s.loss_pct < 8
    assert s.duplicates > 0 and s.reordered > 0
    assert 0 < s.jitter_ms < 30
    assert listener.stale_frames > 0  # frames that lost a race with a newer one
    assert listener.ended

    # Three copies of each event: with 5% loss, all three are lost about once in 8000.
    sent = {p.event_id for p in map(t.decode, (p for _, p in datagrams)) if p.type == t.EVENT}
    assert set(listener.events) == sent
    assert listener.score() == viewer_data(duel)["score"]


def test_listener_waits_for_match_info(duel):
    packets = [p for _, p in t.schedule(duel, stream=1)]
    frame = next(p for p in packets if p[5] == t.FRAME)
    listener = t.Listener()
    assert listener.handle(frame, 0.0) is None  # a frame before any match info
    assert listener.ignored == 1 and listener.stream is None
    assert listener.handle(packets[0], 0.1).type == t.HELLO
    assert listener.stream == 1


def test_listener_stream_takeover(duel):
    a = [p for _, p in t.schedule(duel, stream=1)]
    b = [p for _, p in t.schedule(duel, stream=2)]
    listener = t.Listener()
    listener.handle(a[0], 0.0)
    listener.handle(a[1], 0.1)

    assert listener.handle(b[0], 0.2) is None  # stream 1 is still going
    assert listener.stream == 1
    assert listener.handle(b[0], 0.1 + t.STREAM_TIMEOUT + 0.1).type == t.HELLO  # 1 went quiet: 2 takes over
    assert listener.stream == 2 and listener.stats.received == 1

    listener.handle(b[-1], 3.0)  # bye
    assert listener.ended
    assert listener.handle(a[0], 3.1).type == t.HELLO  # after a bye any new stream may start
    assert listener.stream == 1 and not listener.ended


def test_loopback(duel):
    """A real UDP round trip on this computer."""
    datagrams = t.schedule(duel, speed=200.0)
    ready = threading.Event()
    port: list[int] = []
    result: list[t.Listener] = []

    def on_ready(p: int) -> None:
        port.append(p)
        ready.set()

    thread = threading.Thread(target=lambda: result.append(t.listen(0, seconds=10, ready=on_ready)))
    thread.start()
    assert ready.wait(5)
    t.send(datagrams, ("127.0.0.1", port[0]))
    thread.join(15)

    listener = result[0]
    assert listener.ended  # at the first bye, so its two repeats aren't read
    assert listener.stats.received == len(datagrams) - (t.COPIES - 1) and listener.stats.lost == 0
    assert listener.frames == sum(1 for _, p in datagrams if p[5] == t.FRAME)


def test_cli_stream_and_listen(capsys):
    ready = threading.Event()
    port: list[int] = []
    result: list[t.Listener] = []

    def on_ready(p: int) -> None:
        port.append(p)
        ready.set()

    thread = threading.Thread(target=lambda: result.append(t.listen(0, seconds=10, ready=on_ready)))
    thread.start()
    assert ready.wait(5)
    replay = str(FIXTURES / "duel.replay")
    assert main(["stream", replay, "--port", str(port[0]), "--speed", "200", "--drop", "0.1", "--seed", "3"]) == 0
    thread.join(15)

    out = capsys.readouterr().out
    assert "Streaming duel.replay" in out and "sent" in out
    assert result[0].ended and 5 < result[0].stats.loss_pct < 15


def test_cli_listen_without_a_stream(capsys):
    assert main(["listen", "--port", "0", "--seconds", "0.3"]) == 1
    assert "waiting for match info" in capsys.readouterr().out


def test_header_layout():
    """The header documented in docs/telemetry.md."""
    assert t.HEADER.size == 16 and t.FRAME_HEAD.size == 10 and t.BODY.size == 20
    magic, version, kind, stream, sequence, sent = struct.unpack("<4sBBHII", GOLDEN_FRAME[:16])
    assert (magic, version, kind, stream, sequence, sent) == (b"RLT1", 1, t.FRAME, 0x1234, 42, 1500)
