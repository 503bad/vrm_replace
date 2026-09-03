@echo off
rem vrm_replace GUI ランチャー。ダブルクリックで gui.py を起動する（コンソール窓は出さない）
rem 優先順: py ランチャー (pyw -3) → PATH の pythonw → PATH の python
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
echo Python が見つかりません。https://www.python.org/ から Python 3.12 を入れて PATH を通してください。
pause
