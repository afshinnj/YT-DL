"""The service layer: protocol requests translated into engine calls.

This is the only place that touches ``ytdm.engine``, ``ytdm.config`` and
``ytdm.history``. Both the native host and the queue supervisor reach the
project's existing machinery through here, so the download engine itself stays
unmodified and there is exactly one code path deciding how a URL is
downloaded.

Long-lived work is deliberately not done here: this layer *records* a download
request and lets the detached supervisor process execute it (see
:mod:`supervisor`). That separation is what keeps a transfer alive after
Chrome closes the host.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from ytdm import engine
from ytdm.config import AppSettings
from ytdm.history import HistoryEntry, store as history
from ytdm.utils import helpers
from ytdm.utils.logger import get_logger

from . import jobs
from . import formats
from .__init__ import __version__ as host_version

log = get_logger()


class ServiceError(Exception):
    """A domain level failure with a protocol error code attached."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ── engine error translation ─────────────────────────────────────────────────
_NETWORK_MARKERS = (
    "unable to reach",
    "connection refused",
    "timed out",
    "network connection failed",
    "could not send head request",
    "no route to host",
)
_FFMPEG_MARKERS = ("ffmpeg", "ffprobe", "postprocessing", "not found")
_NOT_FOUND_MARKERS = (
    "video unavailable",
    "does not exist",
    "private video",
    "404",
    "not exist",
    "removed",
)


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Map an engine exception onto a (error_code, message) pair.

    yt-dlp reports failures as free-form stderr text, so we match on known
    phrases and fall back to a generic code, always preserving the original
    message for the user.
    """
    if isinstance(exc, engine.DownloadCancelled):
        return "CANCELLED", str(exc) or "download cancelled"
    if isinstance(exc, engine.BinaryError):
        return "ENGINE_MISSING", str(exc)
    text = str(exc).lower()
    if any(marker in text for marker in _NETWORK_MARKERS):
        return "NETWORK_ERROR", str(exc)
    if any(marker in text for marker in _NOT_FOUND_MARKERS):
        return "NOT_FOUND", str(exc)
    if any(marker in text for marker in _FFMPEG_MARKERS):
        return "FFMPEG_MISSING", str(exc)
    return "DOWNLOAD_ERROR", str(exc)


class DownloadService:
    """Stateless facade in front of the download engine and the job store."""

    # ── connection / environment ─────────────────────────────────────────────
    def ping(self) -> dict:
        """Report the host environment: engine, tools, paths and proxy."""
        try:
            engine_version = engine.version()
        except Exception as exc:  # noqa: BLE001 - ping must never fail
            engine_version = f"unavailable ({exc})"
            log.warning("engine.version() failed: %s", exc)
        else:
            if engine_version == "unknown":
                log.warning(
                    "engine.version() returned empty output; _run got nothing"
                )
        settings = AppSettings.load()
        ffmpeg = engine.find_ffmpeg()
        try:
            download_dir = str(settings.resolved_download_dir())
        except Exception:
            download_dir = str(Path("downloads").resolve())
        try:
            proxy = settings.proxy_or_none() or engine.system_proxy()
        except Exception as exc:  # noqa: BLE001
            proxy = ""
            log.warning("system_proxy() failed: %s", exc)
        return {
            "host_version": host_version,
            "ytdm_version": engine_version,
            "ffmpeg_available": ffmpeg is not None,
            "ffmpeg_path": str(ffmpeg) if ffmpeg else "",
            "download_dir": download_dir,
            "proxy": proxy or "",
            "supervisor_running": jobs.supervisor_is_alive(),
            "queue_length": len(jobs.active_jobs()),
            "supported_qualities": list(engine.SUPPORTED_QUALITIES),
        }

    # ── metadata ─────────────────────────────────────────────────────────────
    def get_info(self, url: str, proxy: str | None = None) -> dict:
        """Probe a URL and return the real metadata and choice lists."""
        settings = AppSettings.load()
        opts = engine.DownloadOptions(
            urls=[url],
            proxy=proxy or settings.proxy_or_none(),
        )
        try:
            meta = engine.probe(url, opts)
        except engine.DownloadError as exc:
            code, message = classify_error(exc)
            log.info("get_info failed for %s: %s", url, message)
            raise ServiceError(code, message) from exc
        except Exception as exc:  # noqa: BLE001 - surface every probe failure
            log.exception("unexpected probe failure for %s", url)
            raise ServiceError("HOST_INTERNAL", str(exc) or "probe failed") from exc

        info = formats.video_info(meta)
        # yt-dlp reports duration for the playlist entry too; keep it simple.
        if not info["title"]:
            info["title"] = url
        return info

    # ── downloads ────────────────────────────────────────────────────────────
    def _resolve_output_dir(self, requested: str | None) -> str:
        settings = AppSettings.load()
        if requested:
            path = Path(requested)
        else:
            path = settings.resolved_download_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ServiceError(
                "BAD_OUTPUT_DIR", f"cannot create or access '{path}': {exc}"
            ) from exc
        return str(path)

    def _is_duplicate(self, url: str, media_type: str, quality: str,
                      audio_format: str | None) -> jobs.Job | None:
        """An equivalent download already queued or running?"""
        for job in jobs.active_jobs():
            if (
                job.url == url
                and job.media_type == media_type
                and job.quality == quality
                and job.audio_format == audio_format
            ):
                return job
        return None

    def start_download(
        self,
        url: str,
        media_type: str,
        quality: str,
        audio_format: str | None,
        output_dir: str | None = None,
        proxy: str | None = None,
        playlist: bool = False,
        request_id: str | None = None,
    ) -> dict:
        """Record the request and hand it to the queue.

        The supervisor picks it up from the job store; we never block here.
        """
        settings = AppSettings.load()

        # Validate up front so a bad request fails now, not minutes later.
        if media_type == "audio":
            choices = formats.AUDIO_CHOICES
            if audio_format not in choices:
                raise ServiceError(
                    "BAD_AUDIO_FORMAT",
                    f"audio_format {audio_format!r} is not one of {', '.join(choices)}",
                )
            if audio_format in formats.AUDIO_NEEDS_FFMPEG and not engine.find_ffmpeg():
                raise ServiceError(
                    "FFMPEG_MISSING",
                    f"converting to {audio_format.upper()} requires FFmpeg, which "
                    "was not found. Use the original (best) format or install "
                    "FFmpeg next to the application.",
                )
        else:
            if quality not in engine.SUPPORTED_QUALITIES:
                raise ServiceError(
                    "BAD_QUALITY",
                    f"quality {quality!r} is not one of "
                    f"{', '.join(engine.SUPPORTED_QUALITIES)}",
                )

        resolved_dir = self._resolve_output_dir(output_dir)

        existing = self._is_duplicate(url, media_type, quality, audio_format)
        if existing is not None:
            log.info("duplicate download request, reusing job %s", existing.job_id)
            return {
                "job_id": existing.job_id,
                "status": existing.status,
                "already_queued": True,
                "message": "This download is already in the queue.",
            }

        job = jobs.Job(
            job_id=jobs.new_job_id(),
            request_id=request_id or "",
            url=url,
            media_type=media_type,
            quality=quality,
            audio_format=audio_format,
            output_dir=resolved_dir,
            proxy=proxy or settings.proxy_or_none(),
            playlist=bool(playlist),
        )
        jobs.save(job)
        jobs.prune()
        log.info(
            "queued job %s: %s [%s/%s] -> %s",
            job.job_id, url, media_type, quality, resolved_dir,
        )

        started = self.ensure_supervisor()
        return {
            "job_id": job.job_id,
            "status": job.status,
            "already_queued": False,
            "supervisor_started": started,
            "message": "Download queued." if started else "Download queued.",
        }

    def get_status(self, job_id: str) -> dict:
        job = jobs.load(job_id)
        if job is None:
            raise ServiceError("UNKNOWN_JOB", f"no download with id '{job_id}'")
        return {
            "job_id": job.job_id,
            "status": job.status,
            "title": job.title,
            "url": job.url,
            "media_type": job.media_type,
            "quality": job.quality,
            "audio_format": job.audio_format,
            "progress": job.progress or {},
            "output_files": job.output_files,
            "error_code": job.error_code,
            "error": job.error,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }

    def cancel_download(self, job_id: str) -> dict:
        job = jobs.load(job_id)
        if job is None:
            raise ServiceError("UNKNOWN_JOB", f"no download with id '{job_id}'")
        if job.status in jobs.TERMINAL_STATUSES:
            return {
                "job_id": job.job_id,
                "status": job.status,
                "cancelled": False,
                "message": f"download already {job.status}",
            }
        # The supervisor polls this flag between progress updates.
        job.cancel_requested = True
        jobs.save(job)
        log.info("cancel requested for job %s", job_id)
        return {
            "job_id": job.job_id,
            "status": job.status,
            "cancelled": True,
            "message": "Cancellation requested.",
        }

    def list_downloads(self, limit: int = 25) -> dict:
        recent = [job.summary() for job in jobs.load_all()[:limit]]
        return {"downloads": recent, "total": len(jobs.load_all())}

    # ── supervisor lifecycle ─────────────────────────────────────────────────
    def ensure_supervisor(self) -> bool:
        """Make sure the queue process is running, starting it if needed."""
        if jobs.supervisor_is_alive():
            return False
        return launch_supervisor()


def launch_supervisor() -> bool:
    """Start the detached queue supervisor that outlives this process."""
    exe = Path(sys.executable)
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        # A PyInstaller build of the host re-enters itself with this argument.
        args = [str(exe), "--supervisor"]
        cwd = str(exe.parent)
    else:
        args = [str(exe), "-I", str(Path(__file__).resolve().parent / "supervisor.py")]
        cwd = str(Path(__file__).resolve().parent.parent)

    log.info("starting supervisor: %s (cwd=%s)", " ".join(args), cwd)
    try:
        # Detached: it must survive Chrome killing the host process.
        if os.name == "nt":
            creationflags = (
                subprocess_DETACHED_PROCESS()
                | subprocess_CREATE_NEW_PROCESS_GROUP()
            )
            proc = subprocess.Popen(  # noqa: S603
                args,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                close_fds=True,
            )
        else:
            proc = subprocess.Popen(  # noqa: S603
                args,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        log.error("could not start supervisor: %s", exc)
        return False

    jobs.write_supervisor_state(proc.pid, time.time())
    return True


def subprocess_DETACHED_PROCESS() -> int:
    return getattr(subprocess, "DETACHED_PROCESS", 0x00000008)


def subprocess_CREATE_NEW_PROCESS_GROUP() -> int:
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def record_history(job: jobs.Job, status: str) -> None:
    """Append a finished job to the same history file the TUI reads."""
    primary = job.output_files[0] if job.output_files else ""
    quality_label = job.quality
    if job.media_type == "audio":
        quality_label = f"AUDIO .{(job.audio_format or 'best').upper()}"
    elif job.quality != "best":
        quality_label = f"{job.quality}p"
    try:
        history.add(
            HistoryEntry(
                title=job.title or job.url,
                url=job.url,
                path=primary,
                size=jobs.file_size(primary) if primary else 0,
                quality=quality_label,
                mode="AUDIO" if job.media_type == "audio" else "VIDEO",
                status=status,
                timestamp=helpers.timestamp(),
            )
        )
    except Exception:  # noqa: BLE001 - history must never break a download
        log.exception("could not write history entry")
