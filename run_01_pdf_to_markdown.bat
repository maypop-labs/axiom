@echo off
REM AXIOM - Run Stage 01: PDF to Markdown
REM Activates the project's Python virtual environment and runs the
REM 01_pdf_to_markdown.py conversion script.

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

python 01_pdf_to_markdown.py
set EXIT_CODE=%errorlevel%

pause
exit /b %EXIT_CODE%
