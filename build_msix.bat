@echo off
chcp 65001 >nul
echo ==========================================================
echo Starting HushSnap MSIX Packaging...
echo ==========================================================
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
powershell -Command "$v = (& git -C '%~dp0' describe --tags --abbrev=0 2>$null) -replace '^v',''; if (-not $v) { $v = 'unknown' }; Write-Host '  Version:' $v"
echo.
timeout /t 1 /nobreak >nul
exit /b 0
