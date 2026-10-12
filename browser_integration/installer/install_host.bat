@echo off
rem Register the YT-DL native messaging host for the current Windows user.
setlocal enableextensions

set "HOST_NAME=app.ytdm.nativehost"
set "REG_KEY=HKCU\Software\Google\Chrome\NativeMessagingHosts\%HOST_NAME%"
set "EXT_ID=pgdmapapgkdngahokfjaodmmidffceoh"

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
if exist "%SCRIPT_DIR%\YT-DL-CyberTerminal.exe" (
    set "ROOT=%SCRIPT_DIR%"
    set "PACKAGED=1"
    set "LAUNCHER=%SCRIPT_DIR%\YT-DL-CyberTerminal.exe"
) else (
    for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
    set "PACKAGED=0"
    set "LAUNCHER=%ROOT%\browser_integration\native_host_launcher.bat"
)

set "MANIFEST_DIR=%ROOT%\data"
set "MANIFEST=%MANIFEST_DIR%\native_host_manifest.json"

if "%PACKAGED%"=="0" (
    set "PYEXE="
    for %%C in (py python python3) do if not defined PYEXE where %%C >nul 2>&1 && set "PYEXE=%%C"
    if not defined PYEXE (
        echo ERROR: Python was not found. Install Python or use the packaged app.
        exit /b 1
    )
    (
        echo @echo off
        echo setlocal
        echo set "PYTHONIOENCODING=utf-8"
        echo set "PYTHONUNBUFFERED=1"
        echo set "YTDM_ROOT=%%~dp0"
        echo set "YTDM_ROOT=%%YTDM_ROOT:~0,-1%%"
        echo for %%%%I in ^("%%YTDM_ROOT%%"^) do set "YTDM_ROOT=%%%%~dpI"
        echo set "YTDM_ROOT=%%YTDM_ROOT:~0,-1%%"
        echo cd /d "%%YTDM_ROOT%%"
        echo "%PYEXE%" -I "%%~dp0native_host.py" %%*
    ) > "%LAUNCHER%"
    if errorlevel 1 (
        echo ERROR: Could not write the source host launcher.
        exit /b 1
    )
)

if not exist "%MANIFEST_DIR%" mkdir "%MANIFEST_DIR%" >nul 2>&1
set "LAUNCHER_ESC=%LAUNCHER:\=\\%"
(
    echo {
    echo   "name": "%HOST_NAME%",
    echo   "description": "Native messaging host for the YT-DL desktop downloader",
    echo   "path": "%LAUNCHER_ESC%",
    echo   "type": "stdio",
    echo   "allowed_origins": ["chrome-extension://%EXT_ID%/"]
    echo }
) > "%MANIFEST%"
if errorlevel 1 (
    echo ERROR: Could not write "%MANIFEST%".
    exit /b 1
)

reg add "%REG_KEY%" /ve /d "%MANIFEST%" /f >nul 2>&1
if errorlevel 1 (
    echo ERROR: Could not register the host in the current user's registry.
    exit /b 1
)

echo Registered %HOST_NAME% for extension %EXT_ID%.
echo Verifying native host...
if "%PACKAGED%"=="1" (
    "%LAUNCHER%" --selftest 1>nul 2>&1
) else (
    cd /d "%ROOT%"
    "%PYEXE%" -I browser_integration\native_host.py --selftest 1>nul 2>&1
)
if errorlevel 1 (
    echo Host self-test failed. Check data\logs\app.log.
    exit /b 1
)

echo Host self-test passed. Reload the extension in chrome://extensions.
endlocal
