"""Small pure-python helpers.

Kept free of third-party imports so they can be used from anywhere in the
package, including the classic (non-rich) command line.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path


def runtime_data_dir(name: str = "data") -> Path:
    """Where user-writable app data lives.

    In a PyInstaller build the program folder may be read-only, so we sit the
    data directory next to the executable (portable-app convention). In a
    source checkout it stays at the project root.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent.parent
    return base / name

# Youtube host shapes we accept when the user pastes a link.
_URL_RE = re.compile(
    r"""^https?://
        (?:www\.)?
        (?:
            youtu\.be/
            | youtube\.com/(?:watch|embed|v|shorts|live|playlist)(?:[/?#]|$)
        )""",
    re.IGNORECASE | re.VERBOSE,
)

_PLAYLIST_RE = re.compile(r"[?&]list=", re.IGNORECASE)


def normalize_url(text: str) -> str:
    """Trim whitespace and stray quotes from a pasted URL."""
    return (text or "").strip().strip("\"'").strip()


def looks_like_url(text: str) -> bool:
    return bool(_URL_RE.match(normalize_url(text)))


def is_playlist_url(text: str) -> bool:
    return bool(_PLAYLIST_RE.search(normalize_url(text)))


def safe_output_dir(text: str) -> Path | None:
    """Normalise a caller supplied output directory.

    Returns an absolute path, or ``None`` when the input is not acceptable:
    it must be absolute (no implicit dependency on the current working
    directory) and must not climb out of itself with ``..``. The directory
    does not have to exist yet — the caller is expected to create it.
    """
    raw = (text or "").strip().strip("\"'").strip()
    if not raw:
        return None
    try:
        path = Path(raw)
    except ValueError:
        return None
    if not path.is_absolute():
        return None
    if path.parts and ".." in path.parts:
        return None
    # Resolve separators without touching the filesystem, so a path on a
    # drive that does not exist yet still validates.
    return Path(str(path))


def _scale(value: float, base: float) -> tuple[float, str]:
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    size = float(value)
    for unit in units:
        if abs(size) < base:
            if unit == "B":
                return size, unit
            return size, unit
        size /= base
    return size, units[-1]


def human_size(num: float | None, base: float = 1000.0) -> str:
    """Render a byte count as a short human readable string."""
    if num is None:
        return "—"
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "—"
    size, unit = _scale(num, base)
    if unit == "B":
        return f"{size:,.0f} {unit}"
    return f"{size:,.2f} {unit}"


def human_speed(bytes_per_sec: float | None) -> str:
    if not bytes_per_sec:
        return "—"
    return f"{human_size(bytes_per_sec)}/s"


def human_eta(seconds: float | None) -> str:
    if seconds is None or seconds != seconds or seconds <= 0 or seconds == float("inf"):
        return "—"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def human_duration(seconds: float | None) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_count(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def to_upload_date(raw: str | None) -> str:
    """Turn yt-dlp's YYYYMMDD into YYYY-MM-DD."""
    if not raw or not isinstance(raw, str):
        return "—"
    try:
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    except Exception:
        return raw


def timestamp(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or datetime.now().timestamp()).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def bar(percent: float, width: int = 30) -> str:
    """A `████░░░` style bar clamped to 0..100."""
    try:
        percent = float(percent)
    except (TypeError, ValueError):
        percent = 0.0
    percent = max(0.0, min(100.0, percent))
    filled = int(round(width * percent / 100.0))
    return "█" * filled + "░" * (width - filled)


def truncate(text: str, limit: int, tail: str = "…") -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    if limit <= len(tail):
        return tail[:limit]
    return text[: limit - len(tail)] + tail
