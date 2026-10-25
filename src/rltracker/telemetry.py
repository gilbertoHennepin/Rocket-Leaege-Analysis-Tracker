"""Live match telemetry over UDP.

``rltracker stream`` plays a match out as UDP datagrams in real time, the way
a game server streams state to spectators, and the Unreal viewer (started
with ``-Live``) or ``rltracker listen`` receives it. The wire format and the
receiver rules are documented in ``docs/telemetry.md``.

Why UDP: a spectator wants the newest state now, not every old state
eventually. A late frame is useless, so TCP's retransmits and head-of-line
blocking would only add lag. Instead each packet carries a sequence number
and a send timestamp, so the receiver can measure loss, reordering and
jitter, drop stale frames and smooth playback with a small jitter buffer.
The few messages that must arrive (match info and events) are made reliable
by repetition: match info is resent every second, and each event (and the
final bye) is sent three times, with an id the receiver de-duplicates on.
"""

from __future__ import annotations

import json
import random
import socket
import struct
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .models import Match
from .viewer_export import viewer_data

MAGIC = b"RLT1"
VERSION = 1
DEFAULT_PORT = 7777

HELLO, FRAME, EVENT, BYE = 1, 2, 3, 4
TYPE_NAMES = {HELLO: "hello", FRAME: "frame", EVENT: "event", BYE: "bye"}

# magic, version, type, stream id, sequence, sender time (ms since stream start)
HEADER = struct.Struct("<4sBBHII")
# match time, game clock, live flag, car count
FRAME_HEAD = struct.Struct("<ffBB")
# position xyz (float32), rotation quaternion xyzw (int16, scaled by 32767)
BODY = struct.Struct("<fff4h")
BOOST = struct.Struct("<B")
EVENT_ID = struct.Struct("<I")

HELLO_INTERVAL = 1.0  # s between match info repeats
COPIES = 3  # each event, and the bye, is sent this many times
COPY_SPACING = 0.05  # s between copies, so one burst of loss can't take them all
MAX_DATAGRAM = 1200  # stay under a typical path MTU
DUPLICATE_WINDOW = 1024  # how many recent sequence numbers the receiver remembers
STREAM_TIMEOUT = 2.0  # s of silence before another stream may take over


@dataclass(frozen=True)
class Body:
    pos: tuple[float, float, float]
    rot: tuple[float, float, float, float]  # quaternion x, y, z, w


@dataclass(frozen=True)
class Frame:
    time: float
    clock: float
    live: bool
    ball: Body
    cars: list[Body]
    boost: list[int]  # 0-100, same order as cars


@dataclass(frozen=True)
class Packet:
    type: int
    stream: int
    sequence: int
    sent_ms: int
    frame: Frame | None = None
    event_id: int | None = None
    data: dict[str, Any] | None = None  # hello and event payloads


class ProtocolError(ValueError):
    pass


# Encoding --------------------------------------------------------------------


