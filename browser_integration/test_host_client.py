"""Drive the native host the same way Chrome does, from the command line.

Chrome Native Messaging is just: read a 4-byte little-endian length, then that
many bytes of UTF-8 JSON. This helper speaks that protocol over the host's
stdin/stdout so the whole pipeline can be exercised without a browser.

Usage:
    py browser_integration/test_host_client.py ping
    py browser_integration/test_host_client.py get_info <URL>
    py browser_integration/test_host_client.py start <URL> [video|audio] [quality|fmt]
    py browser_integration/test_host_client.py status <job_id>
    py browser_integration/test_host_client.py cancel <job_id>
    py browser_integration/test_host_client.py list [limit]
    py browser_integration/test_host_client.py --raw '{"action":"ping",...}'
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOST = HERE / "native_host.py"
PREFIX = struct.Struct("<I")


def encode(message: dict) -> bytes:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return PREFIX.pack(len(payload)) + payload


def read_exact(stream, count: int) -> bytes | None:
    chunks = []
    remaining = count
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream):
    header = read_exact(stream, 4)
    if header is None:
        return None
    (length,) = PREFIX.unpack(header)
    payload = read_exact(stream, length)
    if payload is None:
        return None
    return json.loads(payload.decode("utf-8"))


def request_id() -> str:
    return f"req-{int(time.time() * 1000)}-{os.getpid()}"


def build_message(argv: list[str]) -> dict:
    if argv[0] == "--raw":
        return json.loads(argv[1])
    # Short aliases for convenience at the command line.
    aliases = {"start": "start_download", "status": "get_status",
               "cancel": "cancel_download", "list": "list_downloads"}
    action = aliases.get(argv[0], argv[0])
    msg = {"protocol_version": 1, "action": action, "request_id": request_id()}
    if action == "ping":
        pass
    elif action == "get_info":
        msg["url"] = argv[1]
        if len(argv) > 2:
            msg["proxy"] = argv[2]
    elif action == "start_download":
        msg["url"] = argv[1]
        msg["media_type"] = argv[2] if len(argv) > 2 else "video"
        if msg["media_type"] == "audio":
            msg["audio_format"] = argv[3] if len(argv) > 3 else "best"
        else:
            msg["quality"] = argv[3] if len(argv) > 3 else "best"
        if len(argv) > 4:
            msg["output_dir"] = argv[4]
    elif action == "get_status":
        msg["job_id"] = argv[1]
    elif action == "cancel_download":
        msg["job_id"] = argv[1]
    elif action == "list_downloads":
        if len(argv) > 1:
            msg["limit"] = int(argv[1])
    else:
        raise SystemExit(f"unknown action {action!r}")
    return msg


def main(argv: list[str]) -> int:
    message = build_message(argv)
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-I", str(HOST)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(ROOT),
    )
    proc.stdin.write(encode(message))
    proc.stdin.flush()

    reply = read_message(proc.stdout)
    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    stderr = proc.stderr.read().decode("utf-8", "replace").strip()
    print(json.dumps(reply, indent=2, ensure_ascii=False))
    if stderr:
        print("--- host stderr ---", file=sys.stderr)
        print(stderr, file=sys.stderr)
    if reply is None or not reply.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    sys.exit(main(args))
