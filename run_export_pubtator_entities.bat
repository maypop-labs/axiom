@echo off
REM AXIOM - Export PubTator Entities
REM Activates the project's Python virtual environment and runs the
REM export_pubtator_entities.py utility, which queries axiom.db for
REM PubTator-tagged Gene entities, aggregates them by NCBI Gene ID, and
REM writes a timestamped .xlsx file to Python\export\.

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

python utils\export_pubtator_entities.py %*
set EXIT_CODE=%errorlevel%

pause
exit /b %EXIT_CODE%
