"""Unit tests for the job store and the format extraction layer.

Run:  py browser_integration/tests/test_jobs.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from browser_integration import formats, jobs  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {detail}")


def with_temp_jobs(fn):
    """Run fn with the job store redirected to a throwaway directory."""
    tmp = Path(tempfile.mkdtemp(prefix="ytdl-jobs-"))
    original_dir = jobs.JOBS_DIR
    jobs.JOBS_DIR = tmp / "jobs"
    jobs.SUPERVISOR_FILE = jobs.JOBS_DIR / "supervisor.json"
    try:
        fn()
    finally:
        jobs.JOBS_DIR = original_dir
        jobs.SUPERVISOR_FILE = original_dir / "supervisor.json"


def test_job_roundtrip() -> None:
    print("job roundtrip")

    def run():
        job = jobs.Job(
            job_id="job-abc",
            request_id="req-1",
            url="https://www.youtube.com/watch?v=x",
            media_type="video",
            quality="720",
            audio_format=None,
            output_dir=r"D:\Videos",
            proxy=None,
            playlist=False,
            title="A title",
        )
        jobs.save(job)
        back = jobs.load("job-abc")
        check("persisted", back is not None)
        check("fields preserved", back.url == job.url and back.title == "A title")
        check("unknown id -> None", jobs.load("job-missing") is None)

    with_temp_jobs(run)


def test_atomic_write_leaves_no_tmp() -> None:
    print("atomic writes")

    def run():
        job = jobs.Job(
            job_id="job-1", request_id="r", url="u",
            media_type="video", quality="best", audio_format=None,
            output_dir="out", proxy=None, playlist=False,
        )
        jobs.save(job)
        leftovers = [p.name for p in jobs.JOBS_DIR.iterdir() if p.name.endswith(".tmp")]
        check("no leftover .tmp files", leftovers == [], str(leftovers))
        # A reader sees complete JSON even mid-write: parse the file back.
        raw = jobs.job_path("job-1").read_text(encoding="utf-8")
        check("file is valid json", isinstance(json.loads(raw), dict))

    with_temp_jobs(run)


def test_queue_ordering() -> None:
    print("queue ordering")

    def run():
        for i in range(3):
            job = jobs.Job(
                job_id=f"job-{i}", request_id="r", url="u",
                media_type="video", quality="best", audio_format=None,
                output_dir="out", proxy=None, playlist=False,
            )
            job.created_epoch = float(i)
            jobs.save(job)
            time.sleep(0.001)
        queued = [job.job_id for job in jobs.queued_jobs()]
        check("FIFO order", queued == ["job-0", "job-1", "job-2"], str(queued))

    with_temp_jobs(run)


def test_claim_and_duplicate() -> None:
    print("claim and duplicate detection")

    def run():
        job = jobs.Job(
            job_id="job-1", request_id="r", url="u",
            media_type="video", quality="best", audio_format=None,
            output_dir="out", proxy=None, playlist=False,
        )
        jobs.save(job)
        claimed = jobs.claim_next_job(1234)
        check("claim returned the job", claimed is not None)
        check("status is running", claimed.status == jobs.RUNNING)
        # _owner_pid is an internal marker written next to the record, not a
        # dataclass field, so read the raw file to see it.
        raw = json.loads(jobs.job_path("job-1").read_text(encoding="utf-8"))
        check("owner pid recorded", raw.get("_owner_pid") == 1234, str(raw.get("_owner_pid")))
        back = jobs.load("job-1")
        check("owner pid hidden from the public record",
              "_owner_pid" not in back.to_dict())
        check("empty queue afterwards", jobs.claim_next_job(1234) is None)

    with_temp_jobs(run)


def test_prune() -> None:
    print("prune keeps the newest records")

    def run():
        for i in range(jobs.MAX_JOB_RECORDS + 10):
            job = jobs.Job(
                job_id=f"job-{i:04d}", request_id="r", url="u",
                media_type="video", quality="best", audio_format=None,
                output_dir="out", proxy=None, playlist=False,
                status=jobs.COMPLETED,
            )
            job.created_epoch = float(i)
            jobs.save(job)
        jobs.prune()
        remaining = jobs.load_all()
        check("capped at MAX_JOB_RECORDS", len(remaining) == jobs.MAX_JOB_RECORDS,
              str(len(remaining)))
        check("kept the newest", remaining[0].job_id == f"job-{jobs.MAX_JOB_RECORDS + 9:04d}")

    with_temp_jobs(run)


def test_supervisor_state() -> None:
    print("supervisor liveness bookkeeping")

    def run():
        check("no state -> not alive", jobs.supervisor_is_alive() is False)
        jobs.write_supervisor_state(os_getpid(), time.time())
        check("fresh state for a live pid -> alive", jobs.supervisor_is_alive() is True)
        jobs.write_supervisor_state(os_getpid(), time.time() - jobs.SUPERVISOR_STALE_SECONDS * 2)
        check("stale heartbeat -> not alive", jobs.supervisor_is_alive() is False)

    with_temp_jobs(run)


def os_getpid() -> int:
    import os

    return os.getpid()


def test_progress_from_engine() -> None:
    print("progress conversion")
    from ytdm import engine

    update = engine.ProgressUpdate(
        downloaded=500, total=1000, speed=100.0, eta=5.0,
        fragment_index=2, fragment_count=4,
    )
    snap = jobs.Progress.from_engine(update)
    check("percent is 50", snap.percent == 50.0, str(snap.percent))
    check("roundtrips to dict", snap.to_dict()["speed"] == 100.0)


# ── format extraction ────────────────────────────────────────────────────────
SAMPLE_META = {
    "title": "Big Buck Bunny",
    "uploader": "Blender",
    "channel": "Blender Foundation",
    "duration": 635,
    "view_count": 12345,
    "id": "aqz-KE-bpKQ",
    "extractor_key": "Youtube",
    "thumbnail": "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg",
    "formats": [
        {"format_id": "137", "ext": "mp4", "height": 1080, "width": 1920,
         "vcodec": "avc1", "acodec": "none", "fps": 30,
         "filesize": 50_000_000},
        {"format_id": "136", "ext": "mp4", "height": 720, "width": 1280,
         "vcodec": "avc1", "acodec": "none", "fps": 30,
         "filesize": 20_000_000},
        {"format_id": "134", "ext": "mp4", "height": 360, "width": 640,
         "vcodec": "avc1", "acodec": "mp4a", "fps": 30,
         "filesize": 8_000_000},
        {"format_id": "140", "ext": "m4a", "height": None, "vcodec": "none",
         "acodec": "mp4a", "filesize": 4_000_000},
        {"format_id": "sb0", "ext": "mhtml", "height": 27, "vcodec": "none",
         "acodec": "none"},
    ],
}


def test_video_streams() -> None:
    print("video_streams")
    streams = formats.video_streams(SAMPLE_META)
    heights = [s.height for s in streams]
    check("three video heights", heights == [1080, 720, 360], str(heights))
    check("storyboard skipped", all(s.height != 27 for s in streams))
    seven20 = next(s for s in streams if s.height == 720)
    check("size carried through", seven20.size == 20_000_000)
    check("audio flag detected", seven20.has_audio is False)
    audio_included = next(s for s in streams if s.height == 360)
    check("has_audio true when acodec set", audio_included.has_audio is True)


def test_available_qualities() -> None:
    print("available_qualities")
    entries = formats.available_qualities(SAMPLE_META)
    by_q = {e["quality"]: e for e in entries}
    check("best is first", entries[0]["quality"] == "best")
    check("best reports max height", entries[0]["height"] == 1080)
    check("1080 available", by_q["1080"]["available"] is True)
    check("720 available", by_q["720"]["available"] is True)
    check("2160 not offered", by_q["2160"]["available"] is False)
    check("2160 flagged for the user", "not offered" in by_q["2160"]["note"])
    # The selector is a ceiling, so a height the video does not carry is still
    # satisfiable by the best stream below it; 480 falls back to 360 here.
    check("480 satisfiable via 360 fallback", by_q["480"]["available"] is True)
    check("480 notes the fallback", "360p" in by_q["480"]["note"], by_q["480"]["note"])
    check("1440 above the max height is not offered", by_q["1440"]["available"] is False)
    check("720 size includes audio track",
          by_q["720"]["size"] == 20_000_000 + 4_000_000,
          str(by_q["720"]["size"]))


def test_available_audio() -> None:
    print("available_audio_formats")
    entries = formats.available_audio_formats(SAMPLE_META)
    by_f = {e["format"]: e for e in entries}
    check("original always listed", "best" in by_f)
    check("m4a offered (native)", by_f["m4a"]["available"] is True)
    # FFmpeg presence decides mp3/wav in this environment.
    from ytdm import engine

    expected = engine.find_ffmpeg() is not None
    check(f"mp3 availability matches ffmpeg ({expected})",
          by_f["mp3"]["available"] is expected)
    check(f"wav availability matches ffmpeg ({expected})",
          by_f["wav"]["available"] is expected)


def test_available_audio_no_streams() -> None:
    print("audio without streams")
    entries = formats.available_audio_formats({"formats": []})
    check("all unavailable", all(e["available"] is False for e in entries))


def test_video_info_block() -> None:
    print("video_info")
    info = formats.video_info(SAMPLE_META)
    check("title", info["title"] == "Big Buck Bunny")
    check("channel", info["channel"] == "Blender")
    check("duration", info["duration"] == 635)
    check("thumbnail", info["thumbnail"].startswith("https://"))
    check("qualities present", len(info["qualities"]) > 1)
    check("audio formats present", len(info["audio_formats"]) > 1)


def main() -> int:
    for fn in (
        test_job_roundtrip, test_atomic_write_leaves_no_tmp, test_queue_ordering,
        test_claim_and_duplicate, test_prune, test_supervisor_state,
        test_progress_from_engine, test_video_streams, test_available_qualities,
        test_available_audio, test_available_audio_no_streams, test_video_info_block,
    ):
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
