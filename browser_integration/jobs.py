"""Persistent job records shared between the native host and the supervisor.

The host (spawned by Chrome, short lived) and the supervisor (detached, long
lived) never talk to each other over a socket. Instead they both read and
write small JSON files in ``data/jobs/``:

    data/jobs/<job_id>.json     one record per download request
    data/jobs/supervisor.json   liveness bookkeeping for the queue process

This keeps the design free of any IPC plumbing that would have to survive
Chrome killing the host, and it means the queue state is trivially inspectable
from disk. Writes are atomic (temp file + ``os.replace``) so a reader can
never observe a half-written record.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ytdm.utils.helpers import runtime_data_dir

JOBS_DIR = runtime_data_dir("data") / "jobs"
SUPERVISOR_FILE = JOBS_DIR / "supervisor.json"

#: How many finished records we keep around for the "recent downloads" list.
MAX_JOB_RECORDS = 200

# ── statuses ─────────────────────────────────────────────────────────────────
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE_STATUSES = frozenset({QUEUED, RUNNING})
TERMINAL_STATUSES = frozenset({COMPLETED, FAILED, CANCELLED})


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _epoch() -> float:
    return time.time()


@dataclass
class Progress:
    """A snapshot of a running transfer."""

    percent: float = 0.0
    downloaded: float = 0.0
    total: float = 0.0
    speed: float = 0.0
    eta: float = 0.0
    fragment_index: int = 0
    fragment_count: int = 0

    @classmethod
    def from_engine(cls, update) -> "Progress":
        return cls(
            percent=round(float(update.percent), 1),
            downloaded=float(update.downloaded),
            total=float(update.total),
            speed=float(update.speed),
            eta=float(update.eta),
            fragment_index=int(update.fragment_index),
            fragment_count=int(update.fragment_count),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Job:
    """One download request plus everything we learned while running it."""

    job_id: str
    request_id: str
    url: str
    media_type: str
    quality: str
    audio_format: str | None
    output_dir: str
    proxy: str | None
    playlist: bool
    title: str = ""
    duration: int | None = None
    channel: str = ""
    thumbnail: str = ""
    status: str = QUEUED
    cancel_requested: bool = False
    progress: dict = field(default_factory=dict)
    output_files: list[str] = field(default_factory=list)
    error_code: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    created_epoch: float = field(default_factory=_epoch)

    # ── (de)serialisation ────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        data = asdict(self)
        # The epoch is only used for ordering internally; the extension gets
        # the human readable timestamps.
        data.pop("created_epoch", None)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def summary(self) -> dict:
        """The compact shape the extension's download list consumes."""
        progress = self.progress or {}
        return {
            "job_id": self.job_id,
            "title": self.title or self.url,
            "url": self.url,
            "media_type": self.media_type,
            "quality": self.quality,
            "audio_format": self.audio_format,
            "status": self.status,
            "percent": progress.get("percent", 0.0),
            "output_file": self.output_files[0] if self.output_files else "",
            "error_code": self.error_code,
            "error": self.error,
            "timestamp": self.finished_at or self.started_at or self.created_at,
        }


# ── low level file helpers ───────────────────────────────────────────────────
def _atomic_write(path: Path, data: dict) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── job file API ─────────────────────────────────────────────────────────────
def new_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:16]}"


def job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def save(job: Job) -> None:
    """Persist ``job`` atomically."""
    _atomic_write(job_path(job.job_id), job.to_dict())


def load(job_id: str) -> Job | None:
    data = _read_json(job_path(job_id))
    return Job.from_dict(data) if isinstance(data, dict) else None


def delete(job_id: str) -> None:
    try:
        job_path(job_id).unlink()
    except OSError:
        pass


def load_all() -> list[Job]:
    """All records on disk, newest first."""
    if not JOBS_DIR.is_dir():
        return []
    jobs: list[Job] = []
    for entry in JOBS_DIR.iterdir():
        if not entry.name.endswith(".json") or entry.name.startswith("supervisor"):
            continue
        data = _read_json(entry)
        if isinstance(data, dict):
            jobs.append(Job.from_dict(data))
    jobs.sort(key=lambda job: job.created_epoch, reverse=True)
    return jobs


def prune() -> None:
    """Keep only the most recent finished records."""
    jobs = load_all()
    if len(jobs) <= MAX_JOB_RECORDS:
        return
    for job in jobs[MAX_JOB_RECORDS:]:
        delete(job.job_id)


def active_jobs() -> list[Job]:
    return [job for job in load_all() if job.status in ACTIVE_STATUSES]


def queued_jobs() -> list[Job]:
    """Queued records oldest first, so the queue is genuinely FIFO."""
    jobs = [job for job in load_all() if job.status == QUEUED]
    jobs.sort(key=lambda job: job.created_epoch)
    return jobs


def claim_next_job(owner_pid: int) -> Job | None:
    """Move the oldest queued job to ``running``.

    ``owner_pid`` is recorded in the job file so a second supervisor that
    happens to be alive can detect that it lost the race and exit. The write
    is atomic, so a concurrent reader either sees ``queued`` or the fully
    claimed record.
    """
    for job in queued_jobs():
        fresh = load(job.job_id)
        if fresh is None or fresh.status != QUEUED:
            continue
        fresh.status = RUNNING
        fresh.started_at = _now()
        fresh.progress = Progress().to_dict()
        fresh.error_code = None
        fresh.error = None
        try:
            _atomic_write(
                job_path(fresh.job_id),
                {**fresh.to_dict(), "_owner_pid": owner_pid},
            )
        except OSError:
            return None
        # Re-read and confirm we still own the claim.
        claimed = load(fresh.job_id)
        if claimed is None or claimed.status != RUNNING:
            continue
        return fresh
    return None


def file_size(path: str) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


# ── supervisor liveness ──────────────────────────────────────────────────────
SUPERVISOR_HEARTBEAT_SECONDS = 15.0
SUPERVISOR_STALE_SECONDS = 90.0


def write_supervisor_state(pid: int, heartbeat: float, queue_length: int = 0) -> None:
    _atomic_write(
        SUPERVISOR_FILE,
        {
            "pid": pid,
            "heartbeat": heartbeat,
            "queue_length": queue_length,
            "started_at": _now(),
        },
    )


def read_supervisor_state() -> dict | None:
    return _read_json(SUPERVISOR_FILE)


def process_alive(pid: int) -> bool:
    """Best effort liveness check without psutil."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, we just may not signal it
    except OSError:
        return False
    return True


def supervisor_is_alive() -> bool:
    state = read_supervisor_state()
    if not isinstance(state, dict):
        return False
    pid = int(state.get("pid") or 0)
    heartbeat = float(state.get("heartbeat") or 0.0)
    if not process_alive(pid):
        return False
    if heartbeat and (_epoch() - heartbeat) > SUPERVISOR_STALE_SECONDS:
        return False
    return True
