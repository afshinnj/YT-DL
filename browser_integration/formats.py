"""Turn yt-dlp's metadata into the choice lists the extension shows.

The engine already owns *how* a chosen quality is downloaded (the format
selectors in :data:`ytdm.engine.QUALITY_SELECTORS`). This module only answers
*what* is offered: which heights the page actually provides, which audio
containers are producible, and a size estimate for each.

Nothing here is invented — every entry is derived from the ``formats`` array
yt-dlp returned for the real URL.
"""
from __future__ import annotations

from dataclasses import dataclass

from ytdm import engine

# Ordered the way a quality picker should present them: best first.
QUALITY_ORDER = ["2160", "1440", "1080", "720", "480", "360", "240", "144"]

QUALITY_LABELS: dict[str, str] = {
    "2160": "2160p · 4K",
    "1440": "1440p · QHD",
    "1080": "1080p · FHD",
    "720": "720p · HD",
    "480": "480p",
    "360": "360p",
    "240": "240p",
    "144": "144p",
}

#: Audio formats offered by the extension, in display order. "best" keeps the
#: original stream and performs no re-encode, so it works without FFmpeg.
AUDIO_CHOICES: tuple[str, ...] = ("best", "m4a", "mp3", "wav")

AUDIO_LABELS: dict[str, str] = {
    "best": "Original · best quality",
    "m4a": "M4A",
    "mp3": "MP3",
    "wav": "WAV",
}

#: Formats that require FFmpeg to be available.
AUDIO_NEEDS_FFMPEG = frozenset({"mp3", "wav"})


@dataclass
class VideoStream:
    """The best stream we found for one height."""

    height: int
    format_id: str
    ext: str
    fps: int
    size: float
    has_audio: bool


def _stream_size(fmt: dict) -> float:
    size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
    try:
        return float(size)
    except (TypeError, ValueError):
        return 0.0


def video_streams(meta: dict) -> list[VideoStream]:
    """One entry per distinct video height, best file size first within it."""
    formats = meta.get("formats") or []
    best: dict[int, VideoStream] = {}
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        height = fmt.get("height")
        vcodec = fmt.get("vcodec")
        if not height or not vcodec or vcodec == "none":
            continue
        size = _stream_size(fmt)
        current = best.get(int(height))
        if current is None or size > current.size:
            best[int(height)] = VideoStream(
                height=int(height),
                format_id=str(fmt.get("format_id") or ""),
                ext=str(fmt.get("ext") or "?"),
                fps=int(fmt.get("fps") or 0),
                size=size,
                has_audio=bool(
                    fmt.get("acodec") and fmt.get("acodec") != "none"
                ),
            )
    return [best[h] for h in sorted(best, reverse=True)]


def _best_audio_size(meta: dict) -> float:
    """Size of the largest audio-only stream, for size estimates."""
    formats = meta.get("formats") or []
    best = 0.0
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        if not acodec or acodec == "none":
            continue
        if vcodec and vcodec != "none":
            continue  # audio-only streams
        best = max(best, _stream_size(fmt))
    return best


def available_qualities(meta: dict) -> list[dict]:
    """The video quality picker, best first.

    Each entry reports whether that resolution is actually present. A height
    the page does not offer is still listed (greyed out by the popup) so the
    user can see it is the video, not the app, that is missing it.
    """
    streams = video_streams(meta)
    if not streams:
        return []
    max_height = max(stream.height for stream in streams)
    by_height = {stream.height: stream for stream in streams}
    audio_size = _best_audio_size(meta)

    entries: list[dict] = [
        {
            "quality": "best",
            "label": "Best available",
            "available": True,
            "height": max_height,
            "ext": by_height[max_height].ext,
            "fps": by_height[max_height].fps,
            "size": by_height[max_height].size + audio_size,
            "note": f"up to {max_height}p",
        }
    ]
    for quality in QUALITY_ORDER:
        if quality not in engine.QUALITY_SELECTORS:
            continue
        height = int(quality)
        # The engine's selector is a ceiling ("bestvideo[height<=H]"), so a
        # height the page does not carry is still satisfiable by the best
        # stream below it. We say so explicitly instead of hiding the option.
        available = height <= max_height
        estimate = 0.0
        ext = ""
        fps = 0
        note = ""
        if available:
            pick = None
            for stream in streams:          # streams are height-descending
                if stream.height <= height:
                    pick = stream
                    break
            if pick is None:
                pick = streams[-1]
            estimate = pick.size + audio_size
            ext = pick.ext
            fps = pick.fps
            if pick.height < height:
                note = f"falls back to {pick.height}p on this video"
        else:
            note = "not offered by this video"
        entries.append(
            {
                "quality": quality,
                "label": QUALITY_LABELS.get(quality, f"{quality}p"),
                "available": available,
                "height": height,
                "ext": ext,
                "fps": fps,
                "size": estimate,
                "note": note,
            }
        )
    return entries


def available_audio_formats(meta: dict) -> list[dict]:
    """The audio format picker, filtered by what the system can actually do."""
    streams = meta.get("formats") or []
    has_audio_stream = any(
        isinstance(fmt, dict)
        and fmt.get("acodec")
        and fmt.get("acodec") != "none"
        for fmt in streams
    )
    ffmpeg_present = engine.find_ffmpeg() is not None

    entries: list[dict] = []
    for fmt in AUDIO_CHOICES:
        if not has_audio_stream:
            available = False
            note = "no audio stream found"
        elif fmt in AUDIO_NEEDS_FFMPEG and not ffmpeg_present:
            available = False
            note = "requires FFmpeg (not found)"
        else:
            available = True
            note = ""
        entries.append(
            {
                "format": fmt,
                "label": AUDIO_LABELS.get(fmt, fmt.upper()),
                "available": available,
                "note": note,
            }
        )
    return entries


def video_info(meta: dict) -> dict:
    """The metadata block the popup renders."""
    return {
        "title": str(meta.get("title") or ""),
        "channel": str(meta.get("uploader") or meta.get("channel") or ""),
        "channel_id": str(meta.get("channel_id") or ""),
        "duration": int(meta.get("duration") or 0),
        "view_count": int(meta.get("view_count") or 0),
        "upload_date": str(meta.get("upload_date") or ""),
        "video_id": str(meta.get("id") or ""),
        "extractor": str(meta.get("extractor_key") or ""),
        "thumbnail": str(meta.get("thumbnail") or ""),
        "is_playlist": bool(meta.get("_type") == "playlist"),
        "qualities": available_qualities(meta),
        "audio_formats": available_audio_formats(meta),
    }
