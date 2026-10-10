"""Hacker-terminal theme: ASCII art, colour palettes and shared style tokens.

The interface reads colours through the module-level `current` theme object so
the Settings screen can switch palettes on the fly.
"""
from __future__ import annotations

from rich.text import Text

APP_NAME = "YT-DL"
APP_TAG = "CYBER TERMINAL"
APP_VERSION = "1.0.0"
PROMPT = "root@yt-downloader:~$"

# Banner art: "YT" + "DL" in the classic phosphor style.
LOGO = [
    "██╗   ██╗████████╗    ██████╗ ██╗      ███████╗",
    "╚██╗ ██╔╝╚══██╔══╝    ██╔══██╗██║      ██╔════╝",
    " ╚████╔╝    ██║       ██║  ██║██║      █████╗  ",
    "  ╚██╔╝     ██║       ██║  ██║██║      ██╔══╝  ",
    "   ██║      ██║       ██████╔╝███████╗ ███████╗",
    "   ╚═╝      ╚═╝       ╚═════╝ ╚══════╝ ╚══════╝",
]

# Each palette: the primary phosphor colour, a secondary accent, the colour used
# for panel borders, and the dim tone for labels.
PALETTES: dict[str, dict[str, str]] = {
    "hacker": {
        "accent": "#00ff66",
        "accent2": "#00e5ff",
        "border": "#1e5b35",
        "label": "#5f9c73",
        "title": "HACKER GREEN",
    },
    "matrix": {
        "accent": "#00ff00",
        "accent2": "#7cff5e",
        "border": "#1f7a1f",
        "label": "#5f9e5f",
        "title": "MATRIX RAIN",
    },
    "cyberpunk": {
        "accent": "#ff2bd6",
        "accent2": "#00e5ff",
        "border": "#7a1f66",
        "label": "#b06ba0",
        "title": "NEON DRIVE",
    },
    "crimson": {
        "accent": "#ff2e4e",
        "accent2": "#ffb000",
        "border": "#7a1f2e",
        "label": "#c06570",
        "title": "CRIMSON ALERT",
    },
    "arctic": {
        "accent": "#00d4ff",
        "accent2": "#8ea9ff",
        "border": "#1f547a",
        "label": "#5f8fb0",
        "title": "ARCTIC SIGNAL",
    },
}

DEFAULT_THEME = "hacker"


class Theme:
    """The active palette plus the style strings the widgets use."""

    def __init__(self, name: str = DEFAULT_THEME) -> None:
        self.apply(name)

    def apply(self, name: str) -> None:
        self.name = name if name in PALETTES else DEFAULT_THEME
        palette = PALETTES[self.name]
        self.accent = palette["accent"]
        self.accent2 = palette["accent2"]
        self.border = palette["border"]
        self.label = palette["label"]
        self.title = palette["title"]

    # -- style helpers ----------------------------------------------------
    def accent_style(self, bold: bool = True) -> str:
        return f"{'bold ' if bold else ''}{self.accent}"

    def accent2_style(self, bold: bool = True) -> str:
        return f"{'bold ' if bold else ''}{self.accent2}"

    # -- art --------------------------------------------------------------
    def logo_text(self) -> Text:
        """The banner logo plus its subtitle, centred."""
        # Pad every line to the same width first, otherwise `justify` centres
        # each row independently and the block looks crooked.
        width = max(len(line) for line in LOGO)
        text = Text(justify="center")
        for line in LOGO:
            text.append(line.ljust(width) + "\n", style=f"bold {self.accent}")
        text.append("\n")
        text.append(f"{APP_NAME} DOWNLOADER", style=f"bold {self.accent2}")
        text.append("   ───   ", style="dim")
        text.append(APP_TAG, style="bold white")
        return text

    def badge_bar(self, left: str, middle: str, right: str) -> Text:
        """The `[ SYSTEM ONLINE ] ... [ v1.0.0 ]` strip, centred."""
        text = Text(justify="center")
        text.append("[ ", style=self.accent)
        text.append(left, style=f"bold {self.accent}")
        text.append(" ]", style=self.accent)
        text.append("       ", style="dim")
        text.append("[ ", style=self.accent2)
        text.append(middle, style=f"bold {self.accent2}")
        text.append(" ]", style=self.accent2)
        text.append("       ", style="dim")
        text.append("[ ", style=self.label)
        text.append(right, style=f"bold {self.label}")
        text.append(" ]", style=self.label)
        return text


current = Theme()


def apply(name: str) -> None:
    """Switch the global theme to one of the palettes in PALETTES."""
    current.apply(name)
