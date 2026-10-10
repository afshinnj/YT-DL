"""Download history persisted as JSON.

Every finished (or failed) download is appended here so the history screen can
show it later. The list is capped so the file never grows without bound.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..utils.helpers import runtime_data_dir

DATA_DIR = runtime_data_dir("data")
HISTORY_FILE = DATA_DIR / "history.json"
MAX_ENTRIES = 250


@dataclass
class HistoryEntry:
    title: str
    url: str
    path: str
    size: int
    quality: str
    mode: str
    status: str
    timestamp: str


def load() -> list[dict]:
    """Read the history file; an empty list when it is missing or corrupt."""
    try:
        raw = HISTORY_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def add(entry: HistoryEntry) -> None:
    """Append one entry and keep the file within MAX_ENTRIES."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        entries = load()
        entries.insert(0, asdict(entry))
        entries = entries[:MAX_ENTRIES]
        HISTORY_FILE.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def clear() -> None:
    try:
        HISTORY_FILE.write_text("[]", encoding="utf-8")
    except OSError:
        pass
