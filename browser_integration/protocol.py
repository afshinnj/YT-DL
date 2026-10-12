"""The versioned JSON protocol spoken between the Chrome extension and the
native messaging host.

Wire format (Chrome Native Messaging):
    <uint32 little-endian message length><UTF-8 JSON bytes>

The length prefix counts only the JSON payload. stdout of the host process is
reserved for these frames and nothing else — every diagnostic goes to stderr or
to the application log file.

A request always carries the protocol version, an ``action`` and a client
generated ``request_id``. The host copies the same ``request_id`` into its
reply so the client can match answers to outstanding requests.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any, IO

from ytdm import engine
from ytdm.utils import helpers

PROTOCOL_VERSION = 1

#: Absolute ceiling on a single frame. Chrome itself caps native messages at
#: 1 MB; we accept a little more so a verbose reply can never be mistaken for
#: a corrupt stream.
MAX_MESSAGE_BYTES = 4 * 1024 * 1024

LENGTH_PREFIX = struct.Struct("<I")

# ── actions ──────────────────────────────────────────────────────────────────
PING = "ping"
GET_INFO = "get_info"
START_DOWNLOAD = "start_download"
GET_STATUS = "get_status"
CANCEL_DOWNLOAD = "cancel_download"
LIST_DOWNLOADS = "list_downloads"

ACTIONS = frozenset(
    {PING, GET_INFO, START_DOWNLOAD, GET_STATUS, CANCEL_DOWNLOAD, LIST_DOWNLOADS}
)

MEDIA_VIDEO = "video"
MEDIA_AUDIO = "audio"
MEDIA_TYPES = frozenset({MEDIA_VIDEO, MEDIA_AUDIO})

#: Audio containers the engine can produce on top of :data:`AUDIO_ORIGINAL`.
AUDIO_FORMATS = ("mp3", "m4a", "wav")
#: Sentinel meaning "keep the original audio stream, no re-encoding".
AUDIO_ORIGINAL = "best"

# ── error codes ──────────────────────────────────────────────────────────────
ERR_INVALID_JSON = "INVALID_JSON"
ERR_INVALID_REQUEST = "INVALID_REQUEST"
ERR_BAD_PROTOCOL_VERSION = "BAD_PROTOCOL_VERSION"
ERR_UNKNOWN_ACTION = "UNKNOWN_ACTION"
ERR_MISSING_FIELD = "MISSING_FIELD"
ERR_BAD_FIELD = "BAD_FIELD"
ERR_UNSUPPORTED_URL = "UNSUPPORTED_URL"
ERR_BAD_QUALITY = "BAD_QUALITY"
ERR_BAD_AUDIO_FORMAT = "BAD_AUDIO_FORMAT"
ERR_BAD_OUTPUT_DIR = "BAD_OUTPUT_DIR"
ERR_UNKNOWN_JOB = "UNKNOWN_JOB"
ERR_HOST_INTERNAL = "HOST_INTERNAL"
ERR_ENGINE_MISSING = "ENGINE_MISSING"
ERR_FFMPEG_MISSING = "FFMPEG_MISSING"
ERR_NETWORK = "NETWORK_ERROR"
ERR_NOT_FOUND = "NOT_FOUND"
ERR_DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
ERR_CANCELLED = "CANCELLED"
ERR_BAD_STATE = "BAD_STATE"

#: Human readable explanations the popup shows verbatim for the common cases.
ERROR_HINTS: dict[str, str] = {
    ERR_ENGINE_MISSING: "yt-dlp could not be found next to the application.",
    ERR_FFMPEG_MISSING: (
        "This format needs FFmpeg, which is bundled with the application. "
        "Reinstall or restore the bin/ffmpeg folder."
    ),
    ERR_NETWORK: (
        "The video site could not be reached. If it is filtered on your "
        "network, configure a proxy in the application settings."
    ),
    ERR_UNSUPPORTED_URL: "Only YouTube video and playlist links are supported.",
}


class ProtocolError(Exception):
    """Raised when a request cannot be accepted; carries an error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ── framing ──────────────────────────────────────────────────────────────────
