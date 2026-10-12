"""Unit tests for the browser integration protocol.

Run:  py browser_integration/tests/test_protocol.py
"""
from __future__ import annotations

import io
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from browser_integration import protocol  # noqa: E402

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


# ── framing ──────────────────────────────────────────────────────────────────
def test_roundtrip() -> None:
    print("framing roundtrip")
    stream = io.BytesIO()
    message = {"hello": "سلام", "n": 1}
    protocol.write_message(stream, message)
    stream.seek(0)
    back = protocol.read_message(stream)
    check("roundtrip preserves unicode", back == message, f"got {back!r}")


def test_length_prefix() -> None:
    print("length prefix format")
    stream = io.BytesIO()
    protocol.write_message(stream, {"a": 1})
    raw = stream.getvalue()
    (length,) = struct.unpack("<I", raw[:4])
    check("prefix equals payload length", length == len(raw) - 4, f"{length} vs {len(raw)-4}")
    check("payload is valid json", json.loads(raw[4:].decode("utf-8")) == {"a": 1})


def test_eof_is_clean() -> None:
    print("clean EOF")
    check("empty stream -> None", protocol.read_message(io.BytesIO()) is None)
    partial = io.BytesIO(b"\x00\x00")
    check("truncated header -> None", protocol.read_message(partial) is None)


def test_zero_length_is_error() -> None:
    print("zero-length frame")
    stream = io.BytesIO(b"\x00\x00\x00\x00")
    try:
        protocol.read_message(stream)
        check("raises ProtocolError", False)
    except protocol.ProtocolError as exc:
        check("rejects zero length", exc.code == protocol.ERR_INVALID_REQUEST, exc.code)


def test_truncated_message() -> None:
    print("truncated frame")
    stream = io.BytesIO(struct.pack("<I", 100) + b"short")
    try:
        protocol.read_message(stream)
        check("raises ProtocolError", False)
    except protocol.ProtocolError as exc:
        check("raises ProtocolError", exc.code == protocol.ERR_INVALID_REQUEST, exc.code)


def test_oversized_message() -> None:
    print("oversized frame")
    stream = io.BytesIO(struct.pack("<I", protocol.MAX_MESSAGE_BYTES + 1) + b"x")
    try:
        protocol.read_message(stream)
        check("raises ProtocolError", False)
    except protocol.ProtocolError as exc:
        check("rejects oversize", exc.code == protocol.ERR_INVALID_REQUEST, exc.code)


def test_bad_json() -> None:
    print("invalid json payload")
    payload = b"{not json"
    stream = io.BytesIO(struct.pack("<I", len(payload)) + payload)
    try:
        protocol.read_message(stream)
        check("raises ProtocolError", False)
    except protocol.ProtocolError as exc:
        check("code INVALID_JSON", exc.code == protocol.ERR_INVALID_JSON, exc.code)


def test_non_object_json() -> None:
    print("array payload")
    payload = b"[1,2,3]"
    stream = io.BytesIO(struct.pack("<I", len(payload)) + payload)
    try:
        protocol.read_message(stream)
        check("raises ProtocolError", False)
    except protocol.ProtocolError as exc:
        check("rejects non-object", exc.code == protocol.ERR_INVALID_JSON, exc.code)


# ── request validation ───────────────────────────────────────────────────────
def validate(raw: dict):
    return protocol.validate_request(raw)


def expect_error(raw: dict, code: str, name: str) -> None:
    try:
        validate(raw)
        check(name, False, "validation unexpectedly passed")
    except protocol.ProtocolError as exc:
        check(name, exc.code == code, f"expected {code}, got {exc.code}: {exc}")


def test_validation() -> None:
    print("request validation")

    expect_error({}, protocol.ERR_BAD_PROTOCOL_VERSION, "missing version")
    expect_error(
        {"protocol_version": 2, "action": "ping"},
        protocol.ERR_BAD_PROTOCOL_VERSION,
        "unsupported version",
    )
    expect_error(
        {"protocol_version": "1", "action": "ping"},
        protocol.ERR_BAD_PROTOCOL_VERSION,
        "version must be int",
    )
    expect_error(
        {"protocol_version": 1, "action": "nope"},
        protocol.ERR_UNKNOWN_ACTION,
        "unknown action",
    )
    expect_error(
        {"protocol_version": 1, "action": "ping", "request_id": 5},
        protocol.ERR_BAD_FIELD,
        "request_id must be string",
    )

    ok = validate({"protocol_version": 1, "action": "ping", "request_id": "r1"})
    check("ping validates", ok.action == "ping" and ok.request_id == "r1")


def test_url_validation() -> None:
    print("url validation")

    good = [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtube.com/watch?v=abc123",
        "https://youtu.be/abc123",
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/embed/abc123",
        "https://www.youtube.com/live/abc123",
        '  "https://www.youtube.com/watch?v=abc123"  ',
    ]
    for url in good:
        req = validate(
            {"protocol_version": 1, "action": "get_info", "url": url}
        )
        check(f"accept {url.strip().strip(chr(34))[:45]}", req.params["url"].startswith("http"))

    bad = [
        "ftp://youtube.com/watch?v=x",
        "javascript:alert(1)",
        "https://vimeo.com/12345",
        "not a url",
        "https://youtube.com.evil.com/watch?v=x",
    ]
    for url in bad:
        expect_error(
            {"protocol_version": 1, "action": "get_info", "url": url},
            protocol.ERR_UNSUPPORTED_URL,
            f"reject {url[:40]!r}",
        )
    # An empty url fails earlier in validation: it is a missing field.
    expect_error(
        {"protocol_version": 1, "action": "get_info", "url": ""},
        protocol.ERR_MISSING_FIELD,
        "empty url is a missing field",
    )


