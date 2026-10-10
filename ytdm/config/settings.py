"""Application settings persisted as JSON in the project's data directory.

Only user-facing preferences live here; anything that changes how a download
runs is still owned by the download engine.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..utils.helpers import runtime_data_dir

DATA_DIR = runtime_data_dir("data")
CONFIG_FILE = DATA_DIR / "config.json"

QUALITIES = ["best", "2160", "1440", "1080", "720", "480", "360", "240", "144"]
AUDIO_FORMATS = ["mp3", "m4a", "opus", "wav", "aac", "flac", "vorbis"]
THEMES = ["hacker", "matrix", "cyberpunk", "crimson", "arctic"]


@dataclass
class AppSettings:
    """User preferences for the terminal application."""

    download_dir: str = "downloads"
    quality: str = "best"
    audio_format: str = "mp3"
    theme: str = "hacker"
    ffmpeg_path: str = ""
    proxy: str = ""
    threads: int = 4
    rate_limit: str = ""

    @classmethod
    def load(cls) -> "AppSettings":
        """Read the config file, falling back to the bundled defaults."""
        try:
            raw = CONFIG_FILE.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        settings = cls()
        for field in settings.__dataclass_fields__:
            if field in data:
                setattr(settings, field, data[field])
        return settings

    def save(self) -> None:
        """Write the settings back to disk."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def resolved_download_dir(self) -> Path:
        """The download directory as an absolute path."""
        path = Path(self.download_dir or "downloads")
        if not path.is_absolute():
            path = runtime_data_dir(path.as_posix())
        return path

    def proxy_or_none(self) -> str | None:
        value = (self.proxy or "").strip()
        return value or None

    def rate_limit_or_none(self) -> str | None:
        value = (self.rate_limit or "").strip()
        return value or None

    def ffmpeg_dir_or_none(self) -> str | None:
        value = (self.ffmpeg_path or "").strip()
        if not value:
            return None
        path = Path(value)
        return str(path if path.is_dir() else path.parent)
