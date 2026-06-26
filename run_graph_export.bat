@echo off
REM AXIOM - Run Stage 04: Graph Export
REM Activates the project's Python virtual environment and runs the
REM 04_graph_export.py script. Any arguments passed to this batch file
REM are forwarded to the script (e.g. --format, --output-dir,
REM --min-coverage, --year-min).
REM
REM Examples:
REM   run_04_graph_export.bat --format cytoscape-js --output-dir export
REM   run_04_graph_export.bat --format all --output-dir export
REM   run_04_graph_export.bat --format graphml --output graph.graphml

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

python 04_graph_export.py --format all --output-dir export %*
set EXIT_CODE=%errorlevel%

pause
exit /b %EXIT_CODE%
