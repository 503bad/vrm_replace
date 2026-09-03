@echo off
rem ============================================================================
rem  vrm_replace launcher (Windows). Double-click this file.
rem    1. checks Python / Node.js / NVIDIA driver and tells you what to install
rem    2. runs setup_env.py (installs pip/npm deps, downloads models; fast when done)
rem    3. starts the GUI
rem  ASCII only: cmd.exe parses this file with the system code page.
rem ============================================================================
setlocal
cd /d "%~dp0"
title vrm_replace
echo === vrm_replace ===

rem ---- Python (3.10+; py launcher preferred) ----------------------------------
set "PY="
set "PYW="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    set "PYW=pyw -3"
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PY=python"
        set "PYW=pythonw"
    )
)
if not defined PY (
    echo.
    echo [NG] Python 3.10 or newer was not found.
    echo      Install Python 3.12 from  https://www.python.org/downloads/windows/
    echo      In the installer, check "Add python.exe to PATH". Then run this file again.
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('%PY% -c "import sys; print(sys.version.split()[0])"') do echo [OK] Python %%v

rem ---- Node.js ----------------------------------------------------------------
where node >nul 2>&1
if errorlevel 1 (
    echo.
    echo [NG] Node.js was not found.
    echo      Install the LTS version from  https://nodejs.org/  and run this file again.
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('node --version') do echo [OK] Node.js %%v

rem ---- NVIDIA driver (warning only) -------------------------------------------
where nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo [!!] nvidia-smi not found. An NVIDIA GPU and driver are required for SAM2 / ProPainter.
    echo      https://www.nvidia.com/Download/index.aspx
) else (
    echo [OK] NVIDIA driver
)

rem ---- git (optional) ---------------------------------------------------------
where git >nul 2>&1
if errorlevel 1 echo [..] git not found - SAM2 / ProPainter will be downloaded as zip instead.

rem ---- setup (idempotent) -----------------------------------------------------
echo.
%PY% setup_env.py
if errorlevel 1 (
    echo.
    echo [NG] Setup failed. See logs\setup.log, fix the problem, then run this file again.
    echo.
    pause
    exit /b 1
)

rem ---- GUI --------------------------------------------------------------------
start "" %PYW% gui.py
exit /b 0
