"""Terminal output helpers: colours, size formatting and progress bars."""
from __future__ import annotations

import shutil
import sys

# ANSI colours; disabled automatically when the terminal cannot render them.
_USE_COLOR = sys.stdout.isatty() and shutil.get_terminal_size((80, 20)).columns > 0


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def c(text: object, color: str = "") -> str:
    codes = {
        "red": "31", "green": "32", "yellow": "33",
        "blue": "34", "magenta": "35", "cyan": "36",
        "gray": "90", "bold": "1", "dim": "2",
    }
    text = str(text)
    if not color or color not in codes:
        return text
    return _paint(codes[color], text)


def hr(char: str = "─", color: str = "dim") -> str:
    width = max(20, shutil.get_terminal_size((80, 20)).columns)
    return c(char * width, color)


def human_size(num: float) -> str:
    """Render a byte count in a compact human-readable form."""
    if num is None:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num) < 1024.0:
            if unit == "B":
                return f"{num:.0f} {unit}"
            return f"{num:6.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} PiB"


def human_speed(bytes_per_sec: float) -> str:
    if not bytes_per_sec:
        return "?/s"
    return f"{human_size(bytes_per_sec).strip()}/s"


def human_eta(seconds: float) -> str:
    if seconds is None or seconds <= 0 or seconds == float("inf"):
        return "?"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def bar(percent: float, width: int = 30) -> str:
    """Render a [####-----] style progress bar."""
    percent = max(0.0, min(100.0, percent))
    filled = int(round(width * percent / 100.0))
    inner = "#" * filled + "-" * (width - filled)
    return f"[{c(inner, 'green' if percent >= 100 else 'cyan')}]"


def line(text: str = "") -> None:
    """Print, tolerating unicode on a narrow Windows console."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def same_line(text: str) -> None:
    """Overwrite the current terminal line (used for live progress)."""
    try:
        sys.stdout.write("\r" + text)
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.write("\r" + text.encode("ascii", "replace").decode("ascii"))
        sys.stdout.flush()
