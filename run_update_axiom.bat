@echo off
REM AXIOM - Run Stages 01-03 in sequence
REM Activates the project's Python virtual environment and runs:
REM   01_pdf_to_markdown.py
REM   02_PMID_lookup.py
REM   03_chunk_and_embed.py
REM Stops on the first non-zero exit code.

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

echo.
echo === Stage 01: PDF to Markdown ===
python 01_pdf_to_markdown.py
set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo ERROR: Stage 01 failed with exit code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo === Stage 02: PubMed Lookup ===
python 02_PMID_lookup.py
set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo ERROR: Stage 02 failed with exit code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo === Stage 03: Chunk and Embed ===
python 03_chunk_and_embed.py
set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo ERROR: Stage 03 failed with exit code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo === All stages complete ===
pause
exit /b 0
