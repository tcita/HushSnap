@echo off
chcp 65001 >nul
echo ==========================================================
echo HushSnap MSIX Packaging - DEV Build
echo (For local testing - version 0.0.0.0, auto-register)
echo ==========================================================
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0installer\build_msix.ps1" -Dev %*
if not %errorLevel% == 0 (
    echo.
    echo ERROR: Build failed.
    echo.
    pause
    exit /b %errorLevel%
)

echo.
echo Dev build and registration complete!
echo Search "HushSnap" in the Start Menu to run.
echo.
echo To update after code changes: re-run build_msix_dev.bat
echo.
timeout /t 5 /nobreak >nul
exit /b 0