def _quat16(q: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    norm = max(sum(c * c for c in q) ** 0.5, 1e-9)
    return tuple(max(-32767, min(32767, round(c / norm * 32767))) for c in q)  # type: ignore[return-value]


def _header(kind: int, stream: int, sequence: int, sent_ms: int) -> bytes:
    return HEADER.pack(MAGIC, VERSION, kind, stream & 0xFFFF, sequence & 0xFFFFFFFF, sent_ms & 0xFFFFFFFF)


def _json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()


def _check_size(kind: int, packet: bytes) -> bytes:
    if len(packet) > MAX_DATAGRAM:
        raise ProtocolError(f"{TYPE_NAMES[kind]} payload too large for one datagram ({len(packet)} bytes)")
    return packet


def encode_hello(stream: int, sequence: int, sent_ms: int, info: dict[str, Any]) -> bytes:
    return _check_size(HELLO, _header(HELLO, stream, sequence, sent_ms) + _json(info))


def encode_frame(stream: int, sequence: int, sent_ms: int, frame: Frame) -> bytes:
    head = FRAME_HEAD.pack(frame.time, frame.clock, frame.live, len(frame.cars))
    parts = [_header(FRAME, stream, sequence, sent_ms), head, BODY.pack(*frame.ball.pos, *_quat16(frame.ball.rot))]
    for car, boost in zip(frame.cars, frame.boost, strict=True):
        parts.append(BODY.pack(*car.pos, *_quat16(car.rot)) + BOOST.pack(max(0, min(100, int(boost)))))
    return b"".join(parts)


def encode_event(stream: int, sequence: int, sent_ms: int, event_id: int, event: dict[str, Any]) -> bytes:
    return _check_size(EVENT, _header(EVENT, stream, sequence, sent_ms) + EVENT_ID.pack(event_id) + _json(event))


def encode_bye(stream: int, sequence: int, sent_ms: int) -> bytes:
    return _header(BYE, stream, sequence, sent_ms)


# Decoding --------------------------------------------------------------------


def decode(packet: bytes) -> Packet:
    if len(packet) < HEADER.size:
        raise ProtocolError("packet shorter than the header")
    magic, version, kind, stream, sequence, sent_ms = HEADER.unpack_from(packet)
    if magic != MAGIC or version != VERSION:
        raise ProtocolError("not an rltracker telemetry packet")
    body = memoryview(packet)[HEADER.size :]
    if kind == FRAME:
        return Packet(kind, stream, sequence, sent_ms, frame=_decode_frame(body))
    if kind == HELLO:
        return Packet(kind, stream, sequence, sent_ms, data=_decode_json(kind, body))
    if kind == EVENT:
        if len(body) < EVENT_ID.size:
            raise ProtocolError("event without an id")
        (event_id,) = EVENT_ID.unpack_from(body)
        return Packet(
            kind, stream, sequence, sent_ms, event_id=event_id, data=_decode_json(kind, body[EVENT_ID.size :])
        )
    if kind == BYE:
        return Packet(kind, stream, sequence, sent_ms)
    raise ProtocolError(f"unknown packet type {kind}")


def _decode_json(kind: int, body: memoryview) -> dict[str, Any]:
    try:
        data = json.loads(bytes(body))
    except ValueError as e:
        raise ProtocolError(f"bad {TYPE_NAMES[kind]} payload") from e
    if not isinstance(data, dict):
        raise ProtocolError(f"bad {TYPE_NAMES[kind]} payload")
    return data


def _decode_frame(body: memoryview) -> Frame:
    if len(body) < FRAME_HEAD.size + BODY.size:
        raise ProtocolError("truncated frame")
    t, clock, live, n_cars = FRAME_HEAD.unpack_from(body)
    if len(body) != FRAME_HEAD.size + BODY.size + n_cars * (BODY.size + BOOST.size):
        raise ProtocolError("frame length doesn't match its car count")

    def body_at(offset: int) -> Body:
        x, y, z, *q = BODY.unpack_from(body, offset)
        return Body((x, y, z), tuple(c / 32767 for c in q))  # type: ignore[arg-type]

    offset = FRAME_HEAD.size
    ball = body_at(offset)
    offset += BODY.size
    cars, boost = [], []
    for _ in range(n_cars):
        cars.append(body_at(offset))
        boost.append(body[offset + BODY.size])
        offset += BODY.size + BOOST.size
    return Frame(t, clock, bool(live), ball, cars, boost)


# Receiver statistics ---------------------------------------------------------


@dataclass
class StreamStats:
    """Loss, reordering, duplicates and jitter from sequence numbers and timestamps.

    Duplicates are spotted with a sliding window over the last
    ``DUPLICATE_WINDOW`` sequence numbers, like the anti-replay window in
    IPsec. Jitter is the RFC 3550 (RTP) interarrival jitter: a running average
    of how much the spacing between arrivals differs from the spacing between
    sends. The Unreal viewer's ``LinkStats`` (Core/TelemetryCore.h) is the same
    algorithm in C++.
    """

    received: int = 0
    duplicates: int = 0
    reordered: int = 0
    late: int = 0  # older than the window, so too late to tell or to use
    first: int | None = None
    highest: int | None = None
    jitter_ms: float = 0.0
    _seen: list[bool] = field(default_factory=lambda: [False] * DUPLICATE_WINDOW, repr=False)
    _last_transit: float | None = field(default=None, repr=False)

    def add(self, sequence: int, sent_ms: int, arrival_s: float) -> bool:
        """Record a packet. Returns False for a duplicate (or a very late one), which should be ignored."""
        if self.highest is None or self.first is None:
            self.first = self.highest = sequence
        elif sequence > self.highest:
            # Slots for the numbers we skip over now stand for newer packets.
            for s in range(self.highest + 1, min(sequence, self.highest + DUPLICATE_WINDOW) + 1):
                self._seen[s % DUPLICATE_WINDOW] = False
            self.highest = sequence
        elif self.highest - sequence >= DUPLICATE_WINDOW:
            self.late += 1
            return False
        elif self._seen[sequence % DUPLICATE_WINDOW]:
            self.duplicates += 1
            return False
        else:
            self.reordered += 1
            self.first = min(self.first, sequence)
        self._seen[sequence % DUPLICATE_WINDOW] = True
        self.received += 1

        transit = arrival_s * 1000.0 - sent_ms
        if self._last_transit is not None:
            self.jitter_ms += (abs(transit - self._last_transit) - self.jitter_ms) / 16.0
        self._last_transit = transit
        return True

    @property
    def expected(self) -> int:
        return 0 if self.first is None or self.highest is None else self.highest - self.first + 1

    @property
    def lost(self) -> int:
        return max(0, self.expected - self.received)

    @property
    def loss_pct(self) -> float:
        return 100.0 * self.lost / self.expected if self.expected else 0.0


# Sending ---------------------------------------------------------------------


def match_info(match: Match) -> dict[str, Any]:
    """The match header a receiver needs before frames make sense (no frames, no final score)."""
    data = viewer_data(match)
    return {k: data[k] for k in ("source", "mode", "playlist", "fps", "recordedBy", "players")}


def match_frames(data: dict[str, Any]) -> Iterator[Frame]:
    """Frames from a viewer export document (see ``viewer_export.viewer_data``)."""
    frames = data["frames"]
    for i, t in enumerate(frames["time"]):
        ball = Body(tuple(frames["ball"]["pos"][3 * i : 3 * i + 3]), tuple(frames["ball"]["rot"][4 * i : 4 * i + 4]))
        cars = [Body(tuple(c["pos"][3 * i : 3 * i + 3]), tuple(c["rot"][4 * i : 4 * i + 4])) for c in frames["cars"]]
        boost = [int(c["boost"][i]) for c in frames["cars"]]
        yield Frame(t, frames["clock"][i], bool(frames["live"][i]), ball, cars, boost)  # type: ignore[arg-type]


def _score(blue: int, orange: int) -> dict[str, int]:
    return {"blue": blue, "orange": orange}


def match_events(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Goals and coaching moments as events, in time order. Each goal carries the score after it."""
    goals = []
    totals = [0, 0]
    for goal in sorted(data["goals"], key=lambda g: g["time"]):
        totals[goal["team"]] += 1
        goals.append({"event": "goal", **goal, "score": _score(*totals)})
    events = goals + [{"event": "moment", **m} for m in data["moments"]]
    events.sort(key=lambda e: e["time"])
    return events


def score_at(events: list[dict[str, Any]], match_time: float) -> dict[str, int]:
    score = _score(0, 0)
    for event in events:
        if event["event"] == "goal" and event["time"] <= match_time:
            score = event["score"]
    return score


@dataclass
class Impairment:
    """Simulated network trouble, to show the receiver coping with it."""

    drop: float = 0.0  # chance each datagram is lost
    duplicate: float = 0.0  # chance each datagram arrives twice
    jitter_ms: float = 0.0  # extra random delay, up to this much (reorders packets)
    seed: int | None = None


def schedule(match: Match, speed: float = 1.0, stream: int | None = None) -> list[tuple[float, bytes]]:
    """Every datagram for the match with its send time in seconds from the start, in order.

    Each call is a new stream with its own random id (unless one is given), so
    a receiver can tell a restarted or looped stream from the old one.
    """
    stream = random.getrandbits(16) if stream is None else stream
    data = viewer_data(match)
    frames = list(match_frames(data))
    start = frames[0].time if frames else 0.0
    duration = (frames[-1].time - start) / speed if frames else 0.0
    info = match_info(match)
    events = match_events(data)

    # (send time, type, payload), then numbered in send order.
    timeline: list[tuple[float, int, Any]] = [((f.time - start) / speed, FRAME, f) for f in frames]
    for i in range(int(duration // HELLO_INTERVAL) + 1):
        at = i * HELLO_INTERVAL
        timeline.append((at, HELLO, {**info, "score": score_at(events, start + at * speed)}))
    for event_id, event in enumerate(events):
        at = max(0.0, (event["time"] - start) / speed)
        timeline += [(at + copy * COPY_SPACING, EVENT, (event_id, event)) for copy in range(COPIES)]
    timeline += [(duration + 0.1 + copy * COPY_SPACING, BYE, None) for copy in range(COPIES)]
    timeline.sort(key=lambda item: (item[0], item[1] != HELLO))  # match info first at time 0

    out = []
    for sequence, (at, kind, payload) in enumerate(timeline):
        sent_ms = round(at * 1000)
        if kind == FRAME:
            packet = encode_frame(stream, sequence, sent_ms, payload)
        elif kind == HELLO:
            packet = encode_hello(stream, sequence, sent_ms, payload)
        elif kind == EVENT:
            packet = encode_event(stream, sequence, sent_ms, *payload)
        else:
            packet = encode_bye(stream, sequence, sent_ms)
        out.append((at, packet))
    return out


def impair(datagrams: list[tuple[float, bytes]], impairment: Impairment) -> list[tuple[float, bytes]]:
    """Apply simulated loss, duplication and jitter. Jitter can reorder packets."""
    rng = random.Random(impairment.seed)
    out = []
    for at, packet in datagrams:
        if rng.random() < impairment.drop:
            continue
        copies = 2 if rng.random() < impairment.duplicate else 1
        for _ in range(copies):
            out.append((at + rng.uniform(0, impairment.jitter_ms) / 1000.0, packet))
    return sorted(out, key=lambda item: item[0])


def send(
    datagrams: list[tuple[float, bytes]],
    address: tuple[str, int],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Send datagrams at their scheduled times. Returns how many were sent."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        start = clock()
        for at, packet in datagrams:
            wait = at - (clock() - start)
            if wait > 0:
                sleep(wait)
            sock.sendto(packet, address)
        return len(datagrams)
    finally:
        sock.close()


# Receiving -------------------------------------------------------------------


@dataclass
class Listener:
    """What ``rltracker listen`` keeps track of while receiving a stream.

    Follows the receiver rules in docs/telemetry.md, the same ones the Unreal
    viewer's ``StreamReceiver`` uses.
    """

    stream: int | None = None
    stats: StreamStats = field(default_factory=StreamStats)
    info: dict[str, Any] | None = None
    events: dict[int, dict[str, Any]] = field(default_factory=dict)
    frames: int = 0
    stale_frames: int = 0  # arrived after a newer frame, so too late to show
    last_frame: Frame | None = None
    ended: bool = False
    errors: int = 0  # not telemetry, or malformed
    ignored: int = 0  # not from the stream being followed
    _last_arrival: float = field(default=0.0, repr=False)

    def handle(self, datagram: bytes, arrival_s: float) -> Packet | None:
        """Take one datagram. Returns the packet if it is new and in use, otherwise None."""
        try:
            packet = decode(datagram)
        except ProtocolError:
            self.errors += 1
            return None
        if packet.stream != self.stream:
            # A stream starts with its match info, and can only take over from
            # one that has ended or gone quiet (its sender restarted).
            free = self.stream is None or self.ended or arrival_s - self._last_arrival > STREAM_TIMEOUT
            if packet.type != HELLO or not free:
                self.ignored += 1
                return None
            self._follow(packet.stream)
        self._last_arrival = arrival_s
        if not self.stats.add(packet.sequence, packet.sent_ms, arrival_s):
            return None

        if packet.type == HELLO:
            self.info = packet.data
        elif packet.type == EVENT and packet.event_id is not None and packet.data is not None:
            if packet.event_id in self.events:
                return None  # another copy of an event we already have
            self.events[packet.event_id] = packet.data
        elif packet.type == FRAME and packet.frame is not None:
            if self.last_frame is not None and packet.frame.time <= self.last_frame.time:
                self.stale_frames += 1
                return None
            self.frames += 1
            self.last_frame = packet.frame
        elif packet.type == BYE:
            self.ended = True
        return packet

    def _follow(self, stream: int) -> None:
        self.stream = stream
        self.stats = StreamStats()
        self.info = None
        self.events = {}
        self.frames = self.stale_frames = 0
        self.last_frame = None
        self.ended = False

    def score(self) -> dict[str, int]:
        """The latest score: from match info, or from a goal since then (scores only go up)."""
        scores = [self.info["score"]] if self.info else []
        scores += [e["score"] for e in self.events.values() if e.get("event") == "goal"]
        return max(scores, key=lambda s: s["blue"] + s["orange"], default=_score(0, 0))

    def status(self) -> str:
        s = self.stats
        if self.info is None:
            return f"waiting for match info  |  {self.ignored} packets ignored  |  {self.errors} bad packets"
        score = self.score()
        clock = f"clock {self.last_frame.clock:.0f}s" if self.last_frame else "no frames yet"
        return (
            f"{self.info['mode']} {score['blue']}-{score['orange']}  |  {s.received} packets  |  "
            f"lost {s.loss_pct:.1f}%  |  reordered {s.reordered}  |  duplicates {s.duplicates}  |  "
            f"stale frames {self.stale_frames}  |  jitter {s.jitter_ms:.1f} ms  |  "
            f"{len(self.events)} events  |  {clock}"
        )


def listen(
    port: int = DEFAULT_PORT,
    seconds: float | None = None,
    on_status: Callable[[Listener], None] | None = None,
    host: str = "127.0.0.1",
    ready: Callable[[int], None] | None = None,
) -> Listener:
    """Receive a stream until it ends (or ``seconds`` pass), reporting status once a second."""
    listener = Listener()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
        sock.settimeout(0.2)
        if ready:
            ready(sock.getsockname()[1])
        start = last_status = time.monotonic()
        while not listener.ended and (seconds is None or time.monotonic() - start < seconds):
            try:
                datagram, _ = sock.recvfrom(65535)
                listener.handle(datagram, time.monotonic())
            except TimeoutError:
                pass
            now = time.monotonic()
            if on_status and now - last_status >= 1.0:
                on_status(listener)
                last_status = now
        if on_status:
            on_status(listener)
        return listener
    finally:
        sock.close()
