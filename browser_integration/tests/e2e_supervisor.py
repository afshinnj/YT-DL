"""End-to-end supervisor test with a public, non-YouTube video.

The protocol's URL allowlist intentionally accepts YouTube only (that is what
the project supports). The supervisor itself does not care about the host, so
this script queues a job record directly and lets the queue process it, which
exercises exactly the code path a browser request takes.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "browser_integration"))

from browser_integration import jobs  # noqa: E402
from browser_integration.download_service import (  # noqa: E402
    DownloadService,
    launch_supervisor,
)

PUBLIC_VIDEO = "https://samplelib.com/lib/preview/mp4/sample-5s.mp4"


def main() -> int:
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    # A clean slate makes the run reproducible.
    for old in jobs.JOBS_DIR.glob("job-*.json"):
        old.unlink()

    service = DownloadService()
    result = service.start_download(
        url=PUBLIC_VIDEO,
        media_type="video",
        quality="best",
        audio_format=None,
        output_dir=str(ROOT / "downloads"),
        proxy=None,
        playlist=False,
    )
    job_id = result["job_id"]
    print(f"queued: {job_id} (supervisor_started={result['supervisor_started']})")

    deadline = time.time() + 120
    last = None
    while time.time() < deadline:
        status = service.get_status(job_id)
        summary = (status["status"], round(status["progress"].get("percent", 0.0), 1))
        if summary != last:
            print(f"  status={status['status']} "
                  f"percent={status['progress'].get('percent', 0.0):.1f}%")
            last = summary
        if status["status"] in jobs.TERMINAL_STATUSES:
            print(f"  files: {status['output_files']}")
            print(f"  error: {status.get('error_code')} {status.get('error') or ''}")
            return 0 if status["status"] == jobs.COMPLETED else 1
        time.sleep(0.7)

    print("TIMED OUT waiting for the download")
    return 2


if __name__ == "__main__":
    sys.exit(main())
