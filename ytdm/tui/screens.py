"""Interactive hacker-terminal screens.

Each screen draws one full page of the application. Screens return a control
signal: "back" to return to the main menu, "exit" to quit, None when the flow
simply finished.

The download screens talk to the existing download engine through its public
API (`probe`, `download`, `list_formats`) and never touch yt-dlp themselves.
"""
from __future__ import annotations

import time
from pathlib import Path

from rich import box
from rich.table import Table
from rich.text import Text

from .. import engine
from ..config import AppSettings
from ..history import HistoryEntry, store as history
from ..utils import helpers, logger
from . import interface, theme
from .interface import console, ui
from .progress import DownloadDisplay, DownloadState

log = logger.get_logger()

QUALITY_LABELS: dict[str, str] = {
    "best": "BEST (auto)",
    "2160": "2160p  4K",
    "1440": "1440p  QHD",
    "1080": "1080p  FHD",
    "720": "720p   HD",
    "480": "480p",
    "360": "360p",
    "240": "240p",
    "144": "144p",
}


# ══════════════════════════════════════════════════════════════════════════
#  Shared building blocks
# ══════════════════════════════════════════════════════════════════════════

def _info_grid(rows: list[tuple[str, str]]) -> Table:
    """A two-column `LABEL : value` layout."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(style=f"bold {theme.current.accent2}", min_width=10, no_wrap=True)
    grid.add_column(style="bold #e8ffe8", no_wrap=False)
    for label, value in rows:
        grid.add_row(f"{label} :", str(value))
    return grid


def _scan_steps(url: str, meta: dict) -> None:
    """The `✓ URL VALID / ✓ VIDEO FOUND / ✓ METADATA RECEIVED` strip."""
    checks = [
        ("URL VALID", True),
        ("VIDEO FOUND", bool(meta.get("id"))),
        ("METADATA RECEIVED", bool(meta.get("title"))),
    ]
    body = Text()
    for label, ok in checks:
        body.append("\n  ")
        body.append("✓ " if ok else "✗ ", style=f"bold {theme.current.accent}")
        body.append(label, style="bold #e8ffe8" if ok else "bold #ff3b4e")
    body.append("\n")
    ui.boxed("SCANNING TARGET", body)
    console.print()


def _probe_with_scan(url: str, settings: AppSettings) -> dict | None:
    """Fetch metadata behind a spinner, then show the scan results."""
    opts = engine.DownloadOptions(urls=[url], proxy=settings.proxy_or_none())
    try:
        with console.status(
            f"[bold {theme.current.accent}]SCANNING TARGET[/] [dim]{url}[/]",
            spinner="dots", spinner_style=f"bold {theme.current.accent}",
        ):
            meta = engine.probe(url, opts)
    except engine.DownloadError as exc:
        ui.failure(str(exc), "METADATA_ERROR")
        return None
    except Exception as exc:  # noqa: BLE001 - never crash the console on a probe
        log.exception("probe failed")
        ui.failure(str(exc) or "unexpected error", "METADATA_ERROR")
        return None
    _scan_steps(url, meta)
    return meta


def _show_video_info(meta: dict) -> None:
    rows = [
        ("TITLE", helpers.truncate(str(meta.get("title") or "—"), 68)),
        ("CHANNEL", meta.get("uploader") or meta.get("channel") or "—"),
        ("DURATION", helpers.human_duration(meta.get("duration"))),
        ("VIEWS", helpers.format_count(meta.get("view_count"))),
        ("UPLOADED", helpers.to_upload_date(meta.get("upload_date"))),
        ("ID", meta.get("id") or "—"),
    ]
    ui.boxed("VIDEO INFORMATION", _info_grid(rows))


# ══════════════════════════════════════════════════════════════════════════
#  Stream selection
# ══════════════════════════════════════════════════════════════════════════

def _video_streams(meta: dict) -> list[dict]:
    """Distinct video heights reported by yt-dlp, best file size first."""
    formats = meta.get("formats") or []
    best: dict[int, dict] = {}
    for fmt in formats:
        height = fmt.get("height")
        if not height:
            continue
        vcodec = fmt.get("vcodec")
        if not vcodec or vcodec == "none":
            continue
        size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        try:
            size = float(size)
        except (TypeError, ValueError):
            size = 0.0
        current = best.get(height)
        if current is None or size > current["size"]:
            best[height] = {
                "id": fmt.get("format_id"),
                "height": int(height),
                "ext": fmt.get("ext") or "?",
                "fps": int(fmt.get("fps") or 0),
                "size": size,
                "has_audio": bool(fmt.get("acodec") and fmt.get("acodec") != "none"),
            }
    return [best[h] for h in sorted(best, reverse=True)]


def _streams_table(streams: list[dict]) -> Table:
    table = Table(
        box=box.HEAVY_HEAD,
        border_style=theme.current.border,
        header_style=f"bold {theme.current.accent}",
        expand=True,
        show_lines=False,
    )
    table.add_column("ID", justify="center", width=6)
    table.add_column("QUALITY", justify="center")
    table.add_column("FORMAT", justify="center")
    table.add_column("FPS", justify="center")
    table.add_column("SIZE", justify="right")
    for index, stream in enumerate(streams, start=1):
        size = helpers.human_size(stream["size"]) if stream["size"] else "—"
        table.add_row(
            f"{index:02d}",
            f"{stream['height']}p",
            str(stream["ext"]).upper(),
            f"{stream['fps']} FPS" if stream["fps"] else "—",
            size,
        )
    return table


def _pick_stream(meta: dict, *, audio: bool) -> tuple[str, str] | None:
    """Show the stream table and ask which one to fetch.

    Returns the raw yt-dlp selector and a human readable label.
    """
    if audio:
        return "bestaudio/best", "AUDIO (best)"
    streams = _video_streams(meta)
    if not streams:
        ui.failure("No downloadable video streams were found for this URL.", "NO_STREAMS")
        return None
    console.print()
    ui.boxed("AVAILABLE STREAMS", _streams_table(streams))
    console.print()
    choice = ui.ask(
        f"{theme.PROMPT} SELECT STREAM",
        choices=[str(i) for i in range(1, len(streams) + 1)] + ["0"],
        default="1",
    )
    if choice == "0":
        return None
    stream = streams[int(choice) - 1]
    label = f"{stream['height']}p {str(stream['ext']).upper()}"
    if stream["has_audio"]:
        selector = str(stream["id"])
    else:
        selector = f"{stream['id']}+bestaudio[ext=m4a]/bestaudio"
    return selector, label


# ══════════════════════════════════════════════════════════════════════════
#  Output directory
# ══════════════════════════════════════════════════════════════════════════

def _ask_output_dir(settings: AppSettings) -> Path | None:
    current = settings.resolved_download_dir()
    body = Text()
    body.append("Current directory:\n", style=f"bold {theme.current.accent2}")
    body.append(str(current) + "\n", style="bold #e8ffe8")
    ui.boxed("OUTPUT DIRECTORY", body)

    ui.menu(
        "SELECT OUTPUT",
        [
            ("1", "USE CURRENT DIRECTORY"),
            ("2", "ENTER CUSTOM DIRECTORY"),
            ("0", "BACK"),
        ],
    )
    choice = ui.ask(f"{theme.PROMPT}", choices=["1", "2", "0"], default="1")
    if choice == "0":
        return None
    if choice == "1":
        return current

    path_text = ui.ask("ENTER PATH (e.g. D:\\Videos)").strip().strip('"')
    if not path_text:
        return current
    path = Path(path_text)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        ui.failure(f"Could not create or access '{path}': {exc}", "BAD_OUTPUT_DIR")
        return None
    return path


# ══════════════════════════════════════════════════════════════════════════
#  Quality / mode pickers
# ══════════════════════════════════════════════════════════════════════════

def _ask_quality(settings: AppSettings) -> tuple[str, str] | None:
    keys = list(QUALITY_LABELS)
    items = [(f"{i:02d}", QUALITY_LABELS[key]) for i, key in enumerate(keys, start=1)]
    ui.menu_grid("SELECT QUALITY", items, columns=3)
    choice = ui.ask(
        f"{theme.PROMPT}", choices=[str(i) for i in range(1, len(keys) + 1)] + ["0"],
        default="1",
    )
    if choice == "0":
        return None
    key = keys[int(choice) - 1]
    return key, QUALITY_LABELS[key]


def _ask_audio_format(settings: AppSettings) -> str | None:
    from ..config import AUDIO_FORMATS

    items = [(f"{i:02d}", fmt.upper()) for i, fmt in enumerate(AUDIO_FORMATS, start=1)]
    ui.menu_grid("AUDIO FORMAT", items, columns=4)
    default_index = AUDIO_FORMATS.index(settings.audio_format) + 1 if settings.audio_format in AUDIO_FORMATS else 1
    choice = ui.ask(
        f"{theme.PROMPT}",
        choices=[str(i) for i in range(1, len(AUDIO_FORMATS) + 1)] + ["0"],
        default=str(default_index),
    )
    if choice == "0":
        return None
    return AUDIO_FORMATS[int(choice) - 1]


def _ask_mode() -> str | None:
    ui.menu(
        "DOWNLOAD MODE",
        [
            ("1", "VIDEO + AUDIO"),
            ("2", "AUDIO ONLY"),
            ("3", "BEST QUALITY"),
            ("4", "CUSTOM QUALITY"),
            ("0", "BACK"),
        ],
    )
    return ui.ask(f"{theme.PROMPT}", choices=["1", "2", "3", "4", "0"], default="1")


# ══════════════════════════════════════════════════════════════════════════
#  The download flow
# ══════════════════════════════════════════════════════════════════════════

def _ask_url() -> str | None:
    body = Text()
    body.append("paste url:\n\n", style=f"bold {theme.current.accent2}")
    body.append("> ", style=f"bold {theme.current.accent}")
    ui.boxed("ENTER TARGET URL", body)

    url = ui.ask_raw(">")
    url = helpers.normalize_url(url)
    if not url:
        ui.failure("No URL was entered.", "EMPTY_URL")
        return None
    if not helpers.looks_like_url(url):
        ui.failure(
            f"'{helpers.truncate(url, 60)}' is not a recognised YouTube link.",
            "INVALID_URL",
            hint="Expected something like https://www.youtube.com/watch?v=…",
        )
        return None
    return url


def _build_options(
    settings: AppSettings,
    url: str,
    *,
    quality: str,
    audio_only: bool,
    audio_format: str,
    output_dir: Path,
    raw_format: str | None,
    playlist: bool | None,
) -> engine.DownloadOptions:
    return engine.DownloadOptions(
        urls=[url],
        quality=quality,
        audio_only=audio_only,
        audio_format=audio_format,
        output_dir=output_dir,
        raw_format=raw_format,
        threads=settings.threads,
        rate_limit=settings.rate_limit_or_none(),
        proxy=settings.proxy_or_none(),
        playlist=playlist,
    )


def _run_download(
    opts: engine.DownloadOptions,
    meta: dict,
    quality_label: str,
    fmt_label: str,
    audio: bool,
) -> tuple[list[str], float] | None:
    """Drive the engine while the live progress panel is on screen."""
    title = str(meta.get("title") or opts.urls[0])
    state = DownloadState(title=title, quality=quality_label, fmt=fmt_label)
    display = DownloadDisplay(state)
    started = time.time()

    try:
        with display.live:
            files = engine.download(
                opts, on_progress=display.on_progress, on_log=display.on_log
            )
    except KeyboardInterrupt:
        state.status = "ABORTED"
        ui.failure("The download was interrupted by the user.", "USER_ABORT")
        log.info("download aborted by user: %s", opts.urls[0])
        _cleanup_part_files(opts.output_dir)
        return None
    except engine.DownloadError as exc:
        ui.failure(str(exc), "DOWNLOAD_ERROR")
        log.warning("download failed: %s", exc)
        _cleanup_part_files(opts.output_dir)
        return None
    except Exception as exc:  # noqa: BLE001 - keep the console alive
        log.exception("unexpected download failure")
        ui.failure(str(exc) or "unexpected error", "DOWNLOAD_ERROR")
        return None

    state.status = "COMPLETE"
    return files, time.time() - started


def _show_complete(
    files: list[str],
    elapsed: float,
    quality_label: str,
    audio: bool,
) -> None:
    body = Text()
    body.append(helpers.bar(100.0, 34), style=f"bold {theme.current.accent}")
    body.append("  100.0%\n\n", style=f"bold {theme.current.accent}")

    primary = files[0] if files else None
    if primary:
        lines = ("DOWNLOAD COMPLETE", "FILE VERIFIED", "OUTPUT SAVED")
    else:
        # yt-dlp reported success but wrote nothing new — the file already
        # existed, so the transfer was skipped rather than completed.
        lines = ("ALREADY DOWNLOADED", "NO TRANSFER NEEDED", "OUTPUT INTACT")
    for line in lines:
        body.append("✓ ", style=f"bold {theme.current.accent}")
        body.append(line + "\n", style="bold #e8ffe8")
    body.append("\n")
    if primary:
        size = _file_size(primary)
        body.append("FILE:\n", style=f"bold {theme.current.accent2}")
        body.append(f"  {primary}\n\n", style="#e8ffe8")
        body.append("SIZE: ", style=f"bold {theme.current.accent2}")
        body.append(f"{helpers.human_size(size)}   ", style="#e8ffe8")
    body.append("TIME: ", style=f"bold {theme.current.accent2}")
    body.append(helpers.human_duration(elapsed), style="#e8ffe8")
    ui.boxed("DOWNLOAD COMPLETE", body, border=theme.current.accent)
    console.print()
    if primary:
        ui.sysmsg(["TRANSFER COMPLETE", "FILE READY", "OPERATION SUCCESSFUL"])
    else:
        ui.sysmsg(["TARGET UNCHANGED", "SKIPPED", "OPERATION SUCCESSFUL"])


def _file_size(path: str) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _cleanup_part_files(output_dir) -> None:
    """Remove the `.part`/`.ytdl` fragments a cancelled download leaves behind.

    yt-dlp usually tidies these up itself; this covers the cases where it was
    killed before it got the chance.
    """
    try:
        directory = Path(output_dir)
        if not directory.is_dir():
            return
        for entry in directory.iterdir():
            if entry.suffix in {".part", ".ytdl"} or entry.name.endswith(".tmp"):
                try:
                    entry.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _record_history(
    meta: dict,
    url: str,
    files: list[str],
    quality_label: str,
    audio: bool,
    status: str,
) -> None:
    primary = files[0] if files else ""
    history.add(
        HistoryEntry(
            title=str(meta.get("title") or url),
            url=url,
            path=primary,
            size=_file_size(primary) if primary else 0,
            quality=quality_label,
            mode="AUDIO" if audio else "VIDEO",
            status=status,
            timestamp=helpers.timestamp(),
        )
    )


def screen_download(settings: AppSettings, *, audio: bool = False) -> str:
    """The full download pipeline: url → scan → mode → streams → save → run."""
    url = _ask_url()
    if not url:
        return "back"

    meta = _probe_with_scan(url, settings)
    if not meta:
        return "back"
    _show_video_info(meta)
    console.print()

    audio_only = audio
    audio_format = settings.audio_format
    quality = settings.quality
    quality_label = QUALITY_LABELS.get(quality, quality)
    raw_format: str | None = None

    if audio:
        picked = _ask_audio_format(settings)
        if not picked:
            return "back"
        audio_format = picked
        quality_label = f"AUDIO .{picked.upper()}"
    else:
        mode = _ask_mode()
        if not mode or mode == "0":
            return "back"
        if mode == "2":
            audio_only = True
            quality_label = f"AUDIO .{audio_format.upper()}"
        elif mode == "3":
            quality, quality_label = "best", QUALITY_LABELS["best"]
        elif mode == "4":
            picked = _pick_stream(meta, audio=False)
            if not picked:
                return "back"
            raw_format, quality_label = picked
        else:
            picked = _ask_quality(settings)
            if not picked:
                return "back"
            quality, quality_label = picked

    output_dir = _ask_output_dir(settings)
    if output_dir is None:
        return "back"

    playlist: bool | None = None
    if helpers.is_playlist_url(url):
        playlist = ui.confirm("This URL is a playlist. Download every video?", default=False)

    opts = _build_options(
        settings,
        url,
        quality=quality,
        audio_only=audio_only,
        audio_format=audio_format,
        output_dir=output_dir,
        raw_format=raw_format,
        playlist=playlist,
    )

    fmt_label = "AUDIO" if audio_only else "MP4"
    result = _run_download(opts, meta, quality_label, fmt_label, audio_only)

    if result is None:
        _record_history(meta, url, [], quality_label, audio_only, "FAILED")
        return "back"

    files, elapsed = result
    _show_complete(files, elapsed, quality_label, audio_only)
    _record_history(meta, url, files, quality_label, audio_only, "SUCCESS")
    return "back"


# ══════════════════════════════════════════════════════════════════════════
#  Video information
# ══════════════════════════════════════════════════════════════════════════

def screen_info(settings: AppSettings) -> str:
    url = _ask_url()
    if not url:
        return "back"
    console.print()
    meta = _probe_with_scan(url, settings)
    if not meta:
        return "back"
    console.print()
    _show_video_info(meta)
    if meta.get("description"):
        console.print()
        desc = str(meta["description"]).splitlines()
        body = Text()
        for line in desc[:10]:
            body.append(line + "\n", style="#cfe8cf")
        if len(desc) > 10:
            body.append(f"… {len(desc) - 10} more lines", style="dim")
        ui.boxed("DESCRIPTION", body)
    return "back"


# ══════════════════════════════════════════════════════════════════════════
#  History
# ══════════════════════════════════════════════════════════════════════════

def screen_history(settings: AppSettings) -> str:
    entries = history.load()
    if not entries:
        body = Text()
        body.append("● ", style=f"bold {theme.current.accent2}")
        body.append("No downloads recorded yet.", style="bold #e8ffe8")
        body.append("\nCompleted downloads will be logged here.\n", style="dim")
        ui.boxed("DOWNLOAD HISTORY", body)
        return "back"

    table = Table(
        box=box.HEAVY_HEAD,
        border_style=theme.current.border,
        header_style=f"bold {theme.current.accent}",
        expand=True,
        show_lines=False,
    )
    table.add_column("#", justify="right", width=4, no_wrap=True)
    table.add_column("TIME", min_width=16, no_wrap=True)
    table.add_column("TITLE", no_wrap=True, ratio=1, overflow="ellipsis")
    table.add_column("QUALITY", justify="center", min_width=10, no_wrap=True)
    table.add_column("SIZE", justify="right", min_width=10, no_wrap=True)
    table.add_column("MODE", justify="center", min_width=6, no_wrap=True)
    table.add_column("STATUS", justify="center", min_width=8, no_wrap=True)

    for index, entry in enumerate(entries, start=1):
        status = str(entry.get("status") or "—")
        status_style = (
            f"bold {theme.current.accent}" if status == "SUCCESS" else "bold #ff3b4e"
        )
        table.add_row(
            str(index),
            str(entry.get("timestamp") or "—"),
            helpers.truncate(str(entry.get("title") or "—"), 44),
            str(entry.get("quality") or "—"),
            helpers.human_size(entry.get("size") or 0),
            str(entry.get("mode") or "—"),
            Text(status, style=status_style),
        )
    ui.boxed(f"DOWNLOAD HISTORY  ({len(entries)})", table)

    ui.menu(
        "ACTIONS",
        [
            ("1", "OPEN DOWNLOAD FOLDER"),
            ("C", "CLEAR HISTORY"),
            ("0", "BACK"),
        ],
        marker="·",
    )
    choice = ui.ask(f"{theme.PROMPT}", choices=["1", "c", "C", "0"], default="0")
    if choice.lower() == "c":
        if ui.confirm("Erase every recorded download?", default=False):
            history.clear()
            ui.success("HISTORY", ["Download history cleared."])
    elif choice == "1":
        _open_folder(settings.resolved_download_dir())
    return "back"


def _open_folder(path: Path) -> None:
    import os

    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        ui.failure(f"Could not open '{path}' in Explorer.", "OPEN_FAILED")


# ══════════════════════════════════════════════════════════════════════════
#  Settings
# ══════════════════════════════════════════════════════════════════════════

def _settings_table(settings: AppSettings) -> Table:
    def yes(value: str) -> str:
        return value if value else "—"

    ffmpeg = yes(settings.ffmpeg_path) or "BUNDLED (auto)"
    proxy = yes(settings.proxy) or "AUTO (system)"
    rows = [
        ("[1]  DEFAULT DIRECTORY", str(settings.resolved_download_dir())),
        ("[2]  DEFAULT QUALITY", QUALITY_LABELS.get(settings.quality, settings.quality)),
        ("[3]  DEFAULT AUDIO FORMAT", settings.audio_format.upper()),
        ("[4]  THEME", f"{settings.theme.upper()} ({theme.current.title})"),
        ("[5]  FFMPEG PATH", ffmpeg),
        ("[6]  PROXY", proxy),
        ("[7]  THREADS", str(settings.threads)),
        ("[8]  RATE LIMIT", yes(settings.rate_limit)),
    ]
    table = Table.grid(padding=(0, 2))
    table.add_column(style=f"bold {theme.current.accent2}", min_width=26, no_wrap=True)
    table.add_column(style="bold #e8ffe8")
    for label, value in rows:
        table.add_row(label, value)
    return table


def screen_settings(settings: AppSettings) -> str:
    ui.boxed("SETTINGS", _settings_table(settings))
    ui.menu(
        "CONFIGURE",
        [
            ("1", "DEFAULT DOWNLOAD DIRECTORY"),
            ("2", "DEFAULT QUALITY"),
            ("3", "DEFAULT AUDIO FORMAT"),
            ("4", "THEME"),
            ("5", "FFMPEG PATH"),
            ("6", "PROXY"),
            ("7", "THREADS"),
            ("8", "RATE LIMIT"),
            ("9", "CLEAR DOWNLOAD HISTORY"),
            ("0", "BACK"),
        ],
    )

    choice = ui.ask(
        f"{theme.PROMPT}",
        choices=[str(i) for i in range(10)] + ["c", "C"],
        default="0",
    )
    if choice == "0":
        return "back"

    if choice == "1":
        value = ui.ask("ENTER DIRECTORY", default=settings.download_dir).strip().strip('"')
        if value:
            settings.download_dir = value
            _ensure_dir(settings.resolved_download_dir())
    elif choice == "2":
        picked = _ask_quality(settings)
        if picked:
            settings.quality = picked[0]
    elif choice == "3":
        picked = _ask_audio_format(settings)
        if picked:
            settings.audio_format = picked
    elif choice == "4":
        _pick_theme(settings)
    elif choice == "5":
        value = ui.ask("ENTER FFMPEG PATH (empty = auto)", default=settings.ffmpeg_path).strip().strip('"')
        settings.ffmpeg_path = value
        interface.apply_ffmpeg_path(settings)
    elif choice == "6":
        value = ui.ask("ENTER PROXY (e.g. http://127.0.0.1:10808)", default=settings.proxy).strip()
        settings.proxy = value
    elif choice == "7":
        threads = ui.ask("ENTER THREADS (1-16)", default=str(settings.threads))
        try:
            settings.threads = max(1, min(16, int(threads)))
        except ValueError:
            ui.failure("Threads must be a number between 1 and 16.", "BAD_VALUE")
            return "back"
    elif choice == "8":
        settings.rate_limit = ui.ask("ENTER RATE LIMIT (e.g. 2M, empty = unlimited)", default=settings.rate_limit).strip()
    elif choice in ("9",):
        if ui.confirm("Erase every recorded download?", default=False):
            history.clear()
            ui.success("HISTORY", ["Download history cleared."])
    elif choice.lower() == "c":
        if ui.confirm("Reset all settings to defaults?", default=False):
            defaults = AppSettings()
            for field in settings.__dataclass_fields__:
                setattr(settings, field, getattr(defaults, field))
            theme.apply(settings.theme)
            interface.apply_ffmpeg_path(settings)

    settings.save()
    theme.apply(settings.theme)
    ui.success("SETTINGS", ["Configuration saved."])
    return "back"


def _pick_theme(settings: AppSettings) -> None:
    from ..config import THEMES

    items = [(f"{i:02d}", name.upper()) for i, name in enumerate(THEMES, start=1)]
    ui.menu_grid("SELECT THEME", items, columns=3)
    default_index = THEMES.index(settings.theme) + 1 if settings.theme in THEMES else 1
    choice = ui.ask(
        f"{theme.PROMPT}",
        choices=[str(i) for i in range(1, len(THEMES) + 1)] + ["0"],
        default=str(default_index),
    )
    if choice != "0":
        settings.theme = THEMES[int(choice) - 1]
        theme.apply(settings.theme)


def _ensure_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════
#  Update the bundled engine
# ══════════════════════════════════════════════════════════════════════════

def screen_update(settings: AppSettings) -> str:
    ui.line()
    with console.status(
        f"[bold {theme.current.accent}]UPDATING YT-DLP CORE[/]",
        spinner="dots", spinner_style=f"bold {theme.current.accent}",
    ):
        try:
            message = engine.update()
            version = engine.version()
        except Exception as exc:  # noqa: BLE001 - updater failures must not crash
            ui.failure(str(exc) or "update failed", "UPDATE_ERROR")
            return "back"
    ui.success(
        "YT-DLP CORE",
        [str(message).strip() or "Update finished.", f"Current version: {version}"],
    )
    return "back"
