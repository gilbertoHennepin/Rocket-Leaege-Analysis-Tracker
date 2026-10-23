# Rocket League Analysis Tracker

Turn your Rocket League replays into stats, heat maps and coaching feedback, like
"you over-commit on defense" or "you're starving on boost". Works for 1v1, 2v2 and 3v3.

> **Status:** step 1 of 4. The replay parser and data model are done. Stats,
> heat maps, tactical analysis and the dashboard are next (see [Roadmap](#roadmap)).

## How it works

Every match you play on PC is saved as a `.replay` file. Those files contain the
full position, rotation, velocity and boost of every car and the ball, plus game
events. This project:

1. **Parses** replays with [subtr-actor](https://github.com/rlrml/subtr-actor), a
   fast Rust replay parser with Python bindings.
2. **Normalizes** the output into a typed `Match` model with tidy pandas tables.
3. **Analyzes** the match: stats, heat maps and rule-based coaching tips *(coming next)*.

```
.replay ──► subtr-actor ──► rltracker.parse_replay() ──► Match
                                                         ├─ ball     DataFrame (one row per frame)
                                                         ├─ cars     DataFrame (one row per frame per player)
                                                         ├─ players  scoreboard, team, platform, car
                                                         ├─ goals    time, scorer, ball speed, buildup
                                                         ├─ touches  ball/car position, role, shot type tags
                                                         └─ boost_pickups  pad type, amount, steals
```

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/gilbertoHennepin/Rocket-Leaege-Analysis-Tracker.git
cd Rocket-Leaege-Analysis-Tracker
python -m venv .venv
.venv\Scripts\activate        # Windows (on macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
```

## Usage

### Command line

```bash
rltracker list                      # recent replays in your Demos folder
rltracker parse                     # summarize your latest match
rltracker parse --latest 5          # your last 5 matches
rltracker parse path\to\match.replay --export data   # also save tables as Parquet
rltracker parse --export data --format csv          # ...or as CSV
```

```
doubles.replay
  2v2 Ranked  ·  2026-04-28 16:00 UTC  ·  5:00
  Blue 6 - 2 Orange

  Player               Team    Score   G   A  Sv  Sh  Touches
  luhbalenci           Blue      647   1   3   2   3       36
  timehake             Blue      997   5   1   4   5       44
  2Fum2Tastic          Orange    415   0   1   1   3       70
  Ragnar               Orange    382   2   0   0   5       40

  Goals:
    3:54 left  Blue    timehake  (61 km/h)
    ...
```

Replays are found automatically in
`Documents\My Games\Rocket League\TAGame\Demos` (including OneDrive-redirected
Documents). Set `RLTRACKER_REPLAY_DIR` to use a different folder.

### Python

```python
from rltracker import parse_replay

match = parse_replay("match.replay")
print(match.mode, match.score, match.winner)

me = match.player("timehake")
frames = match.player_frames(me.id)  # x, y, z, vx, vy, vz, boost, speed...
print(f"Average boost: {frames['boost'].mean():.0f}")
print(f"Time on 0 boost: {(frames['boost'] < 1).mean():.0%}")
```

## Data model

Coordinates are in Unreal units (1 uu = 1 cm) with the origin at center field.
Blue defends negative `y`, orange defends positive `y`. Field constants live in
`rltracker/field.py`.

| Table / list    | Contents |
| --------------- | -------- |
| `ball`          | `frame`, `time`, `clock` (seconds left), position, rotation, velocity, `speed` |
| `cars`          | `frame`, `time`, `player_id`, `team`, position, rotation, velocity, `boost` (0-100), `jumping`, `speed` |
| `goals`         | `Goal`: time, team, scorer, ball position and speed, buildup type (for example `counter_attack`) |
| `touches`       | `Touch`: time, player, ball and car position, speed change, role (`first_man`...), tags (`hard_hit`, `aerial`...) |
| `boost_pickups` | `BoostPickup`: time, player, `big`/`small` pad, amount, whether it was a steal |
| `raw_events`    | the full subtr-actor event stream (rotation roles, possession, 50/50s...) for later analysis |

Frames are sampled at 10 fps by default (`parse_replay(path, fps=30)` for more detail).

## Project layout

```
src/rltracker/
  parser.py      replay file -> Match
  models.py      Match, Player, Goal, Touch, BoostPickup, Team
  field.py       field dimensions and unit conversions
  discovery.py   find replay folders and files
  export.py      save a Match as Parquet/CSV + match.json
  cli.py         the rltracker command
tests/           pytest suite, run against real 1v1, 2v2 and 3v3 replays
```

## Development

```bash
pytest          # tests
ruff check .    # lint
ruff format .   # format
```

CI runs the tests on Windows and Linux for Python 3.10 and 3.13.

## Roadmap

- [x] **1. Replay parser and data model**: parse 1v1/2v2/3v3 replays into tidy tables
- [ ] **2. Stats and heat maps**: boost usage, time starved on boost, positioning by third, car and ball heat maps
- [ ] **3. Tactical analysis**: detect over-commits, double commits, ball chasing, poor rotation, with timestamped tips
- [ ] **4. Dashboard**: per-match reports and trends over many games

## Credits

Replay parsing by [subtr-actor](https://github.com/rlrml/subtr-actor) (MIT).
Test replays are from its test assets; see `tests/fixtures/README.md`.

## License

MIT
