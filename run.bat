@echo off
REM Quick launcher for the download manager.
REM Usage:  run.bat <URL> [options]
REM Example: run.bat https://www.youtube.com/watch?v=... -q 720
setlocal
set SCRIPT_DIR=%~dp0
py "%SCRIPT_DIR%main.py" %*
endlocal
