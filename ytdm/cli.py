"""Command line interface for the download manager."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__, engine, ui


def _build_parser() -> argparse.ArgumentParser:
    epilog = (
        "examples:\n"
        "  python main.py https://www.youtube.com/watch?v=VIDEO_ID\n"
        "  python main.py URL -q 720                      # cap at 720p\n"
        "  python main.py URL -a --audio-format mp3       # audio only\n"
        "  python main.py URL --formats                   # list streams\n"
        "  python main.py URL -o D:\\Videos --playlist     # whole playlist\n"
    )
    p = argparse.ArgumentParser(
        prog="ytdm",
        description="A small download manager for YouTube, built on yt-dlp.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"ytdm {__version__}")
    sub = p.add_subparsers(dest="command")

    # ---- download (default) ------------------------------------------------
    dl = sub.add_parser("download", aliases=["dl"], help="download a video or playlist")
    dl.add_argument("urls", nargs="+", help="one or more URLs")
    dl.add_argument("-q", "--quality", default="best",
                    choices=engine.SUPPORTED_QUALITIES,
                    help="maximum resolution (default: best)")
    dl.add_argument("-a", "--audio", action="store_true",
                    help="extract audio only")
    dl.add_argument("--audio-format", default="mp3",
                    choices=["mp3", "m4a", "opus", "wav", "vorbis", "aac", "flac"],
                    help="audio container when --audio is used (default: mp3)")
    dl.add_argument("-o", "--output", default="downloads",
                    help="output directory (default: downloads)")
    dl.add_argument("--template", default="%(title)s.%(ext)s",
                    help="yt-dlp output filename template")
    dl.add_argument("--playlist", dest="playlist", action="store_true",
                    default=None,
                    help="download the whole playlist if the URL is one")
    dl.add_argument("--no-playlist", dest="playlist", action="store_false",
                    help="download only the single video even for playlist URLs")
    dl.add_argument("--raw-format", default=None,
                    help="pass a raw yt-dlp format selector (overrides -q)")
    dl.add_argument("--subtitles", default=None,
                    help="subtitle language codes to write, e.g. 'en,fa'")
    dl.add_argument("--embed-subs", action="store_true",
                    help="embed subtitles into the video file")
    dl.add_argument("--limit-rate", default=None,
                    help="throttle download, e.g. '2M'")
    dl.add_argument("--threads", type=int, default=4,
                    help="concurrent fragment downloads (1-16, default: 4)")
    dl.add_argument("--proxy", default=None,
                    help="proxy URL, e.g. http://127.0.0.1:10808 or socks5://...")

    # ---- info ---------------------------------------------------------------
    info = sub.add_parser("info", help="show metadata without downloading")
    info.add_argument("url")
    info.add_argument("--proxy", default=None, help="proxy URL")

    # ---- formats ------------------------------------------------------------
    fmt = sub.add_parser("formats", aliases=["F"],
                         help="list available streams for a URL")
    fmt.add_argument("url")
    fmt.add_argument("--proxy", default=None, help="proxy URL")

    # ---- tui ----------------------------------------------------------------
    sub.add_parser("tui", help="launch the interactive hacker terminal")

    # ---- update -------------------------------------------------------------
    sub.add_parser("update", help="update the bundled yt-dlp binary")
    return p


def _banner(text: str) -> None:
    ui.line()
    ui.line(ui.c(text, "bold"))
    ui.line()


def cmd_info(args: argparse.Namespace) -> int:
    opts = engine.DownloadOptions(urls=[args.url], proxy=args.proxy)
    try:
        meta = engine.probe(args.url, opts)
    except engine.DownloadError as exc:
        ui.line(ui.c(f"✗ {exc}", "red"))
        return 1

    _banner(ui.c("Video information", "cyan"))
    rows = [
        ("Title", meta.get("title")),
        ("Uploader", meta.get("uploader") or meta.get("channel")),
        ("Duration", _duration(meta.get("duration"))),
        ("Views", f"{meta.get('view_count', 0):,}" if meta.get("view_count") else None),
        ("Upload date", meta.get("upload_date")),
        ("Site", meta.get("extractor_key")),
        ("Video ID", meta.get("id")),
    ]
    for label, value in rows:
        if value:
            ui.line(f"  {ui.c(label + ':', 'yellow')} {value}")
    if meta.get("description"):
        ui.line()
        ui.line(f"  {ui.c('Description', 'yellow')}")
        desc = str(meta["description"]).splitlines()
        for line in desc[:12]:
            ui.line(f"    {line}")
        if len(desc) > 12:
            ui.line(ui.c(f"    ... ({len(desc) - 12} more lines)", "dim"))
    ui.line()
    return 0


def cmd_formats(args: argparse.Namespace) -> int:
    opts = engine.DownloadOptions(urls=[args.url], proxy=args.proxy)
    try:
        table = engine.list_formats(args.url, opts)
    except engine.DownloadError as exc:
        ui.line(ui.c(f"✗ {exc}", "red"))
        return 1
    _banner(ui.c("Available formats", "cyan"))
    for line in table.splitlines():
        ui.line(line)
    ui.line()
    ui.line(ui.c("Use --raw-format <ID> to pick a specific stream.", "dim"))
    return 0


def _duration(seconds) -> str:
    if not seconds:
        return "?"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _make_progress_printer(title: str, max_title_len: int = 48):
    """Return a callback that renders a single live progress line."""
    short = title if len(title) <= max_title_len else title[: max_title_len - 1] + "…"
    last_print = [0.0]

    def print_progress(p: engine.ProgressUpdate) -> None:
        now = time.monotonic()
        if now - last_print[0] < 0.1 and p.percent < 100.0:
            return  # throttle to ~10 fps
        last_print[0] = now
        total = ui.human_size(p.total) if p.total else ui.human_size(p.downloaded) + " (est.)"
        frag = ""
        if p.fragment_count:
            frag = f" frag {p.fragment_index}/{p.fragment_count}"
        text = (
            f"  {ui.c(short, 'bold')}  "
            f"{ui.bar(p.percent)} {p.percent:5.1f}%  "
            f"{ui.human_size(p.downloaded)} / {total}  "
            f"{ui.c(ui.human_speed(p.speed), 'magenta')}  "
            f"ETA {ui.human_eta(p.eta)}{ui.c(frag, 'dim')}"
        )
        ui.same_line(text)

    return print_progress


def cmd_download(args: argparse.Namespace) -> int:
    opts = engine.DownloadOptions(
        urls=list(args.urls),
        quality=args.quality,
        audio_only=args.audio,
        audio_format=args.audio_format,
        output_dir=Path(args.output),
        output_template=args.template,
        playlist=args.playlist,
        proxy=args.proxy,
        rate_limit=args.limit_rate,
        raw_format=args.raw_format,
        subtitles=args.subtitles,
        embed_subs=args.embed_subs,
        threads=args.threads,
    )

    problems = opts.validate()
    if problems:
        for problem in problems:
            ui.line(ui.c(f"✗ {problem}", "red"))
        return 2

    active_proxy = opts.proxy or engine.system_proxy()
    _banner(ui.c(f"ytdm {__version__} — download manager", "cyan"))
    ui.line(f"  {ui.c('Engine:', 'yellow')} {engine.version()}")
    ffmpeg = engine.find_ffmpeg()
    ui.line(f"  {ui.c('ffmpeg:', 'yellow')} "
            f"{ffmpeg or ui.c('not found — merging may be unavailable', 'red')}")
    if active_proxy:
        ui.line(f"  {ui.c('Proxy:', 'yellow')} {active_proxy}")
    ui.line(f"  {ui.c('Target:', 'yellow')} {opts.selector()}")
    ui.line(f"  {ui.c('Save to:', 'yellow')} {Path(opts.output_dir).resolve()}")
    ui.line()

    failures: list[str] = []
    for url in opts.urls:
        ui.line(f"{ui.c('▸', 'cyan')} {url}")
        try:
            meta = engine.probe(url, opts)
        except engine.DownloadError as exc:
            ui.line(ui.c(f"  ✗ {exc}", "red"))
            failures.append(url)
            continue

        title = str(meta.get("title") or url)
        ui.line(f"  {ui.c('Title:', 'yellow')} {title}")
        ui.line(f"  {ui.c('Duration:', 'yellow')} {_duration(meta.get('duration'))}")

        printer = _make_progress_printer(title)

        def log(line: str) -> None:
            ui.same_line("")     # clear the live progress line first
            ui.line(f"  {ui.c('›', 'dim')} {line}")

        try:
            saved = engine.download(opts, on_progress=printer, on_log=log)
        except engine.DownloadError as exc:
            ui.same_line("")
            ui.line(ui.c(f"  ✗ {exc}", "red"))
            failures.append(url)
            continue

        ui.same_line("")
        ui.line(f"  {ui.c('✓ saved:', 'green')} {len(saved)} file(s)")
        for path in saved:
            ui.line(f"      {Path(path).resolve()}")
        ui.line()

    if failures:
        ui.line(ui.c(f"✗ {len(failures)} URL(s) failed: {', '.join(failures)}", "red"))
        return 1
    ui.line(ui.c("✓ All downloads completed.", "green"))
    return 0


def cmd_update(_: argparse.Namespace) -> int:
    _banner(ui.c("Updating yt-dlp", "cyan"))
    ui.line(engine.update())
    ui.line(f"  {ui.c('Current version:', 'yellow')} {engine.version()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)

    # No arguments at all drops into the interactive terminal instead of the
    # usage message.
    if not raw_args:
        from .tui import menu

        return menu.run()

    # Treat a bare URL (or any unknown leading token) as an implicit "download".
    known = {"download", "dl", "info", "formats", "F", "update", "tui", "-h", "--help",
             "--version"}
    if raw_args and raw_args[0] not in known and not raw_args[0].startswith("-"):
        raw_args = ["download", *raw_args]

    args = parser.parse_args(raw_args)
    command = args.command or "download"

    if command == "tui":
        from .tui import menu

        return menu.run()
    if command in ("download", "dl"):
        if not getattr(args, "urls", None):
            parser.print_help(sys.stderr)
            return 2
        return cmd_download(args)
    if command == "info":
        return cmd_info(args)
    if command in ("formats", "F"):
        return cmd_formats(args)
    if command == "update":
        return cmd_update(args)

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
