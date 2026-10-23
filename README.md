# Rocket League Analysis Tracker

Turn your Rocket League replays into stats, heat maps and coaching feedback, like
"you over-commit on defense" or "you're starving on boost". Works for 1v1, 2v2 and 3v3.

> **Status:** step 2 of 4. Parsing, per-player stats and heat maps are done.
> Tactical analysis and the dashboard are next (see [Roadmap](#roadmap)).

![Heat maps for a 2v2 match](docs/images/overview-2v2.png)

## How it works

Every match you play on PC is saved as a `.replay` file. Those files contain the
full position, rotation, velocity and boost of every car and the ball, plus game
events. This project:

1. **Parses** replays with [subtr-actor](https://github.com/rlrml/subtr-actor), a
   fast Rust replay parser with Python bindings.
2. **Normalizes** the output into a typed `Match` model with tidy pandas tables.
3. **Analyzes** the match: per-player stats and heat maps, with rule-based
   coaching tips *(coming next)*. Analysis uses live play only, so kickoff
   countdowns and post-goal pauses don't skew the numbers.

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
rltracker stats                     # boost, positioning and movement stats
rltracker stats --latest 10 --csv stats            # save as CSV too
rltracker heatmap                   # heat maps and touch maps -> ./heatmaps/<replay>/
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

`rltracker stats` prints a side-by-side comparison of every player:

```
doubles.replay  (2v2, live play only)
                              luhbalenci    timehake 2Fum2Tastic      Ragnar
                                    Blue        Blue      Orange      Orange
  Boost
    Average boost                     44          36          51          54
    Time under 25 boost              44%         52%         33%         29%
    Time on 0 boost                  21%         26%         14%          7%
    Big / small pads             22 / 66     17 / 75     31 / 61     14 / 75
  Positioning
    Defensive third                  46%         40%         39%         39%
    Behind the ball                  80%         77%         69%         64%
    Closest to ball on team          49%         51%         65%         35%
    Last back on team                57%         43%         46%         54%
  Movement
    Supersonic                       26%         13%         13%          9%
  ...
```

`rltracker heatmap` saves an overview (above) plus one image per player, the
ball and the touch map. Player maps are always drawn with that player
attacking to the right, so maps from blue and orange games compare directly.

<img src="docs/images/player-heatmap.png" alt="Heat map for one player" width="520">

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

from rltracker.stats import stats_table
from rltracker.heatmap import player_heatmap

print(stats_table(match).T)  # one column per player
player_heatmap(match, "timehake").savefig("timehake.png")
```

## Data model

Coordinates are in Unreal units (1 uu = 1 cm) with the origin at center field.
Blue defends negative `y`, orange defends positive `y`. Field constants live in
`rltracker/field.py`.

| Table / list    | Contents |
| --------------- | -------- |
| `ball`          | `frame`, `time`, `clock` (seconds left), `phase`, `live`, position, rotation, velocity, `speed` |
| `cars`          | `frame`, `time`, `player_id`, `team`, `phase`, `live`, position, rotation, velocity, `boost` (0-100), `jumping`, `speed` |
| `goals`         | `Goal`: time, team, scorer, ball position and speed, buildup type (for example `counter_attack`) |
| `touches`       | `Touch`: time, player, ball and car position, speed change, role (`first_man`...), tags (`hard_hit`, `aerial`...) |
| `boost_pickups` | `BoostPickup`: time, player, `big`/`small` pad, amount, whether it was a steal |
| `raw_events`    | the full subtr-actor event stream (rotation roles, possession, 50/50s...) for later analysis |

Frames are sampled at 10 fps by default (`parse_replay(path, fps=30)` for more detail).
`phase` is the gameplay phase (`active_play`, `kickoff_countdown`,
`kickoff_waiting_for_touch`, `post_goal`) and `live` is True during `active_play`.

## Stats

All stats come from live play. Percentages are share of live time.

| Group | Stats |
| ----- | ----- |
| Boost | average boost, time under 25 / at 0 / at 100, boost collected and used per minute, big and small pads, big pads stolen from the opponent's half |
| Positioning | time in defensive / neutral / offensive third, time behind the ball, time as closest to the ball and as last back on your team, average distance to the ball |
| Movement | average speed, time supersonic, time on the ground, time high in the air |
| Ball | touches, touches per minute, hard hits, aerial touches |

## Project layout

```
src/rltracker/
  parser.py      replay file -> Match
  models.py      Match, Player, Goal, Touch, BoostPickup, Team
  field.py       field dimensions and unit conversions
  discovery.py   find replay folders and files
  stats.py       per-player stats from live play
  heatmap.py     field diagram, heat maps, touch maps
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
- [x] **2. Stats and heat maps**: boost usage, time starved on boost, positioning by third, car and ball heat maps
- [ ] **3. Tactical analysis**: detect over-commits, double commits, ball chasing, poor rotation, with timestamped tips
- [ ] **4. Dashboard**: per-match reports and trends over many games

## Credits

Replay parsing by [subtr-actor](https://github.com/rlrml/subtr-actor) (MIT).
Test replays are from its test assets; see `tests/fixtures/README.md`.

## License

MIT
