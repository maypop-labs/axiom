@echo off
REM AXIOM - View Graph
REM Activates the project's Python virtual environment and runs the
REM view_graph.py wrapper. The wrapper re-exports the curated knowledge
REM graph in cytoscape-js format, starts a local HTTP server on a free
REM port, and opens a browser tab to the viewer.
REM
REM On F5 in the browser, the wrapper re-exports if axiom_graph.db has
REM changed since the last export. Press Ctrl+C in this window to stop.

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

python view_graph.py
set EXIT_CODE=%errorlevel%

if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
