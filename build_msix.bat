@echo off
chcp 65001 >nul
echo ==========================================================
echo HushSnap MSIX Packaging
echo ==========================================================
echo.
echo Workflow:
echo   1. Quick dev test:  python HushSnap.py
echo   2. Release build:   build_msix.bat ^(runs pytest automatically, then sign + install^)
echo      Skip tests only: build_msix.bat -SkipTests
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0installer\build_msix.ps1" %*
if not %errorLevel% == 0 (
    echo.
    echo ERROR: Build failed.
    echo.
    pause
    exit /b %errorLevel%
)

echo.
echo Build succeeded!
echo.
echo Next: sign the MSIX with your certificate, then install.
echo.
timeout /t 5 /nobreak >nul
exit /b 0
