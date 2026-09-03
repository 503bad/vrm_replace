@echo off
rem vrm_replace GUI launcher. Double-click to start gui.py without a console window.
rem Order: py launcher (pyw -3) -> pythonw on PATH -> python on PATH
rem (ASCII only: cmd.exe parses this file with the system code page)
cd /d "%~dp0"
where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" pyw -3 gui.py
    goto :eof
)
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw gui.py
    goto :eof
)
where python >nul 2>&1
if %errorlevel%==0 (
    start "" python gui.py
    goto :eof
)
echo Python not found. Install Python 3.12 from https://www.python.org/ and add it to PATH.
pause
