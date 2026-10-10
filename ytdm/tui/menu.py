"""The application loop: boot sequence, home screen and menu dispatch."""
from __future__ import annotations

import time
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import __version__, engine
from ..config import AppSettings
from ..utils import logger
from . import interface, screens, theme
from .interface import console, ui

log = logger.get_logger()

MAIN_MENU = [
    ("1", "DOWNLOAD VIDEO"),
    ("2", "DOWNLOAD AUDIO"),
    ("3", "VIDEO INFORMATION"),
    ("4", "DOWNLOAD HISTORY"),
    ("5", "SETTINGS"),
    ("6", "UPDATE YT-DLP"),
    ("0", "EXIT"),
]

MENU_CHOICES = [key for key, _ in MAIN_MENU] + ["q", "Q", "exit", "quit"]

BOOT_STEPS = (
    ("INITIALIZING SYSTEM", 0.18),
    ("LOADING DOWNLOAD ENGINE", 0.18),
    ("CHECKING NETWORK", 0.18),
    ("CONNECTING TO YOUTUBE", 0.18),
    ("READY", 0.10),
)


# ══════════════════════════════════════════════════════════════════════════
#  Boot
# ══════════════════════════════════════════════════════════════════════════

def collect_status(settings: AppSettings) -> dict:
    """Probe the environment once; the home screen reuses the result."""
    status: dict = {"engine_version": "unknown", "ffmpeg": None, "proxy": "DIRECT"}
    try:
        status["engine_version"] = engine.version()
    except Exception as exc:  # noqa: BLE001 - the console must still boot
        status["engine_version"] = f"error: {exc}"
    try:
        status["ffmpeg"] = engine.find_ffmpeg()
    except Exception:  # noqa: BLE001
        status["ffmpeg"] = None
    try:
        detected = engine.system_proxy()
    except Exception:  # noqa: BLE001
        detected = None
    if settings.proxy_or_none():
        status["proxy"] = settings.proxy.strip()
    elif detected:
        status["proxy"] = f"SYSTEM → {detected}"
    status["download_dir"] = settings.resolved_download_dir()
    return status


def _boot_sequence(status: dict) -> None:
    """Short typed log lines; fast enough not to get in the way."""
    for label, delay in BOOT_STEPS:
        console.print(
            f"[dim]>[/] [bold {theme.current.accent}]{label}...[/]",
            end="",
        )
        time.sleep(delay)
        console.print(f" [{'bold ' + theme.current.accent}]OK[/]")
    console.print()


# ══════════════════════════════════════════════════════════════════════════
#  Home screen
# ══════════════════════════════════════════════════════════════════════════

def _storage_ok(directory) -> bool:
    """True when the download directory exists and is writable."""
    if not directory:
        return False
    try:
        path = Path(str(directory))
        path.mkdir(parents=True, exist_ok=True)
        return path.is_dir()
    except OSError:
        return False


def _status_grid(status: dict) -> Table:
    def state(ok: bool, up: str, down: str) -> Text:
        text = Text()
        text.append("● ", style=f"bold {theme.current.accent}" if ok else "bold #ff3b4e")
        text.append(up if ok else down, style=f"bold {theme.current.accent}" if ok else "bold #ff3b4e")
        return text

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=f"bold {theme.current.accent}", no_wrap=True)
    grid.add_column(style="dim", min_width=12)
    grid.add_column(no_wrap=True)
    grid.add_column(style="dim", overflow="fold")

    ffmpeg_ok = status.get("ffmpeg") is not None
    storage_ok = _storage_ok(status.get("download_dir"))
    rows = [
        (
            "● DOWNLOADER",
            "·" * 12,
            state(True, "ONLINE", "OFFLINE"),
            f"yt-dlp {status.get('engine_version', 'unknown')}",
        ),
        (
            "● NETWORK",
            "·" * 12,
            state(True, "READY", "DOWN"),
            str(status.get("proxy", "DIRECT")),
        ),
        (
            "● FFMPEG",
            "·" * 12,
            state(ffmpeg_ok, "READY", "NOT FOUND"),
            "bundled" if ffmpeg_ok else "merge unavailable",
        ),
        (
            "● STORAGE",
            "·" * 12,
            state(storage_ok, "READY", "DENIED"),
            str(status.get("download_dir", "")),
        ),
    ]
    for name, dots, state_text, detail in rows:
        name_text = Text(name)
        name_text.style = f"bold {theme.current.accent}"
        grid.add_row(name_text, Text(dots), state_text, Text(str(detail), style="dim"))
    return grid


def draw_home(status: dict) -> None:
    console.print(
        Panel(
            theme.current.logo_text(),
            border_style=theme.current.accent,
            box=box.DOUBLE,
            padding=(1, 4),
        )
    )
    console.print()
    console.print(
        Panel(
            theme.current.badge_bar("SYSTEM ONLINE", "YT-DL CORE", f"v{__version__}"),
            border_style=theme.current.border,
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )
    console.print()
    console.print(
        Panel(
            _status_grid(status),
            title=f"[bold {theme.current.accent}]┤ SYSTEM STATUS ├[/]",
            title_align="left",
            border_style=theme.current.border,
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()
    ui.menu("MAIN MENU", MAIN_MENU)


# ══════════════════════════════════════════════════════════════════════════
#  Loop
# ══════════════════════════════════════════════════════════════════════════

def _shutdown() -> None:
    console.print()
    ui.sysmsg(["CONNECTION CLOSED", "SYSTEM SHUTDOWN", "GOODBYE, OPERATOR"])
    console.print()


def run() -> int:
    settings = AppSettings.load()
    theme.apply(settings.theme)
    interface.apply_ffmpeg_path(settings)

    ui.clear()
    try:
        status = collect_status(settings)
    except engine.BinaryError as exc:
        ui.failure(str(exc), "ENGINE_MISSING")
        return 1
    _boot_sequence(status)

    while True:
        try:
            ui.clear()
            draw_home(status)
            choice = ui.ask(f"{theme.PROMPT}", choices=MENU_CHOICES, default="0")
            choice = choice.strip().lower()

            if choice in ("0", "q", "exit", "quit"):
                _shutdown()
                return 0
            if choice == "1":
                screens.screen_download(settings, audio=False)
            elif choice == "2":
                screens.screen_download(settings, audio=True)
            elif choice == "3":
                screens.screen_info(settings)
            elif choice == "4":
                screens.screen_history(settings)
            elif choice == "5":
                screens.screen_settings(settings)
            elif choice == "6":
                screens.screen_update(settings)

            ui.pause()
        except KeyboardInterrupt:
            console.print()
            ui.sysmsg(["INTERRUPT RECEIVED", "RETURNING TO MAIN MENU"])
            time.sleep(0.6)
        except EOFError:
            # stdin closed (closed terminal, redirected input that ran out).
            # Treat it as a clean shutdown instead of looping on the error.
            console.print()
            _shutdown()
            return 0
        except Exception as exc:  # noqa: BLE001 - a broken screen must not kill the app
            log.exception("unhandled error in the terminal loop")
            console.print()
            ui.failure(str(exc) or "unexpected error", "INTERNAL_ERROR")
            ui.pause()


if __name__ == "__main__":
    raise SystemExit(run())