def test_start_download_validation() -> None:
    print("start_download validation")

    raw = {
        "protocol_version": 1,
        "action": "start_download",
        "request_id": "r1",
        "url": "https://www.youtube.com/watch?v=abc123",
        "media_type": "video",
        "quality": "720",
    }
    req = validate(raw)
    check("video 720 validates", req.params["quality"] == "720")
    check("playlist defaults from url", req.params["playlist"] is False)

    req = validate({**raw, "media_type": "audio", "audio_format": "mp3"})
    check("audio mp3 validates", req.params["audio_format"] == "mp3")
    check("audio forces best quality", req.params["quality"] == protocol.AUDIO_ORIGINAL)

    expect_error({**raw, "media_type": "both"}, protocol.ERR_BAD_FIELD, "bad media_type")
    expect_error({**raw, "quality": "9999"}, protocol.ERR_BAD_QUALITY, "bad quality")
    expect_error(
        {**raw, "media_type": "audio", "audio_format": "flac"},
        protocol.ERR_BAD_AUDIO_FORMAT,
        "flac not offered",
    )
    expect_error(
        {**raw, "url": "https://evil.com/x"},
        protocol.ERR_UNSUPPORTED_URL,
        "bad url rejected",
    )
    expect_error({**raw, "playlist": "yes"}, protocol.ERR_BAD_FIELD, "playlist must be bool")


def test_output_dir_validation() -> None:
    print("output_dir validation")
    base = {
        "protocol_version": 1,
        "action": "start_download",
        "url": "https://www.youtube.com/watch?v=x",
        "media_type": "video",
        "quality": "best",
    }
    req = validate({**base, "output_dir": r"D:\My Videos"})
    check("absolute path accepted", req.params["output_dir"] == r"D:\My Videos")

    expect_error({**base, "output_dir": "downloads"}, protocol.ERR_BAD_OUTPUT_DIR, "relative rejected")
    expect_error(
        {**base, "output_dir": r"D:\Videos\..\..\Windows"},
        protocol.ERR_BAD_OUTPUT_DIR,
        "traversal rejected",
    )
    req = validate({**base, "output_dir": ""})
    check("empty output_dir -> None", req.params["output_dir"] is None)


def test_proxy_validation() -> None:
    print("proxy validation")
    base = {
        "protocol_version": 1,
        "action": "start_download",
        "url": "https://www.youtube.com/watch?v=x",
        "media_type": "video",
        "quality": "best",
    }
    req = validate({**base, "proxy": "socks5://127.0.0.1:1080"})
    check("socks5 accepted", req.params["proxy"].startswith("socks5://"))
    expect_error({**base, "proxy": "127.0.0.1:1080"}, protocol.ERR_BAD_FIELD, "scheme required")


def test_status_and_list() -> None:
    print("get_status / cancel / list validation")
    expect_error(
        {"protocol_version": 1, "action": "get_status"},
        protocol.ERR_MISSING_FIELD,
        "missing job_id",
    )
    req = validate(
        {"protocol_version": 1, "action": "get_status", "job_id": "  job-1  "}
    )
    check("job_id trimmed", req.params["job_id"] == "job-1")

    req = validate({"protocol_version": 1, "action": "list_downloads", "limit": 10})
    check("limit accepted", req.params["limit"] == 10)
    expect_error(
        {"protocol_version": 1, "action": "list_downloads", "limit": 0},
        protocol.ERR_BAD_FIELD,
        "limit too small",
    )
    expect_error(
        {"protocol_version": 1, "action": "list_downloads", "limit": "ten"},
        protocol.ERR_BAD_FIELD,
        "limit must be int",
    )


def test_bool_is_not_int() -> None:
    print("type strictness")
    expect_error(
        {"protocol_version": 1, "action": "list_downloads", "limit": True},
        protocol.ERR_BAD_FIELD,
        "bool rejected for int field",
    )


# ── response builders ────────────────────────────────────────────────────────
def test_responses() -> None:
    print("response builders")
    ok = protocol.ok("r1", job_id="job-1", status="queued")
    check("ok shape", ok == {
        "protocol_version": 1, "ok": True, "request_id": "r1",
        "job_id": "job-1", "status": "queued",
    })
    err = protocol.error("r2", protocol.ERR_BAD_QUALITY, "nope")
    check("error shape", err["ok"] is False and err["error_code"] == protocol.ERR_BAD_QUALITY)
    check("error carries request_id", err["request_id"] == "r2")


def main() -> int:
    for fn in (
        test_roundtrip, test_length_prefix, test_eof_is_clean,
        test_zero_length_is_error, test_truncated_message,
        test_oversized_message, test_bad_json,
        test_non_object_json, test_validation, test_url_validation,
        test_start_download_validation, test_output_dir_validation,
        test_proxy_validation, test_status_and_list, test_bool_is_not_int,
        test_responses,
    ):
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
