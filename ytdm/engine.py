"""Download engine: locates the bundled yt-dlp/ffmpeg binaries and drives them."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from . import ui

# Package layout:  <root>/ytdm/engine.py  ->  <root>/bin/...
_BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
_FFmpeg_SUBDIR = Path("ffmpeg-master-latest-win64-gpl") / "bin"

# Format selectors keyed by the maximum vertical resolution we want.
# mp4/m4a sources are preferred so the result plays everywhere; when YouTube
# only offers webm for a resolution we fall back to it and remux to mp4.
_QUALITY_BASE = (
    "bestvideo[ext=mp4][height<={h}]+bestaudio[ext=m4a]"
    "/bestvideo[height<={h}]+bestaudio"
    "/best[height<={h}]"
)
QUALITY_SELECTORS: dict[str, str] = {
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
    **{h: _QUALITY_BASE.format(h=h) for h in ("2160", "1440", "1080", "720", "480", "360", "240", "144")},
}
SUPPORTED_QUALITIES = list(QUALITY_SELECTORS)

# yt-dlp emits one of these lines for every progress tick; we parse them.
_PROGRESS_TEMPLATE = (
    "YTDL|%(progress.downloaded_bytes)s|%(progress.total_bytes)s"
    "|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s"
    "|%(progress.fragment_index)s|%(progress.fragment_count)s"
)
_PROGRESS_PREFIX = "YTDL|"


class BinaryError(RuntimeError):
    """Raised when the yt-dlp executable cannot be located."""


class DownloadError(RuntimeError):
    """Raised when yt-dlp reports a failure for a URL."""


@dataclass
class DownloadOptions:
    """User-facing settings for a single download request."""
    urls: list[str] = field(default_factory=list)
    quality: str = "best"
    audio_only: bool = False
    audio_format: str = "mp3"
    output_dir: Path = Path("downloads")
    output_template: str = "%(title)s.%(ext)s"
    playlist: bool | None = None
    proxy: str | None = None
    rate_limit: str | None = None
    raw_format: str | None = None
    subtitles: str | None = None
    embed_subs: bool = False
    threads: int = 4
    quiet: bool = False

    def selector(self) -> str:
        if self.raw_format:
            return self.raw_format
        if self.audio_only:
            return "bestaudio/best"
        return QUALITY_SELECTORS.get(self.quality, QUALITY_SELECTORS["best"])

    def validate(self) -> list[str]:
        """Return a list of human-readable problems, empty when everything is fine."""
        problems: list[str] = []
        if not self.urls:
            problems.append("no URL given")
        if self.quality not in QUALITY_SELECTORS and not self.raw_format:
            problems.append(
                f"unknown quality '{self.quality}'; choose from "
                f"{', '.join(SUPPORTED_QUALITIES)}"
            )
        if self.threads < 1 or self.threads > 16:
            problems.append("threads must be between 1 and 16")
        if self.audio_only and self.audio_format.lower() not in {"mp3", "m4a", "opus", "wav", "vorbis", "aac", "flac"}:
            problems.append(f"unsupported audio format '{self.audio_format}'")
        return problems


@dataclass
class ProgressUpdate:
    downloaded: float = 0.0
    total: float = 0.0
    speed: float = 0.0
    eta: float = 0.0
    fragment_index: int = 0
    fragment_count: int = 0

    @property
    def percent(self) -> float:
        if self.total and self.total > 0:
            return self.downloaded / self.total * 100.0
        return 0.0


def find_ytdlp() -> Path:
    """Locate yt-dlp: bundled binary first, then PATH."""
    candidates = [
        _BIN_DIR / "yt-dlp.exe",
        _BIN_DIR / "yt-dlp",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    on_path = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if on_path:
        return Path(on_path)
    raise BinaryError(
        "yt-dlp not found. Expected it next to the app at "
        f"'{_BIN_DIR / 'yt-dlp.exe'}', or on PATH."
    )


def find_ffmpeg() -> Path | None:
    """Locate the ffmpeg directory if the bundled build is present."""
    local = _BIN_DIR / _FFmpeg_SUBDIR
    if (local / "ffmpeg.exe").is_file():
        return local
    on_path = shutil.which("ffmpeg")
    return Path(on_path).parent if on_path else None


def version() -> str:
    out = _run([str(find_ytdlp()), "--version"])
    return (out or "").strip().splitlines()[0] if out else "unknown"


def _run(cmd: Sequence[str], timeout: int = 120) -> str:
    """Run a yt-dlp command and return its combined output as text."""
    proc = subprocess.run(
        list(cmd),
        capture_output=True,
        encoding="utf-8", errors="replace",
        timeout=timeout,
        env=_bin_env(),
    )
    return (proc.stdout or "") + (proc.stderr or "")


def system_proxy() -> str | None:
    """Read the proxy configured in Windows Internet Settings, if enabled."""
    if os.environ.get("HTTPS_PROXY"):
        return os.environ["HTTPS_PROXY"]
    if os.environ.get("HTTP_PROXY"):
        return os.environ["HTTP_PROXY"]
    try:
        import winreg  # type: ignore[import-not-found]
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if server and not server.startswith(("http://", "https://", "socks")):
            server = f"http://{server}"
        return server or None
    except (OSError, ImportError, ValueError):
        return None


def _bin_env() -> dict[str, str]:
    """Build a subprocess environment whose PATH includes our bundled tools.

    yt-dlp searches PATH for a JavaScript runtime (deno) and for ffmpeg, so
    making the bundled binaries discoverable avoids extra configuration.
    """
    env = dict(os.environ)
    extra = str(_BIN_DIR)
    path = env.get("PATH", "")
    if extra not in path.split(os.pathsep):
        env["PATH"] = extra + os.pathsep + path
    return env


def _base_args(opts: DownloadOptions) -> list[str]:
    """Arguments shared by every yt-dlp invocation we launch."""
    args = [
        str(find_ytdlp()),
        "--no-mtime",                       # keep local file timestamps sane
        "--console-title",                   # show progress in the window title too
        "--newline",                         # one progress update per line
        "--no-color",
    ]
    ffmpeg_dir = find_ffmpeg()
    if ffmpeg_dir:
        args += ["--ffmpeg-location", str(ffmpeg_dir)]
    proxy = opts.proxy or system_proxy()
    if proxy:
        args += ["--proxy", proxy]
    if opts.rate_limit:
        args += ["--limit-rate", opts.rate_limit]
    return args


def probe(url: str, opts: DownloadOptions | None = None) -> dict:
    """Fetch metadata for a URL as a dict (yt-dlp --dump-single-json)."""
    opts = opts or DownloadOptions()
    cmd = _base_args(opts) + ["--dump-single-json", "--no-playlist", url]
    proc = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", errors="replace",
        env=_bin_env(),
    )
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        detail = (proc.stderr or "").strip().splitlines()
        tail = " | ".join(detail[-3:]) if detail else "unknown error"
        raise DownloadError(f"could not read info for {url}: {tail}")
    return json.loads(proc.stdout)


def list_formats(url: str, opts: DownloadOptions) -> str:
    """Return yt-dlp's raw format table for inspection."""
    cmd = _base_args(opts) + ["--list-formats", url]
    proc = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", errors="replace",
        env=_bin_env(),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise DownloadError(f"could not list formats for {url}: {out.strip()}")
    return out


