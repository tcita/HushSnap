@echo off
chcp 65001 >nul
:: Register the unpacked developer folder as an MSIX package locally.
:: This is the absolute fastest way to test your MSIX changes.
:: Requires Windows Developer Mode to be ON. Does NOT require Admin!

set MANIFEST_PATH=%~dp0build\msix_stage\AppxManifest.xml

echo ==========================================================
echo Registering HushSnap Developer MSIX (Loose Folder)...
echo ==========================================================
echo.

if not exist "%MANIFEST_PATH%" (
    echo ERROR: Staging AppxManifest not found at:
    echo %MANIFEST_PATH%
    echo.
    echo Please run 'build_msix.bat' first to build the layout.
    goto end
)

echo Registering package with Windows...
powershell -ExecutionPolicy Bypass -Command "Add-AppxPackage -Register -Path '%MANIFEST_PATH%'"

if %errorLevel% == 0 (
    echo.
    echo [OK] HushSnap successfully registered in MSIX container!
    echo [OK] You can now search "HushSnap" in the Start Menu and run it.
    echo [OK] To update after code changes: run build_msix.bat, then run this script again.
) else (
    echo.
    echo ERROR: Registration failed. 
    echo Please make sure Windows Developer Mode is enabled:
    echo Go to Settings - System - For developers, and turn ON Developer Mode.
)

:end
echo.
pause
