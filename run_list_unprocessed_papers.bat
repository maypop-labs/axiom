@echo off
REM AXIOM - List Unprocessed Papers
REM Activates the project's Python virtual environment and runs
REM list_unprocessed_papers.py, writing a markdown checklist of corpus
REM papers that are indexed and chunked but have never contributed to the
REM curated graph (no edge evidence or node observation references them) to:
REM   E:\bin\axiom\Python\export_public\unprocessed_papers.md
REM
REM Any extra arguments passed to this batch file are forwarded to the
REM script (e.g. --sort title to list alphabetically instead of by year).

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

python list_unprocessed_papers.py --output "%~dp0Python\export_public\unprocessed_papers.md" %*
set EXIT_CODE=%errorlevel%

pause
exit /b %EXIT_CODE%
