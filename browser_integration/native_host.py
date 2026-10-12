"""Chrome Native Messaging host for YT-DL.

Chrome spawns this process with the extension's messages on stdin and expects
replies on stdout, framed as::

    <uint32 little-endian length><UTF-8 JSON>

stdout is protocol-only. Every diagnostic goes to stderr and to the
application log file (``data/logs/app.log``), because a single stray byte on
stdout would corrupt the stream and make Chrome drop the connection.

This host stays deliberately thin. Each request is dispatched on a worker
thread; downloads themselves are never run here — they are handed to the
detached supervisor so they survive Chrome closing the port.

Entry points:
    python native_host.py              # serve Chrome (stdin/stdout protocol)
    python native_host.py --selftest   # verify the environment and exit
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Import the project root so `ytdm` resolves from a source checkout.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from browser_integration import jobs, protocol  # noqa: E402
from browser_integration.download_service import (  # noqa: E402
    DownloadService,
    ServiceError,
)
from ytdm.utils.logger import get_logger  # noqa: E402

log = get_logger()

HOST_NAME = "app.ytdm.nativehost"

# Long probes and downloads never run on the read loop; this pool holds them.
MAX_WORKERS = 4

# stdout is written to from the worker threads, so the writes need a lock.
_write_lock = threading.Lock()


class Host:
    """Dispatches validated protocol requests to the service layer."""

    def __init__(self, service: DownloadService) -> None:
        self.service = service

    # ── individual actions ───────────────────────────────────────────────────
    def _handle(self, request: protocol.ValidRequest) -> dict:
        action = request.action
        params = request.params

        if action == protocol.PING:
            env = self.service.ping()
            log.info("ping env: ytdm=%r proxy=%r", env.get("ytdm_version"), env.get("proxy"))
            return protocol.ok(
                request.request_id,
                status="connected",
                **env,
            )

        if action == protocol.GET_INFO:
            info = self.service.get_info(params["url"], params.get("proxy"))
            return protocol.ok(request.request_id, **info)

        if action == protocol.START_DOWNLOAD:
            result = self.service.start_download(
                url=params["url"],
                media_type=params["media_type"],
                quality=params["quality"],
                audio_format=params.get("audio_format"),
                output_dir=params.get("output_dir"),
                proxy=params.get("proxy"),
                playlist=params.get("playlist", False),
                request_id=request.request_id,
            )
            return protocol.ok(request.request_id, **result)

        if action == protocol.GET_STATUS:
            status = self.service.get_status(params["job_id"])
            return protocol.ok(request.request_id, **status)

        if action == protocol.CANCEL_DOWNLOAD:
            result = self.service.cancel_download(params["job_id"])
            return protocol.ok(request.request_id, **result)

        if action == protocol.LIST_DOWNLOADS:
            result = self.service.list_downloads(params["limit"])
            return protocol.ok(request.request_id, **result)

        # validate_request already rejects unknown actions.
        return protocol.error(
            request.request_id,
            protocol.ERR_UNKNOWN_ACTION,
            f"unhandled action {action!r}",
        )

    def dispatch(self, raw: dict, out) -> None:
        """Validate one raw message and write the matching reply to ``out``."""
        try:
            request = protocol.validate_request(raw)
        except protocol.ProtocolError as exc:
            request_id = raw.get("request_id") if isinstance(raw, dict) else None
            reply = protocol.error(None or request_id, exc.code, str(exc))
            self._write(out, reply)
            log.info("rejected request: %s (%s)", exc.code, exc)
            return

        try:
            reply = self._handle(request)
        except ServiceError as exc:
            reply = protocol.error(request.request_id, exc.code, str(exc))
            log.info("service error in %s: %s", request.action, exc)
        except Exception as exc:  # noqa: BLE001 - a host must always reply
            log.exception("unhandled error in %s", request.action)
            reply = protocol.error(
                request.request_id,
                protocol.ERR_HOST_INTERNAL,
                f"internal error: {exc}",
            )
        self._write(out, reply)

    def _write(self, out, reply: dict) -> None:
        with _write_lock:
            protocol.write_message(out, reply)


# ── process plumbing ─────────────────────────────────────────────────────────
def _binary_streams():
    """stdin/stdout as binary buffers, resilient to reconfigured wrappers."""
    stdin = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
    stdout = sys.stdout.buffer if hasattr(sys.stdout, "buffer") else sys.stdout
    return stdin, stdout


def _write_fatal(out, request_id, code: str, message: str) -> None:
    """Best-effort error reply when we cannot serve the request at all."""
    try:
        with _write_lock:
            protocol.write_message(out, protocol.error(request_id, code, message))
    except Exception:  # noqa: BLE001 - nothing left to do
        pass


def _print_environment() -> None:
    """Used by --selftest: report the tools the host can see (to stderr)."""
    from ytdm import engine

    print(f"host: {HOST_NAME}", file=sys.stderr)
    print(f"python: {sys.version.split()[0]}", file=sys.stderr)
    print(f"frozen: {getattr(sys, 'frozen', False)}", file=sys.stderr)
    print(f"jobs dir: {jobs.JOBS_DIR}", file=sys.stderr)
    try:
        print(f"yt-dlp: {engine.version()}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"yt-dlp: ERROR {exc}", file=sys.stderr)
    ffmpeg = engine.find_ffmpeg()
    print(f"ffmpeg: {ffmpeg or 'NOT FOUND'}", file=sys.stderr)


def serve() -> int:
    """Read framed requests until Chrome closes the stream."""
    stdin, stdout = _binary_streams()
    log.info(
        "host starting serve: stdin=%s stdout=%s",
        type(stdin).__name__, type(stdout).__name__,
    )
    service = DownloadService()
    host = Host(service)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ytdl-host") as pool:
        while True:
            try:
                raw = protocol.read_message(stdin)
            except protocol.ProtocolError as exc:
                # A malformed frame means the peer is not speaking our
                # protocol; say so and keep reading when possible.
                log.warning("malformed message: %s (%s)", exc.code, exc)
                _write_fatal(stdout, None, exc.code, str(exc))
                continue
            except (OSError, ValueError) as exc:
                log.info("stdin read error, exiting: %s", exc)
                return 0

            if raw is None:
                log.info("peer closed the connection, host exiting")
                return 0

            # Dispatch off the read loop so a slow probe never blocks a ping.
            pool.submit(host.dispatch, raw, stdout)


def selftest() -> int:
    _print_environment()
    try:
        service = DownloadService()
        ping = service.ping()
        print(f"ping: ok, ytdm={ping['ytdm_version']} "
              f"ffmpeg={ping['ffmpeg_available']} dir={ping['download_dir']}",
              file=sys.stderr)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if "--selftest" in args:
        return selftest()
    if "--supervisor" in args:
        # A frozen host build re-enters itself with this flag to start the
        # queue process; from source we run supervisor.py directly instead.
        from browser_integration import supervisor

        return supervisor.main()

    return serve()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