def _read_exact(stream: IO[bytes], count: int) -> bytes | None:
    """Read exactly ``count`` bytes; ``None`` on a clean EOF mid-header."""
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream: IO[bytes]) -> dict | None:
    """Read one framed message. Returns ``None`` when the peer closed cleanly.

    Raises :class:`ProtocolError` for a truncated or oversized frame.
    """
    header = _read_exact(stream, LENGTH_PREFIX.size)
    if header is None:
        return None
    (length,) = LENGTH_PREFIX.unpack(header)
    if length <= 0:
        raise ProtocolError(ERR_INVALID_REQUEST, "empty message frame")
    if length > MAX_MESSAGE_BYTES:
        raise ProtocolError(
            ERR_INVALID_REQUEST,
            f"message of {length} bytes exceeds the {MAX_MESSAGE_BYTES} byte limit",
        )
    payload = _read_exact(stream, length)
    if payload is None:
        raise ProtocolError(ERR_INVALID_REQUEST, "message frame is truncated")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(ERR_INVALID_JSON, f"undecodable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProtocolError(ERR_INVALID_JSON, "top level JSON must be an object")
    return data


def write_message(stream: IO[bytes], message: dict) -> None:
    """Serialise ``message`` as one framed UTF-8 message."""
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    stream.write(LENGTH_PREFIX.pack(len(payload)))
    stream.write(payload)
    stream.flush()


# ── response builders ────────────────────────────────────────────────────────
def ok(request_id: str | None, **fields: Any) -> dict:
    """A successful reply; extra fields become the payload."""
    reply = {"protocol_version": PROTOCOL_VERSION, "ok": True, "request_id": request_id}
    reply.update(fields)
    return reply


def error(
    request_id: str | None, code: str, message: str, **fields: Any
) -> dict:
    """A failure reply, including the machine readable ``error_code``."""
    reply = {
        "protocol_version": PROTOCOL_VERSION,
        "ok": False,
        "request_id": request_id,
        "error_code": code,
        "error": message,
    }
    reply.update(fields)
    return reply


# ── request validation ───────────────────────────────────────────────────────
def _require(message: dict, name: str, kind: type) -> Any:
    if name not in message:
        raise ProtocolError(ERR_MISSING_FIELD, f"missing required field '{name}'")
    value = message[name]
    # bool is a subclass of int; accept it only where a bool is expected.
    if kind is int and isinstance(value, bool):
        raise ProtocolError(ERR_BAD_FIELD, f"field '{name}' must be an integer")
    if not isinstance(value, kind):
        raise ProtocolError(
            ERR_BAD_FIELD, f"field '{name}' must be a {kind.__name__}, "
            f"got {type(value).__name__}"
        )
    return value


def _validate_url(message: dict) -> str:
    url = helpers.normalize_url(str(_require(message, "url", str)))
    if not url:
        raise ProtocolError(ERR_MISSING_FIELD, "field 'url' must not be empty")
    if not url.lower().startswith(("http://", "https://")):
        raise ProtocolError(
            ERR_UNSUPPORTED_URL, f"URL must use http or https: {url[:120]}"
        )
    if not helpers.looks_like_url(url):
        raise ProtocolError(
            ERR_UNSUPPORTED_URL,
            f"not a recognised YouTube link: {helpers.truncate(url, 120)}",
        )
    return url


def _validate_output_dir(message: dict) -> str | None:
    if "output_dir" not in message or message["output_dir"] in (None, ""):
        return None
    raw = str(message["output_dir"]).strip().strip('"').strip("'")
    if not raw:
        return None
    path = helpers.safe_output_dir(raw)
    if path is None:
        raise ProtocolError(
            ERR_BAD_OUTPUT_DIR,
            "output_dir must be an absolute path and may not contain '..'",
        )
    return str(path)


def _validate_proxy(message: dict) -> str | None:
    if "proxy" not in message or message["proxy"] in (None, ""):
        return None
    proxy = str(message["proxy"]).strip()
    if not proxy.lower().startswith(("http://", "https://", "socks5://", "socks4://")):
        raise ProtocolError(
            ERR_BAD_FIELD,
            "proxy must be a http(s) or socks URL, e.g. http://127.0.0.1:10808",
        )
    return proxy


@dataclass
class ValidRequest:
    """A request that passed schema validation."""

    action: str
    request_id: str | None
    params: dict = field(default_factory=dict)


def validate_request(message: dict) -> ValidRequest:
    """Check types, action and per-action parameters.

    Raises :class:`ProtocolError` with one of the ``ERR_*`` codes so the caller
    can turn it straight into an error reply.
    """
    version = message.get("protocol_version")
    if version is None:
        raise ProtocolError(ERR_BAD_PROTOCOL_VERSION, "missing protocol_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProtocolError(
            ERR_BAD_PROTOCOL_VERSION, "protocol_version must be an integer"
        )
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            ERR_BAD_PROTOCOL_VERSION,
            f"protocol_version {version} is not supported (need {PROTOCOL_VERSION})",
        )

    action = message.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise ProtocolError(
            ERR_UNKNOWN_ACTION,
            f"unknown action {action!r}; expected one of {', '.join(sorted(ACTIONS))}",
        )

    request_id = message.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        raise ProtocolError(ERR_BAD_FIELD, "request_id must be a string")

    params: dict[str, Any] = {}

    if action == PING:
        pass

    elif action == GET_INFO:
        params["url"] = _validate_url(message)
        params["proxy"] = _validate_proxy(message)

    elif action == START_DOWNLOAD:
        params["url"] = _validate_url(message)
        media_type = message.get("media_type", MEDIA_VIDEO)
        if media_type not in MEDIA_TYPES:
            raise ProtocolError(
                ERR_BAD_FIELD,
                f"media_type must be one of {', '.join(sorted(MEDIA_TYPES))}",
            )
        params["media_type"] = media_type

        if "playlist" in message and message["playlist"] is not None:
            playlist = message["playlist"]
            if not isinstance(playlist, bool):
                raise ProtocolError(ERR_BAD_FIELD, "playlist must be a boolean")
            params["playlist"] = playlist
        else:
            params["playlist"] = helpers.is_playlist_url(params["url"])

        params["output_dir"] = _validate_output_dir(message)
        params["proxy"] = _validate_proxy(message)

        if media_type == MEDIA_AUDIO:
            audio_format = str(message.get("audio_format") or "mp3").strip().lower()
            allowed = {*AUDIO_FORMATS, AUDIO_ORIGINAL}
            if audio_format not in allowed:
                raise ProtocolError(
                    ERR_BAD_AUDIO_FORMAT,
                    f"audio_format {audio_format!r} is not one of "
                    f"{', '.join(sorted(allowed))}",
                )
            params["audio_format"] = audio_format
            params["quality"] = AUDIO_ORIGINAL
        else:
            quality = str(message.get("quality") or "best").strip().lower()
            if quality not in engine.SUPPORTED_QUALITIES:
                raise ProtocolError(
                    ERR_BAD_QUALITY,
                    f"quality {quality!r} is not available; choose one of "
                    f"{', '.join(engine.SUPPORTED_QUALITIES)}",
                )
            params["quality"] = quality
            params["audio_format"] = None

    elif action == GET_STATUS:
        job_id = str(_require(message, "job_id", str)).strip()
        if not job_id:
            raise ProtocolError(ERR_MISSING_FIELD, "field 'job_id' must not be empty")
        params["job_id"] = job_id

    elif action == CANCEL_DOWNLOAD:
        job_id = str(_require(message, "job_id", str)).strip()
        if not job_id:
            raise ProtocolError(ERR_MISSING_FIELD, "field 'job_id' must not be empty")
        params["job_id"] = job_id

    elif action == LIST_DOWNLOADS:
        limit = 25
        if "limit" in message and message["limit"] is not None:
            limit = _require(message, "limit", int)
            if limit < 1 or limit > 200:
                raise ProtocolError(ERR_BAD_FIELD, "limit must be between 1 and 200")
        params["limit"] = limit

    return ValidRequest(action=action, request_id=request_id, params=params)
