@echo off
REM AXIOM - Extract Graph Literature Reference List
REM Activates the project's Python virtual environment and runs
REM extract_graph_literature.py, saving the reference list of all corpus
REM literature that has grounded content in the graph to:
REM   E:\bin\axiom\exports\graph_references.txt
REM
REM Any extra arguments passed to this batch file are forwarded to the
REM script (e.g. --plain to suppress the per-source contribution counts).

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

if not exist "%~dp0exports" mkdir "%~dp0exports"

python extract_graph_literature.py --format md --plain --output "%~dp0Python\export_public\graph_references.md" %*
set EXIT_CODE=%errorlevel%

pause
exit /b %EXIT_CODE%
