# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the YT-DL Cyber Terminal.

One-directory layout (mode "onedir"): the result is a self-contained
`YT-DL-CyberTerminal/` folder that runs on any Windows machine without Python.
The bundled yt-dlp / ffmpeg / deno binaries travel along in bin/.

Build with:
    py -m PyInstaller YT-DL-CyberTerminal.spec --noconfirm
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

BLOCK_CIPHER = None

ROOT = Path(SPECPATH).resolve()

# Everything the app ships with: the console binaries and the ffmpeg build.
# ffmpeg.zip (191 MB) is deliberately left out — it is only the source archive
# of the bundled build and the app never reads it.
_BIN = ROOT / "bin"
_FFMPEG_BIN = _BIN / "ffmpeg-master-latest-win64-gpl" / "bin"

datas = [
    # The yt-dlp / deno executables, found by engine.find_ytdlp().
    str(_BIN / "yt-dlp.exe"),
    str(_BIN / "deno.exe"),
]
# Only the ffmpeg binaries themselves, not the doc/ and presets/ trees.
# ffplay is a standalone *player* — yt-dlp only ever spawns ffmpeg (merge) and
# ffprobe (probe), so shipping it would add ~170 MB of dead weight.
for _name in ("ffmpeg.exe", "ffprobe.exe"):
    _path = _FFMPEG_BIN / _name
    if _path.is_file():
        datas.append(str(_path))

# In a frozen build engine.py resolves `bin/` beside its package root, so the
# destination folders must mirror the source layout exactly.
datas = [
    (
        str(p),
        "bin" if p.parent == _BIN else "bin/ffmpeg-master-latest-win64-gpl/bin",
    )
    for p in (Path(x) for x in datas)
]

# Rich ships no compiled data, but this keeps the spec future-proof.
datas += collect_data_files("rich", include_py_files=False)
datas.append((str(ROOT / "browser_integration" / "installer" / "install_host.bat"), "."))

hiddenimports = collect_submodules("rich") + [
    "ytdm",
    "ytdm.cli",
    "ytdm.engine",
    "ytdm.ui",
    "ytdm.tui",
    "ytdm.tui.menu",
    "ytdm.tui.screens",
    "ytdm.tui.interface",
    "ytdm.tui.theme",
    "ytdm.tui.progress",
    "ytdm.config.settings",
    "ytdm.history.store",
    "ytdm.utils.helpers",
    "ytdm.utils.logger",
    "browser_integration",
    "browser_integration.native_host",
    "browser_integration.jobs",
    "browser_integration.protocol",
    "browser_integration.formats",
    "browser_integration.download_service",
    "browser_integration.supervisor",
]

# winreg is imported lazily by the engine for proxy detection; make sure the
# frozen build can still resolve it.
hiddenimports += ["winreg"]


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Test/unused stdlib bulk that would otherwise inflate the build.
        "test",
        "unittest",
        "pydoc",
        "doctest",
        "xml",
        "email",
        "http",
        "urllib",
        "ssl",
        "pdb",
        "profile",
        "pstats",
        # rich -> pygments pulls in the image formatter, which in turn drags
        # in Pillow and numpy (~40 MB). The terminal only ever renders with
        # the terminal256/terminal formatters, so this whole subtree is dead.
        "pygments.formatters.img",
        "PIL",
        "numpy",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=BLOCK_CIPHER,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=BLOCK_CIPHER)


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YT-DL-CyberTerminal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "icon.ico") if (ROOT / "icon.ico").is_file() else None,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="YT-DL-CyberTerminal",
)
