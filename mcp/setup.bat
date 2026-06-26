@echo off
setlocal EnableDelayedExpansion
REM AXIOM MCP - Setup Script
REM Installs MCP server dependencies into the shared Python venv.
REM
REM Prerequisite: the shared venv must already exist at
REM   E:\bin\axiom\Python\venv
REM If it does not, run E:\bin\axiom\Python\setup.bat first.

echo ========================================
echo AXIOM MCP Setup
echo ========================================
echo.

set SCRIPT_DIR=%~dp0
set VENV_DIR=%SCRIPT_DIR%..\Python\venv

REM Verify the shared venv exists
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo ERROR: Shared Python virtual environment not found at:
    echo   %VENV_DIR%
    echo.
    echo Run E:\bin\axiom\Python\setup.bat first to create it.
    pause
    exit /b 1
)

REM Activate the shared venv
echo Activating shared Python venv...
call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment.
    pause
    exit /b 1
)
echo.

REM Install MCP requirements into the shared venv
echo Installing MCP requirements...
pip install -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 (
    echo ERROR: Failed to install MCP requirements.
    pause
    exit /b 1
)
echo.

REM Quick sanity check: confirm the mcp package is importable
echo Verifying mcp package import...
python -c "from mcp.server.fastmcp import FastMCP; print('  OK')"
if errorlevel 1 (
    echo ERROR: mcp package did not import cleanly.
    pause
    exit /b 1
)
echo.

echo ========================================
echo MCP setup completed successfully!
echo ========================================
echo.
echo Next steps:
echo   1. Smoke test: run E:\bin\axiom\mcp\run_server.bat
echo      You should see startup logs on stderr; Ctrl+C to exit.
echo   2. Configure Claude Desktop: see README.md for the
echo      claude_desktop_config.json snippet.
echo.
pause
