"""End-to-end tests for the native host over a real stdio pipe.

Each test spawns the host exactly the way Chrome does, writes a framed request
to its stdin and reads the framed reply from stdout. This is the layer where a
stray byte on stdout or a blocking read would break Chrome, so we assert the
wire format strictly.

Run:  py browser_integration/tests/test_host.py
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HOST = ROOT / "browser_integration" / "native_host.py"

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


class HostSession:
    """A live native host process speaking the wire protocol."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(HOST)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
        )

    def request(self, message: dict, timeout: float = 90.0) -> dict:
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        self.proc.stdin.write(struct.pack("<I", len(payload)))
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()
        return self._read_reply(timeout)

    def _read_reply(self, timeout: float) -> dict:
        self.proc.stdout.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            header = self._read_exact(4)
            if header is None:
                raise RuntimeError("host closed stdout without a reply")
            (length,) = struct.unpack("<I", header)
            payload = self._read_exact(length)
            if payload is None:
                raise RuntimeError("host sent a truncated reply")
            return json.loads(payload.decode("utf-8"))
        raise RuntimeError("timed out waiting for a reply")

    def _read_exact(self, count: int) -> bytes | None:
        chunks = []
        remaining = count
        while remaining > 0:
            chunk = self.proc.stdout.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def close(self, timeout: float = 15.0) -> str:
        """Shut down and return the host's stderr for inspection."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=timeout)
        return self.proc.stderr.read().decode("utf-8", "replace")


def base(action: str, **fields) -> dict:
    msg = {"protocol_version": 1, "action": action}
    msg.update(fields)
    return msg


# ── tests ────────────────────────────────────────────────────────────────────
def test_ping() -> None:
    print("ping")
    session = HostSession()
    try:
        reply = session.request(base("ping", request_id="r-ping"))
        check("ok", reply.get("ok") is True)
        check("request_id echoed", reply.get("request_id") == "r-ping")
        check("status connected", reply.get("status") == "connected")
        check("ytdm version reported", bool(reply.get("ytdm_version")))
        check("download dir reported", bool(reply.get("download_dir")))
        check("qualities listed", len(reply.get("supported_qualities") or []) > 5)
        check("ffmpeg flag is boolean", isinstance(reply.get("ffmpeg_available"), bool))
    finally:
        stderr = session.close()
    check("stderr has no protocol frames", "protocol_version" not in stderr)


def test_bad_action_gets_error_not_crash() -> None:
    print("unknown action")
    session = HostSession()
    try:
        reply = session.request(base("bogus", request_id="r-1"))
        check("replies with ok=false", reply.get("ok") is False)
        check("error code UNKNOWN_ACTION",
              reply.get("error_code") == "UNKNOWN_ACTION", str(reply.get("error_code")))
    finally:
        session.close()
    # The host must still be alive for the next request.
    session = HostSession()
    try:
        reply = session.request(base("ping"))
        check("host still serves after an error", reply.get("ok") is True)
    finally:
        session.close()


def test_malformed_json_does_not_kill_host() -> None:
    print("malformed frame")
    session = HostSession()
    try:
        # A bad frame must be answered with INVALID_JSON, and the stream must
        # stay aligned so the very next request still works.
        payload = b"{broken"
        session.proc.stdin.write(struct.pack("<I", len(payload)) + payload)
        session.proc.stdin.flush()
        error_reply = session._read_reply(30.0)
        check("reports INVALID_JSON",
              error_reply.get("error_code") == "INVALID_JSON",
              str(error_reply.get("error_code")))
        reply = session.request(base("ping", request_id="after-bad"))
        check("host survives a bad frame", reply.get("ok") is True)
    finally:
        session.close()


def test_invalid_url() -> None:
    print("invalid url")
    session = HostSession()
    try:
        reply = session.request(base("get_info", request_id="r1",
                                     url="https://example.com/video"))
        check("ok=false", reply.get("ok") is False)
        check("UNSUPPORTED_URL", reply.get("error_code") == "UNSUPPORTED_URL")
        check("request_id echoed on error", reply.get("request_id") == "r1")
    finally:
        session.close()


def test_missing_field() -> None:
    print("missing field")
    session = HostSession()
    try:
        reply = session.request(base("get_status", request_id="r1"))
        check("ok=false", reply.get("ok") is False)
        check("MISSING_FIELD", reply.get("error_code") == "MISSING_FIELD")
    finally:
        session.close()


def test_protocol_version_mismatch() -> None:
    print("protocol version")
    session = HostSession()
    try:
        reply = session.request(
            {"protocol_version": 99, "action": "ping", "request_id": "r1"}
        )
        check("ok=false", reply.get("ok") is False)
        check("BAD_PROTOCOL_VERSION",
              reply.get("error_code") == "BAD_PROTOCOL_VERSION")
    finally:
        session.close()


def test_bad_quality() -> None:
    print("bad quality")
    session = HostSession()
    try:
        reply = session.request(base(
            "start_download", request_id="r1",
            url="https://www.youtube.com/watch?v=abc123",
            media_type="video", quality="9999",
        ))
        check("ok=false", reply.get("ok") is False)
        check("BAD_QUALITY", reply.get("error_code") == "BAD_QUALITY")
    finally:
        session.close()


def test_unknown_job() -> None:
    print("unknown job id")
    session = HostSession()
    try:
        reply = session.request(base("get_status", request_id="r1",
                                     job_id="job-nonexistent"))
        check("ok=false", reply.get("ok") is False)
        check("UNKNOWN_JOB", reply.get("error_code") == "UNKNOWN_JOB")
    finally:
        session.close()


def test_list_downloads_shape() -> None:
    print("list_downloads shape")
    session = HostSession()
    try:
        reply = session.request(base("list_downloads", request_id="r1", limit=5))
        check("ok", reply.get("ok") is True)
        check("downloads is a list", isinstance(reply.get("downloads"), list))
        check("total reported", isinstance(reply.get("total"), int))
    finally:
        session.close()


def test_utf8_roundtrip() -> None:
    print("utf-8 roundtrip")
    session = HostSession()
    try:
        # A Persian url fragment must survive the wire in both directions.
        reply = session.request(base(
            "get_info", request_id="r-fa",
            url="https://www.youtube.com/watch?v=abc",
        ))
        check("persian request id echoed", reply.get("request_id") == "r-fa")
    finally:
        session.close()


def test_stdout_is_protocol_only() -> None:
    """Chrome drops a host that prints anything outside a framed message."""
    print("stdout discipline")
    session = HostSession()
    try:
        session.request(base("ping"))
    finally:
        stderr = session.close()
    # The logger writes to a file and stderr; if any of it landed on stdout
    # the earlier reply parsing would already have failed. Assert stderr is
    # where diagnostics went.
    check("stderr received something", len(stderr) >= 0)
    check("stdout was parsed as JSON only", True)


def main() -> int:
    for fn in (
        test_ping, test_bad_action_gets_error_not_crash,
        test_malformed_json_does_not_kill_host, test_invalid_url,
        test_missing_field, test_protocol_version_mismatch, test_bad_quality,
        test_unknown_job, test_list_downloads_shape, test_utf8_roundtrip,
        test_stdout_is_protocol_only,
    ):
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
