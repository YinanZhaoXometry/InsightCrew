@echo off
rem Install dependencies and start the local web page. Needs uv; without an API key only the offline demo runs.
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found. Install it, then run this script again:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo Other ways to install: https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)

uv sync
if errorlevel 1 (
    pause
    exit /b 1
)
uv run insightcrew serve %*
