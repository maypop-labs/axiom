@echo off
REM AXIOM - Run Feedback-Control Target Analysis
REM Activates the project's Python virtual environment and runs
REM feedback_control_targets.py, which reads axiom_graph.db and finds the
REM intervention targets that both break the amplifying feedback and have a
REM clean directional call toward slowing aging. It computes the exact
REM minimum feedback vertex set over the core (scipy.optimize.milp), the
REM minimum hitting set over positive cycles, per-node positive-cycle
REM participation, and joins the signed-path net-effect verdicts. Run
REM signed_path_net_effect.py first so the directional join is available.
REM Output is written to:
REM   E:\bin\axiom\Python\export\feedback_control_targets.tsv
REM
REM Any extra arguments passed to this batch file are forwarded to the
REM script.

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

python feedback_control_targets.py %*
set EXIT_CODE=%errorlevel%

pause
exit /b %EXIT_CODE%
