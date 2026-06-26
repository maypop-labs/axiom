@echo off
setlocal EnableDelayedExpansion
REM AXIOM Project - Python Environment Setup Script
REM This script creates a virtual environment and installs all required dependencies

echo ========================================
echo AXIOM Project Environment Setup
echo ========================================
echo.

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo Current directory: %CD%
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.10 or higher ^(required by marker-pdf^)
    pause
    exit /b 1
)

echo Python detected:
python --version
echo.

REM Create virtual environment if it doesn't exist
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
    echo Virtual environment created successfully
) else (
    echo Virtual environment already exists
)
echo.

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
)
echo.

REM Upgrade pip to latest version
echo Upgrading pip...
python -m pip install --upgrade pip
echo.

REM Remove any existing PyTorch packages from the venv before installing the
REM CUDA build. Without this step, pip sees a same-version CPU torch as
REM "already satisfied" when the cu128 install runs and skips the CUDA
REM download entirely. Warnings about packages not being installed are
REM expected and harmless on a fresh venv.
echo Removing any existing PyTorch packages from the venv...
pip uninstall -y torch torchvision torchaudio
echo.

REM Install PyTorch with CUDA 12.8 support. RTX 2080 Ti (Turing, SM 7.5) is
REM supported. cu128 ships torch 2.11.x which satisfies marker-pdf's
REM torch>=2.7.0 constraint, so the requirements.txt install below should
REM not need to upgrade or replace torch.
echo Installing PyTorch with CUDA 12.8 support...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
    echo.
    echo ERROR: PyTorch CUDA install failed.
    echo.
    echo If this machine has no CUDA-capable GPU, install the CPU build manually:
    echo   pip install torch torchvision torchaudio
    echo Then re-run: pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo.

REM Verify the installed torch is actually a CUDA build, not a CPU wheel that
REM happened to satisfy the version-only requirement. Aborts the script if
REM CUDA is not live, rather than continuing with a silently-broken setup.
echo Verifying PyTorch CUDA build...
python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)"
if errorlevel 1 (
    echo.
    echo ERROR: PyTorch installed without CUDA support.
    python -c "import torch; print('  Installed:', torch.__version__)"
    echo Expected a version string ending in '+cu128'.
    echo.
    pause
    exit /b 1
)
python -c "import torch; print('  PyTorch:', torch.__version__, '/ GPU:', torch.cuda.get_device_name(0))"
echo.

REM Install project dependencies. marker-pdf requires torch>=2.7.0,<3.0.0
REM which the cu128 install above satisfies, so this step should leave
REM torch alone. The post-install verification below catches the case
REM where it does not.
echo Installing project dependencies from requirements.txt...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)
echo.

REM Re-verify CUDA after requirements.txt: confirm marker-pdf or another
REM dependency did not silently replace the CUDA torch with a CPU build
REM (this happened with the previous cu124 setup because torch 2.6.0+cu124
REM did not satisfy marker-pdf's torch>=2.7.0 constraint).
echo Re-verifying PyTorch CUDA build after dependency install...
python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)"
if errorlevel 1 (
    echo.
    echo ERROR: PyTorch CUDA support was lost during dependency install.
    python -c "import torch; print('  Installed:', torch.__version__)"
    echo A package in requirements.txt upgraded torch to a CPU build.
    echo Inspect that package's torch version constraint.
    echo.
    pause
    exit /b 1
)
echo   OK
echo.

echo ========================================
echo Setup completed successfully!
echo ========================================
echo.
echo To activate the virtual environment in the future, run:
echo   venv\Scripts\activate.bat
echo.
echo To deactivate the virtual environment, run:
echo   deactivate
echo.
pause
