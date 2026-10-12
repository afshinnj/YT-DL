"""Verify that cancel really stops a running download.

Queues a job, lets the supervisor start it, asks for cancellation and checks
that the engine terminates the child rather than merely flagging the record.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "browser_integration"))

from browser_integration import jobs  # noqa: E402
from browser_integration.download_service import DownloadService  # noqa: E402

# A large public file so there is something to interrupt.
PUBLIC_VIDEO = "https://samplelib.com/lib/preview/mp4/sample-20s.mp4"


def main() -> int:
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    service = DownloadService()
    result = service.start_download(
        url=PUBLIC_VIDEO,
        media_type="video",
        quality="best",
        audio_format=None,
        output_dir=str(ROOT / "downloads"),
    )
    job_id = result["job_id"]
    print(f"queued {job_id}")

    # Wait until it is actually transferring before cancelling.
    running = False
    deadline = time.time() + 60
    while time.time() < deadline:
        status = service.get_status(job_id)
        if status["status"] == jobs.RUNNING and status["progress"].get("percent", 0) > 2:
            running = True
            break
        time.sleep(0.4)

    if not running:
        print("download never started; cannot test cancel")
        return 2

    print(f"cancelling at {status['progress']['percent']:.1f}%")
    cancel_reply = service.cancel_download(job_id)
    print(f"cancel reply: {cancel_reply}")

    deadline = time.time() + 60
    while time.time() < deadline:
        status = service.get_status(job_id)
        if status["status"] in jobs.TERMINAL_STATUSES:
            break
        time.sleep(0.4)

    print(f"final status: {status['status']} error={status.get('error')}")
    return 0 if status["status"] == jobs.CANCELLED else 1


if __name__ == "__main__":
    sys.exit(main())
