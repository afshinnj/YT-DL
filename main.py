#!/usr/bin/env python3
"""Entry point for the download manager.

Usage:
    python main.py                       # interactive hacker terminal (TUI)
    python main.py tui                   # same, explicit
    python main.py <URL>                 # download at best quality
    python main.py <URL> -q 720          # cap at 720p
    python main.py <URL> -a              # audio only (mp3)
    python main.py info <URL>            # show metadata
    python main.py formats <URL>         # list streams
    python main.py update                # update yt-dlp
"""
import sys
from pathlib import Path

if sys.platform == "win32":
    # Keep the console in UTF-8 so Persian titles and box-drawing characters
    # survive even under the classic cmd.exe host.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # pragma: no cover - best effort only
            pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The packaged executable also serves as Chrome's native messaging host.
# Chrome passes its extension origin as the first argument; supervisor and
# self-test modes are launched by the host installer and download service.
_native_args = sys.argv[1:]
if _native_args and (
    _native_args[0].startswith("chrome-extension://")
    or _native_args[0] in {"--supervisor", "--selftest"}
):
    from browser_integration.native_host import main as native_host_main  # noqa: E402

    sys.exit(native_host_main(_native_args))

from ytdm import cli  # noqa: E402


if __name__ == "__main__":
    sys.exit(cli.main())
