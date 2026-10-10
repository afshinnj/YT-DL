"""Rich console setup and the reusable hacker-terminal widgets.

Everything the screens draw goes through this module so the look stays
consistent. Console configuration (ANSI/UTF-8 on Windows) happens here too.
"""
from __future__ import annotations

import os
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from . import theme

# --- Windows console preparation ------------------------------------------
# `os.system("")` asks cmd.exe to enable VT-100 escape processing, and
# reconfiguring the streams to UTF-8 keeps titles/description (often Persian)
# from turning into question marks.
if sys.platform == "win32":
    try:
        os.system("")
    except Exception:  # pragma: no cover - best effort only
        pass
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # pragma: no cover - best effort only
            pass

console = Console(highlight=False)


def _bracket(title: str | None) -> str | None:
    """Wrap a panel title as `┤ TITLE ├` (the classic console look)."""
    if not title:
        return None
    return f"[bold {theme.current.accent}]┤ {title} ├[/]"


class UI:
    """Thin facade that knows the cyber-terminal styling."""

    # --- basic output ----------------------------------------------------
    def clear(self) -> None:
        console.clear()

    def line(self, text: str | Text = "") -> None:
        console.print(text)

    def blank(self) -> None:
        console.print()

    def rule(self, title: str = "") -> None:
        label = Text(f" {title} ", style=f"bold {theme.current.accent}") if title else Text("")
        console.rule(label, characters="─", style=theme.current.border)

    def print(self, *args, **kwargs) -> None:
        console.print(*args, **kwargs)

    # --- panels ----------------------------------------------------------
    def panel(
        self,
        title: str | None,
        body,
        *,
        border: str | None = None,
        box_style=box.ROUNDED,
        padding: tuple[int, int] = (0, 2),
        expand: bool = True,
    ) -> Panel:
        if isinstance(body, str):
            body = Text(body)
        return Panel(
            body,
            title=_bracket(title),
            title_align="left",
            border_style=border or theme.current.border,
            box=box_style,
            padding=padding,
            expand=expand,
        )

    def boxed(self, title: str | None, body, **kwargs) -> None:
        console.print(self.panel(title, body, **kwargs))

    # --- menus -----------------------------------------------------------
    def menu(self, title: str, items: list[tuple[str, str]], *, marker: str = "▶") -> None:
        """A numbered list inside a panel."""
        body = Text()
        for key, label in items:
            body.append("\n  ")
            body.append(f"[{key}]", style=f"bold {theme.current.accent}")
            body.append(f"  {marker} ", style=theme.current.accent2)
            body.append(label, style="bold #e8ffe8")
        body.append("\n")
        self.boxed(title, body)

    def menu_grid(
        self, title: str, items: list[tuple[str, str]], columns: int = 3
    ) -> None:
        """A compact multi-column menu."""
        grid = Table.grid(padding=(0, 3))
        for _ in range(columns):
            grid.add_column(style=theme.current.accent, no_wrap=True)
            grid.add_column(style="bold #e8ffe8", no_wrap=True)
        for start in range(0, len(items), columns):
            row: list[Text] = []
            chunk = items[start : start + columns]
            for key, label in chunk:
                cell = Text()
                cell.append(f"[{key}]", style=f"bold {theme.current.accent}")
                cell.append(f"  {label}", style="bold #e8ffe8")
                row.append(cell)
                row.append(Text(" ", style="dim"))
            while len(row) < columns * 2:
                row.append(Text(""))
            grid.add_row(*row)
        self.boxed(title, grid)

    # --- input -----------------------------------------------------------
    def ask(
        self,
        label: str,
        *,
        choices: list[str] | None = None,
        default: str | None = None,
        show_default: bool = True,
    ) -> str:
        prompt = f"[bold {theme.current.accent}]{label}[/] "
        return Prompt.ask(
            prompt,
            choices=choices,
            default=default,
            show_default=show_default,
            show_choices=False,
            console=console,
        )

    def ask_raw(self, label: str) -> str:
        """Prompt with no choices, no default marker — for free-text input."""
        prompt = f"[bold {theme.current.accent}]{label}[/] "
        return Prompt.ask(prompt, show_default=False, console=console)

    def ask_secret(self, label: str) -> str:
        prompt = f"[bold {theme.current.accent}]{label}[/] "
        return Prompt.ask(prompt, password=True, console=console)

    def confirm(self, label: str, *, default: bool = True) -> bool:
        prompt = f"[bold {theme.current.accent}]{label}[/]"
        return Confirm.ask(prompt, default=default, console=console)

    def pause(self, label: str = "Press ENTER to return to the main menu") -> None:
        try:
            console.input(f"\n[dim]▸ {label}[/] ")
        except (EOFError, KeyboardInterrupt):
            console.print()

    # --- status boxes ----------------------------------------------------
    def success(self, title: str, lines: list[str]) -> None:
        body = Text()
        for line in lines:
            body.append("✓ ", style=f"bold {theme.current.accent}")
            body.append(line + "\n", style="bold #e8ffe8")
        self.boxed(title, body, border=theme.current.accent)

    def failure(self, reason: str, code: str, hint: str = "") -> None:
        body = Text()
        body.append("✗  ", style="bold #ff3b4e")
        body.append("OPERATION FAILED\n\n", style="bold #ff3b4e")
        body.append("REASON\n", style="bold #ffd000")
        for part in reason.splitlines() or ["unknown error"]:
            body.append(f"  {part}\n", style="#e8ffe8")
        body.append("\nCODE: ", style="bold #ffd000")
        body.append(code, style="bold #ff3b4e")
        if hint:
            body.append("\n\n" + hint, style="dim")
        self.boxed("ERROR", body, border="#ff3b4e")

    def sysmsg(self, lines: list[str]) -> None:
        """The `> > TRANSFER COMPLETE` style system message box."""
        body = Text()
        for line in lines:
            body.append("> > ", style=f"bold {theme.current.accent2}")
            body.append(line + "\n", style="bold #e8ffe8")
        self.boxed("SYSTEM MESSAGE", body, border=theme.current.accent)


ui = UI()


def apply_ffmpeg_path(settings) -> None:
    """Expose a custom ffmpeg directory to child processes.

    The download engine looks for ffmpeg on PATH, so prepending the configured
    directory to this process' PATH is enough — the engine itself stays
    untouched.
    """
    directory = settings.ffmpeg_dir_or_none()
    if not directory:
        return
    path = os.environ.get("PATH", "")
    if directory not in path.split(os.pathsep):
        os.environ["PATH"] = directory + os.pathsep + path


__all__ = ("console", "ui", "apply_ffmpeg_path")
