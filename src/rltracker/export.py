"""Save a parsed match to disk so later analysis doesn't re-parse the replay."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal

import pandas as pd

from .models import Match

Format = Literal["parquet", "csv"]


def events_frame(items: list) -> pd.DataFrame:
    """Flatten a list of event dataclasses (goals, touches...) into a table."""
    rows = []
    for item in items:
        row = asdict(item)
        for key, value in list(row.items()):
            if isinstance(value, tuple) and len(value) == 3:
                row.pop(key)
                for axis, v in zip("xyz", value, strict=True):
                    row[f"{key}_{axis}"] = v
            elif isinstance(value, dict):
                row.pop(key)
                for k, v in value.items():
                    row[f"{key}_{k}"] = v
        if "team" in row:
            row["team"] = int(row["team"])
        rows.append(row)
    return pd.DataFrame(rows)


def match_summary(match: Match) -> dict:
    return {
        "source": str(match.source),
        "played_at": match.played_at.isoformat() if match.played_at else None,
        "playlist": match.playlist,
        "mode": match.mode,
        "duration_seconds": round(match.duration, 2),
        "score": {team.label.lower(): goals for team, goals in match.score.items()},
        "fps": match.fps,
        "players": [{**asdict(p), "team": p.team.label.lower()} for p in match.players],
    }


def export_match(match: Match, out_dir: str | Path, fmt: Format = "parquet") -> Path:
    """Write the match as a folder of tables plus a ``match.json`` summary."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    tables = {
        "ball": match.ball,
        "cars": match.cars,
        "goals": events_frame(match.goals),
        "touches": events_frame(match.touches),
        "boost_pickups": events_frame(match.boost_pickups),
    }
    for name, table in tables.items():
        if fmt == "parquet":
            table.to_parquet(out / f"{name}.parquet", index=False)
        else:
            table.to_csv(out / f"{name}.csv", index=False)

    (out / "match.json").write_text(json.dumps(match_summary(match), indent=2, ensure_ascii=False), encoding="utf-8")
    return out
