#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Downloads the third-party console binaries the downloader bundles under bin/.
# Bash twin of fetch-binaries.ps1 — for Git Bash / WSL / Linux.
#
# Usage:  bash tools/fetch-binaries.sh
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/bin"
mkdir -p "$BIN"

save() {
    local url="$1" dest="$2"
    echo "→ $url"
    curl -fL --retry 3 -o "$dest" "$url"
    echo "  saved: $dest"
}

# --- yt-dlp -----------------------------------------------------------------
if [[ ! -f "$BIN/yt-dlp.exe" ]]; then
    save "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" "$BIN/yt-dlp.exe"
else echo "✓ yt-dlp.exe already present"; fi

# --- deno (optional JS runtime) ---------------------------------------------
if [[ ! -f "$BIN/deno.exe" ]]; then
    save "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" "$BIN/deno.zip"
    unzip -o -q "$BIN/deno.zip" -d "$BIN"
    rm -f "$BIN/deno.zip"
else echo "✓ deno.exe already present"; fi

# --- ffmpeg -----------------------------------------------------------------
# The directory name is hard-coded in engine.py — keep it as-is.
if [[ ! -f "$BIN/ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe" ]]; then
    save "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" "$BIN/ffmpeg.zip"
    unzip -o -q "$BIN/ffmpeg.zip" -d "$BIN"
    rm -f "$BIN/ffmpeg.zip"
else echo "✓ ffmpeg already present"; fi

echo
echo "bin/ is ready."
