# Live telemetry (RLT1 over UDP)

`rltracker stream` plays a match out over UDP in real time, the way a game
server streams state to spectators. The Unreal viewer started with `-Live`
receives it and plays it as it arrives: the cars move, goals change the
score, and coaching moments appear on the timeline as they happen. You can
pause, scrub back and replay a moment, and the viewer catches up again.
`rltracker listen` is a receiver for the terminal that prints the link quality.

```
rltracker stream                        # your latest match, at real speed, to 127.0.0.1:7777
rltracker stream match.replay --speed 2 --loop
rltracker stream --drop 0.05 --jitter-ms 40 --duplicate 0.02   # simulate a bad network
rltracker listen --seconds 30           # receive in the terminal instead of the viewer
```

In the viewer, start it with `-Live` (or `-Live=7777`), or click **Listen
for a live match** in the left panel. Add `-LiveHost=0.0.0.0` to accept a
stream from another computer (and `rltracker stream --host <viewer-ip>`).

| In the viewer | |
|---|---|
| **● LIVE** | showing the live edge |
| **Go live** / `L` | jump back to the live edge |
| Space, arrows, N / P, the timeline | pause, seek and replay moments, as with a file; playing back up to the edge rejoins it |
| top bar | packets per second, loss, jitter, buffer depth, and how far behind the edge you are |

## Why UDP

A spectator wants the newest state now, not every old state eventually. A
frame that arrives late is useless, so TCP's retransmits and head-of-line
blocking would only add lag. Instead each packet carries a sequence number
and a send time, so the receiver measures loss, reordering and jitter, drops
frames that are too old to show, and smooths playback with a small jitter
buffer.

The few messages that must arrive are made reliable by repetition instead of
retransmission:

- **Match info** (who is playing) is resent every second, so a viewer that
  starts late, or loses the first one, has it within a second.
- **Events** (goals and coaching moments) and the final **bye** are each sent
  three times, 50 ms apart, so one burst of loss can't take all three. The
  receiver keeps the first copy by event id.

## Wire format

All numbers are little-endian. Every datagram starts with a 16-byte header:

| Bytes | Type | Field |
|---|---|---|
| 0-3 | `"RLT1"` | magic |
| 4 | u8 | protocol version, 1 |
| 5 | u8 | packet type: 1 hello, 2 frame, 3 event, 4 bye |
| 6-7 | u16 | stream id: random, new for every match streamed |
| 8-11 | u32 | sequence: counts every packet of the stream, from 0 |
| 12-15 | u32 | sender time: ms since the stream started |

Then, by type:

- **Hello**: UTF-8 JSON match info: `source`, `mode`, `playlist`, `fps`,
  `recordedBy`, `players` (as in a viewer export) and `score`, the score at
  the time it was sent. It never includes the final score, which would spoil
  the match.
- **Frame**: match time (f32 s), game clock (f32 s left), live flag (u8,
  ball in play), car count (u8); then the ball and each car in player order as
  position (3 × f32, Unreal units) and rotation (quaternion xyzw, 4 × i16
  scaled by 32767). Each car is followed by its boost (u8, 0-100). A 3v3
  frame is 10 + 20 + 6 × 21 = 156 bytes, plus the header.
- **Event**: event id (u32), then UTF-8 JSON with `"event": "goal"` (a goal
  from the export, plus `score`, the score after it) or `"event": "moment"`
  (a coaching moment from the export).
- **Bye**: the header alone.

Datagrams stay under 1200 bytes, below a typical path MTU, so nothing is
fragmented.

## Receiver rules

The viewer (`Core/TelemetryCore.h`) and `rltracker listen`
(`src/rltracker/telemetry.py`) follow the same rules, and the same test
vector (a golden frame) checks both decoders.

1. **Follow one stream.** Start on a hello. Ignore other streams until the
   one being followed says bye or is silent for 2 s (the sender restarted, as
   `--loop` does with a new stream id).
2. **Reject anything malformed.** The socket is open to the network, so
   every length is checked before it is read, and non-finite numbers are
   rejected.
3. **Drop duplicates.** The last 1024 sequence numbers are remembered;
   anything older than that is counted as late and dropped.
4. **Drop stale frames.** A frame whose match time isn't newer than the
   newest one shown is too late to use.
5. **Keep one copy of each event**, by event id.

### Link statistics

- **Loss** is expected minus received, where expected runs from the lowest
  to the highest sequence number seen.
- **Jitter** is the RFC 3550 interarrival jitter: a running average (gain
  1/16) of how much the spacing between arrivals differs from the spacing
  between sends. The viewer stamps each datagram's arrival time on its
  network thread, so this is measured to the millisecond rather than to the
  game's frame rate.

### Playout buffer

Packets carry the sender's clock, so the viewer shows "now on the sender's
clock, minus a delay", and interpolates between the two frames around that
time:

    delay = fastest recent transit + one frame interval + 4 × jitter

- The fastest transit seen in the last 10-20 s is the clock offset plus the
  network's base delay. It is a windowed minimum, so it follows slow drift.
- One frame interval (smoothed, gain 1/8), because to interpolate, the frame
  after the shown time must already be here.
- Four times the jitter is headroom for packets that run late.

A clean local link plays almost at once; a jittery one buffers a little
deeper, and the top bar shows by how much.
