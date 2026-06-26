@echo off
REM AXIOM - Run Stage 04: Graph Export (DrugBank-redacted public set)
REM Activates the project's Python virtual environment and runs
REM 04_graph_export.py with --redact-drugbank, producing license-safe
REM artifacts for public distribution. DrugBank-derived content is stripped
REM from graph.json, graph.graphml, and the tsv set, and a
REM drugbank_redaction_report.txt is written alongside them. The curated
REM graph DB is never modified.
REM
REM Output goes to export_public (NOT export) to avoid clobbering the
REM full-fidelity local export that run_view_graph.bat serves. Change the
REM --output-dir below if you want it elsewhere.
REM
REM Any extra arguments are forwarded to the script (e.g. --min-coverage,
REM --year-min, --node-type).
REM
REM Examples:
REM   run_04_graph_export_redacted.bat
REM   run_04_graph_export_redacted.bat --min-coverage 2 --year-min 2020

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

python 04_graph_export.py --format all --output-dir export_public --redact-drugbank %*
set EXIT_CODE=%errorlevel%

pause
exit /b %EXIT_CODE%