def _parse_progress_line(line: str) -> ProgressUpdate | None:
    if not line.startswith(_PROGRESS_PREFIX):
        return None
    parts = line[len(_PROGRESS_PREFIX):].split("|")
    if len(parts) < 7:
        return None

    def to_float(value: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    return ProgressUpdate(
        downloaded=to_float(parts[0]),
        total=to_float(parts[1]) or to_float(parts[2]),
        speed=to_float(parts[3]),
        eta=to_float(parts[4]),
        fragment_index=int(to_float(parts[5])),
        fragment_count=int(to_float(parts[6])),
    )


def _iter_lines(proc: subprocess.Popen) -> Iterator[tuple[str, str]]:
    """Yield (stream, line) tuples as they arrive from the child process."""
    assert proc.stdout is not None and proc.stderr is not None
    for raw in proc.stdout:                      # type: ignore[attr-defined]
        yield "out", raw.rstrip("\n")
    for raw in proc.stderr:                      # type: ignore[attr-defined]
        yield "err", raw.rstrip("\n")


def download(
    opts: DownloadOptions,
    on_progress=None,
    on_log=None,
) -> list[str]:
    """Run a download request. Returns the list of files yt-dlp produced."""
    problems = opts.validate()
    if problems:
        raise DownloadError("; ".join(problems))

    args = _base_args(opts) + [
        "--format", opts.selector(),
        "--merge-output-format", "mp4",
        "--progress", "--progress-template", _PROGRESS_TEMPLATE,
        "--output", str(Path(opts.output_dir) / opts.output_template),
        "--print", "after_move:filepath",
    ]
    if opts.playlist is True:
        args.append("--yes-playlist")
    elif opts.playlist is False:
        args.append("--no-playlist")
    if opts.audio_only:
        args += [
            "--extract-audio",
            "--audio-format", opts.audio_format,
            "--audio-quality", "0",
        ]
    if opts.subtitles:
        args += ["--write-subs", "--sub-langs", opts.subtitles]
    if opts.embed_subs:
        args.append("--embed-subs")
    if opts.threads > 1:
        args += ["--concurrent-fragments", str(opts.threads)]
    args.extend(opts.urls)

    os.makedirs(opts.output_dir, exist_ok=True)

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",
        bufsize=1,
        env=_bin_env(),
    )

    saved_files: list[str] = []
    error_lines: list[str] = []

    for stream, line in _iter_lines(proc):
        progress = _parse_progress_line(line) if stream == "out" else None
        if progress is not None:
            if on_progress:
                on_progress(progress)
            continue
        if stream == "out":
            # Lines outside the progress template: the final filepath and any
            # stray status text.
            if Path(line.strip()).exists():
                saved_files.append(line.strip())
            elif on_log:
                on_log(line)
        else:
            stripped = line.strip()
            if stripped.startswith("ERROR") or "Giving up" in stripped:
                error_lines.append(stripped)
            if on_log and stripped and not _is_noise(stripped):
                on_log(stripped)

    return_code = proc.wait()
    if return_code != 0:
        tail = " | ".join(error_lines[-3:]) if error_lines else f"exit code {return_code}"
        raise DownloadError(f"download failed: {tail}")

    return saved_files


def _is_noise(line: str) -> bool:
    return bool(re.match(r"^\[(?:youtube|generic|download|ffmpeg)\]", line))


def update() -> str:
    """Update the bundled yt-dlp binary in place and report the new version."""
    result = _run([str(find_ytdlp()), "-U"], timeout=300)
    last = [ln for ln in result.strip().splitlines() if ln.strip()]
    return last[-1] if last else "update finished"
