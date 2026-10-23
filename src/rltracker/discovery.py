"""Find Rocket League replay files on disk."""

from __future__ import annotations

import os
from pathlib import Path

REPLAY_DIR_ENV = "RLTRACKER_REPLAY_DIR"
_DEMOS = Path("My Games") / "Rocket League" / "TAGame" / "Demos"


def candidate_replay_dirs() -> list[Path]:
    """Places the game saves replays on PC, most likely first.

    ``RLTRACKER_REPLAY_DIR`` overrides everything. Otherwise we check the
    regular Documents folder and the OneDrive-redirected one, which Windows
    often uses by default.
    """
    override = os.environ.get(REPLAY_DIR_ENV)
    if override:
        return [Path(override).expanduser()]

    home = Path.home()
    docs = [home / "Documents", home / "OneDrive" / "Documents"]
    if userprofile := os.environ.get("USERPROFILE"):
        docs.append(Path(userprofile) / "Documents")
    if onedrive := os.environ.get("OneDrive"):
        docs.append(Path(onedrive) / "Documents")

    seen: list[Path] = []
    for d in docs:
        candidate = d / _DEMOS
        if candidate not in seen:
            seen.append(candidate)
    return seen


def default_replay_dir() -> Path | None:
    """The first replay folder that exists, or None."""
    return next((d for d in candidate_replay_dirs() if d.is_dir()), None)


def find_replays(directory: str | Path | None = None) -> list[Path]:
    """All .replay files in ``directory`` (or the default folder), newest first."""
    root = Path(directory) if directory is not None else default_replay_dir()
    if root is None or not root.is_dir():
        return []
    replays = [p for p in root.rglob("*.replay") if p.is_file()]
    return sorted(replays, key=lambda p: p.stat().st_mtime, reverse=True)
