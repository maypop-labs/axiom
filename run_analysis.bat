@echo off
REM AXIOM - Run Network Control Analysis (all five passes)
REM Activates the project's Python virtual environment once and runs the
REM analyses in order:
REM   [1/5] signed_path_net_effect.py    -> signed_path_net_effect.tsv
REM   [2/5] cycle_analysis.py            -> cycle_analysis.tsv
REM   [3/5] target_control.py            -> target_control.tsv
REM   [4/5] feedback_control_targets.py  -> feedback_control_targets.tsv
REM   [5/5] build_report.py              -> build_report.json (+ dated archive)
REM
REM signed_path runs first because target_control and feedback_control_targets
REM both join its TSV for the directional annotation. cycle_analysis is the
REM gating diagnostic for
REM whether the attractor-family methods (FVS / stable-motif / attractor)
REM apply to the graph at all. build_report runs last because it consumes the
REM TSVs the earlier passes produce and joins the graph database plus the
REM public PubMed and Open Targets APIs. Configuration for each pass lives in
REM the constants at the top of its own script. Outputs are written into
REM   E:\bin\axiom\Python\export\
REM
REM A non-zero exit from any pass stops the run. To run a single pass in
REM isolation, invoke it directly, e.g.  python target_control.py

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

echo ==============================================================================
echo [1/5] Signed-path net-effect analysis
echo ==============================================================================
python signed_path_net_effect.py
set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo.
    echo ERROR: signed_path_net_effect.py exited with code %EXIT_CODE%. Stopping.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo ==============================================================================
echo [2/5] Cycle / feedback-structure census
echo ==============================================================================
python cycle_analysis.py
set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo.
    echo ERROR: cycle_analysis.py exited with code %EXIT_CODE%. Stopping.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo ==============================================================================
echo [3/5] Structural target-control pass
echo ==============================================================================
python target_control.py
set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo.
    echo ERROR: target_control.py exited with code %EXIT_CODE%. Stopping.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo ==============================================================================
echo [4/5] Feedback-control target intersection
echo ==============================================================================
python feedback_control_targets.py
set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo.
    echo ERROR: feedback_control_targets.py exited with code %EXIT_CODE%. Stopping.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo ==============================================================================
echo [5/5] Build report (build_report.json + dated archive)
echo ==============================================================================
python build_report.py
set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo.
    echo ERROR: build_report.py exited with code %EXIT_CODE%. Stopping.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo All five passes completed.
pause
exit /b %EXIT_CODE%
