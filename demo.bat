@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo ==================================================
echo   InsightCrew  -  Fixed demo run
echo   Runs the default question, then builds the report.
echo ==================================================
echo.

call :pick_python
if not defined PYEXE (
    echo [ERROR] No usable Python found.
    echo Install Python 3.8+ from https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
echo Interpreter:
%PYEXE% -V
echo.

%PYEXE% run_demo.py
echo.
%PYEXE% -m ui.render_report
echo.
echo Report folder: %cd%\runs
echo Open report.html inside the newest run folder.
echo.
pause
exit /b 0

:pick_python
set "PYEXE="
for %%V in (3.13 3.12 3.11 3.10 3.9 3.8) do (
    if not defined PYEXE (
        py -%%V -c "import sys" >nul 2>&1 && set "PYEXE=py -%%V"
    )
)
if not defined PYEXE (
    python -c "import sys" >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
    py -3 -c "import sys" >nul 2>&1 && set "PYEXE=py -3"
)
if not defined PYEXE (
    python3 -c "import sys" >nul 2>&1 && set "PYEXE=python3"
)
goto :eof
