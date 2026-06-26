@echo off
REM AXIOM - Run Stage 03: Chunk and Embed
REM Activates the project's Python virtual environment and runs the
REM 03_chunk_and_embed.py pipeline. Any arguments passed to this batch
REM file are forwarded to the script (e.g. --force, --dry-run, --limit N).

cd /d "%~dp0Python"

if not exist "venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found at:
    echo   %CD%\venv
    echo.
    echo Run setup.bat first to create the environment.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment.
    pause
    exit /b 1
)

python 03_chunk_and_embed.py %*
set EXIT_CODE=%errorlevel%

pause
exit /b %EXIT_CODE%
