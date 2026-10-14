"""The detached download queue supervisor.

Chrome spawns the native host on demand and destroys it when the extension
closes its port. A download must outlive that, so the queue runs here instead:
the host hands the request to the job store, and this separate process picks it
up. It is started lazily by the host (see
:func:`download_service.launch_supervisor`) and exits on its own once the queue
has been empty for a while.

Only one supervisor is ever meant to run. Each claim writes its own pid into
the job record, so a stale second instance notices it lost the race and exits.

Usage from a frozen host build:  ``YT-DL-Host.exe --supervisor``
Usage from a source checkout:    ``py browser_integration/supervisor.py``
"""
from __future__ import annotations

import os
import argparse
import sys
import time
import traceback
from pathlib import Path

# Make `ytdm` and `browser_integration` importable regardless of the entry point.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from browser_integration import jobs  # noqa: E402
from browser_integration.download_service import (  # noqa: E402
    classify_error,
    record_history,
)
from ytdm import engine  # noqa: E402
from ytdm.config import AppSettings  # noqa: E402
from ytdm.utils.logger import get_logger  # noqa: E402

log = get_logger()

#: Exit once the queue has been idle this long, so we never linger forever.
IDLE_EXIT_SECONDS = 600
POLL_INTERVAL_SECONDS = 1.0
PROGRESS_WRITE_INTERVAL = 0.4


def _options_for(job: jobs.Job) -> engine.DownloadOptions:
    """Build the engine options for one queued job."""
    if job.media_type == "audio":
        # "best" means "keep the original stream"; the engine's audio path
        # handles a real container, so only ask for extraction when a specific
        # one was chosen.
        if job.audio_format and job.audio_format != "best":
            return engine.DownloadOptions(
                urls=[job.url],
                quality="best",
                audio_only=True,
                audio_format=job.audio_format,
                output_dir=Path(job.output_dir),
                proxy=job.proxy,
                playlist=job.playlist or None,
                threads=AppSettings.load().threads,
            )
        return engine.DownloadOptions(
            urls=[job.url],
            quality="best",
            audio_only=False,
            raw_format="bestaudio/best",
            output_dir=Path(job.output_dir),
            proxy=job.proxy,
            playlist=job.playlist or None,
            threads=AppSettings.load().threads,
        )

    return engine.DownloadOptions(
        urls=[job.url],
        quality=job.quality or "best",
        audio_only=False,
        output_dir=Path(job.output_dir),
        proxy=job.proxy,
        playlist=job.playlist or None,
        threads=AppSettings.load().threads,
    )


def _cleanup_part_files(output_dir: str) -> None:
    """Remove the fragments a killed download leaves behind."""
    directory = Path(output_dir)
    if not directory.is_dir():
        return
    for entry in directory.iterdir():
        if entry.suffix in {".part", ".ytdl"} or entry.name.endswith(".tmp"):
            try:
                entry.unlink()
            except OSError:
                pass


def _finish(job: jobs.Job, status: str, files: list[str], error_code: str | None,
            error: str | None) -> None:
    # Re-read first: the host may have set cancel_requested on disk while this
    # in-memory copy was running. Writing our stale copy would erase it.
    fresh = jobs.load(job.job_id)
    if fresh is not None:
        job = fresh
    job.status = status
    job.output_files = files
    job.error_code = error_code
    job.error = error
    job.finished_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    job.progress = {"percent": 100.0 if status == jobs.COMPLETED else job.progress.get("percent", 0.0)}
    jobs.save(job)


def run_job(job: jobs.Job) -> None:
    """Execute one queued download through the existing engine."""
    log.info("running job %s (%s) -> %s", job.job_id, job.url, job.output_dir)
    fresh = jobs.load(job.job_id)
    if fresh is not None:
        job = fresh
    job.status = jobs.RUNNING
    job.started_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    jobs.save(job)

    opts = _options_for(job)

    # Metadata first: the record needs a title and the popup shows it.
    try:
        meta = engine.probe(job.url, opts)
        job.title = str(meta.get("title") or job.url)
        job.duration = int(meta.get("duration") or 0) or None
        job.channel = str(meta.get("uploader") or meta.get("channel") or "")
        job.thumbnail = str(meta.get("thumbnail") or "")
        jobs.save(job)
    except Exception as exc:  # noqa: BLE001 - probe failure ends the job
        code, message = classify_error(exc)
        log.warning("job %s probe failed: %s", job.job_id, message)
        _finish(job, jobs.FAILED, [], code, message)
        record_history(job, "FAILED")
        return

    last_write = 0.0

    def on_progress(update) -> None:
        nonlocal last_write
        now = time.monotonic()
        if now - last_write < PROGRESS_WRITE_INTERVAL:
            return
        last_write = now
        snapshot = jobs.Progress.from_engine(update).to_dict()
        record = jobs.load(job.job_id)
        if record is not None:
            record.progress = snapshot
            jobs.save(record)

    def should_stop() -> bool:
        record = jobs.load(job.job_id)
        return bool(record is not None and record.cancel_requested)

    def on_log(line: str) -> None:
        log.info("yt-dlp [%s]: %s", job.job_id, line)

    try:
        files = engine.download(
            opts, on_progress=on_progress, on_log=on_log, should_stop=should_stop
        )
    except engine.DownloadCancelled:
        log.info("job %s cancelled by request", job.job_id)
        _cleanup_part_files(job.output_dir)
        _finish(job, jobs.CANCELLED, [], "CANCELLED", "Download cancelled by user.")
        record_history(job, "CANCELLED")
        return
    except Exception as exc:  # noqa: BLE001 - any engine failure ends the job
        code, message = classify_error(exc)
        log.warning("job %s failed: %s", job.job_id, message)
        _cleanup_part_files(job.output_dir)
        _finish(job, jobs.FAILED, [], code, message)
        record_history(job, "FAILED")
        return

    log.info("job %s completed, %d file(s)", job.job_id, len(files))
    _finish(job, jobs.COMPLETED, files, None, None)
    record_history(job, "SUCCESS")


def main(stay_alive: bool = False) -> int:
    log.info("supervisor pid %d starting", os.getpid())
    jobs.write_supervisor_state(os.getpid(), time.time())

    last_activity = time.time()
    own_pid = os.getpid()

    while True:
        try:
            jobs.write_supervisor_state(
                own_pid, time.time(), queue_length=len(jobs.active_jobs())
            )
        except Exception:  # noqa: BLE001 - heartbeat failure is not fatal
            pass

        try:
            job = jobs.claim_next_job(own_pid)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("failed to claim a job")
            job = None

        if job is not None:
            last_activity = time.time()
            run_job(job)
            continue

        if not stay_alive and time.time() - last_activity > IDLE_EXIT_SECONDS:
            log.info("queue empty for %ds, supervisor exiting", IDLE_EXIT_SECONDS)
            return 0

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process queued YT-DL downloads.")
    parser.add_argument(
        "--stay-alive", action="store_true",
        help="keep the background worker running while the queue is empty",
    )
    args = parser.parse_args()
    try:
        sys.exit(main(stay_alive=args.stay_alive))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:  # noqa: BLE001 - last resort, leave a trace in the log
        traceback.print_exc()
        sys.exit(1)
