# ---------------------------------------------------------------------------
# Downloads the third-party console binaries the downloader bundles under bin/.
#
# Layout after a successful run (engine.py looks for exactly these paths):
#   bin/yt-dlp.exe
#   bin/deno.exe
#   bin/ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe
#   bin/ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe
#
# Usage:  pwsh -File tools/fetch-binaries.ps1
# ---------------------------------------------------------------------------
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# Git Bash / curl work too; this script only needs PowerShell 5.1+.
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Bin  = Join-Path $Root "bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null

function Save-File {
    param([string]$Url, [string]$Destination)
    Write-Host "→ $Url" -ForegroundColor DarkGray
    Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
    Write-Host "  saved: $Destination" -ForegroundColor Green
}

# --- yt-dlp -----------------------------------------------------------------
# The engine reports this version on the boot screen.
$ytdlp = Join-Path $Bin "yt-dlp.exe"
if (-not (Test-Path $ytdlp)) {
    Save-File "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" $ytdlp
} else { Write-Host "✓ yt-dlp.exe already present" -ForegroundColor DarkGray }

# --- deno -------------------------------------------------------------------
# Optional JS runtime; yt-dlp needs it for a handful of sites. ~97 MB.
$deno = Join-Path $Bin "deno.exe"
if (-not (Test-Path $deno)) {
    $tmp = Join-Path $Bin "deno.zip"
    Save-File "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" $tmp
    Expand-Archive -Path $tmp -DestinationPath $Bin -Force
    Remove-Item $tmp
} else { Write-Host "✓ deno.exe already present" -ForegroundColor DarkGray }

# --- ffmpeg -----------------------------------------------------------------
# Only ffmpeg.exe and ffprobe.exe are used; ffplay is a player and is skipped.
# The directory name is hard-coded in engine.py — keep it as-is.
$ffdir = Join-Path $Bin "ffmpeg-master-latest-win64-gpl"
if (-not (Test-Path (Join-Path $ffdir "bin/ffmpeg.exe"))) {
    $tmp = Join-Path $Bin "ffmpeg.zip"
    Save-File "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" $tmp
    Expand-Archive -Path $tmp -DestinationPath $Bin -Force
    Remove-Item $tmp
} else { Write-Host "✓ ffmpeg already present" -ForegroundColor DarkGray }

Write-Host "`nbin/ is ready." -ForegroundColor Cyan
