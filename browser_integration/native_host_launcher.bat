@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "YTDM_ROOT=%~dp0"
set "YTDM_ROOT=%YTDM_ROOT:~0,-1%"
for %%I in ("%YTDM_ROOT%") do set "YTDM_ROOT=%%~dpI"
set "YTDM_ROOT=%YTDM_ROOT:~0,-1%"
cd /d "%YTDM_ROOT%"
"py" -I "%~dp0native_host.py" %*
