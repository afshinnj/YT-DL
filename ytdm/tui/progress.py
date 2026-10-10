"""Live download progress rendered with rich.

The download engine reports progress through a callback; this module turns
those callbacks into the animated `DOWNLOAD ENGINE` panel.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from rich import box
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from ..utils import helpers
from . import theme
from .interface import console

_REFRESH_RATE = 12  # frames per second; the engine throttles its own updates


@dataclass
class DownloadState:
    """Mutable view of one in-flight download."""

    title: str
    quality: str
    fmt: str
    started_at: float = field(default_factory=time.time)

    downloaded: float = 0.0
    total: float = 0.0
    speed: float = 0.0
    eta: float = 0.0
    status: str = "CONNECTING"
    fragments: tuple[int, int] = (0, 0)


class DownloadDisplay:
    """Renders itself as a live panel while the engine runs."""

    def __init__(self, state: DownloadState) -> None:
        self.state = state
        self.live = Live(
            self,
            console=console,
            refresh_per_second=_REFRESH_RATE,
            transient=False,
        )

    # -- engine callbacks -------------------------------------------------
    def on_progress(self, update) -> None:
        state = self.state
        state.downloaded = update.downloaded
        state.total = update.total
        state.speed = update.speed
        state.eta = update.eta
        if update.fragment_count:
            state.fragments = (update.fragment_index, update.fragment_count)
        if state.status == "CONNECTING":
            state.status = "DOWNLOADING"

    def on_log(self, line: str) -> None:
        lowered = line.lower()
        if "merging" in lowered or "extracting audio" in lowered or "converting" in lowered:
            self.state.status = "PROCESSING"
        elif "downloading" in lowered and self.state.status == "CONNECTING":
            self.state.status = "DOWNLOADING"

    # -- rendering --------------------------------------------------------
    @property
    def percent(self) -> float:
        state = self.state
        if state.total and state.total > 0:
            return max(0.0, min(100.0, state.downloaded / state.total * 100.0))
        return 0.0

    def __rich__(self) -> Text:
        state = self.state
        accent = theme.current.accent
        accent2 = theme.current.accent2
        label = theme.current.label
        percent = self.percent

        if state.status == "DOWNLOADING" or state.status == "PROCESSING":
            status_style = f"bold {accent}"
        elif state.status == "COMPLETE":
            status_style = "bold #00ff66"
        else:
            status_style = "bold #ffd000"

        body = Text(no_wrap=True)
        body.append(f"TARGET  : ", style=f"bold {label}")
        body.append(f"{helpers.truncate(state.title, 52)}\n", style="bold #e8ffe8")
        body.append(f"QUALITY : ", style=f"bold {label}")
        body.append(f"{state.quality}\n", style=f"bold {accent2}")
        body.append(f"FORMAT  : ", style=f"bold {label}")
        body.append(f"{state.fmt}\n\n", style=f"bold {accent2}")
        body.append(f"STATUS  : ", style=f"bold {label}")
        body.append(f"● {state.status}\n\n", style=status_style)

        body.append(helpers.bar(percent, 34), style=f"bold {accent}")
        body.append(f"  {percent:5.1f}%\n", style=f"bold {accent}")
        body.append(f"{percent:5.1f}% | ", style=f"bold {accent2}")
        body.append(f"{helpers.human_size(state.downloaded)} / ", style="#e8ffe8")
        total = helpers.human_size(state.total) if state.total else "—"
        body.append(f"{total}\n", style="#e8ffe8")
        body.append(f"SPEED   : ", style=f"bold {label}")
        body.append(f"{helpers.human_speed(state.speed)}\n", style=f"bold {accent2}")
        body.append(f"ETA     : ", style=f"bold {label}")
        body.append(f"{helpers.human_eta(state.eta)}\n", style=f"bold {accent2}")

        if state.fragments[1]:
            body.append(
                f"FRAGMENT: {state.fragments[0]} / {state.fragments[1]}\n",
                style=label,
            )
        return Panel(
            body,
            title=f"[bold {accent}]┤ DOWNLOAD ENGINE ├[/]",
            title_align="left",
            border_style=theme.current.border,
            box=box.ROUNDED,
            padding=(1, 2),
        )


def run_live(display: DownloadDisplay):
    """Context manager wrapper so the live region always stops cleanly."""
    return display.live
