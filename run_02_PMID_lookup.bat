@echo off
REM AXIOM - Stage 02: PubMed Lookup and Per-Paper Enrichment
REM Activates the project venv and runs 02_PMID_lookup.py.

cd /d "%~dp0Python"

if not exist "venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found at venv\Scripts\activate.bat
    echo Run setup.bat first to create it.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
python 02_PMID_lookup.py
pause
