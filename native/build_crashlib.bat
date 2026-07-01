@echo off
REM build_crashlib.bat - compile native/crashlib.c to assets/crashlib.dll (x64)
REM
REM Standalone diagnostic only — NOT bundled by HushSnap.spec and not referenced
REM by app code. Invoke trigger_crash() from a separate Python process to verify
REM the WinDbg JIT crash path on this machine. See scripts/NATIVE_CRASH_DEBUGGING.md.
REM
REM Requires MSVC Build Tools. Locates vcvars64.bat via vswhere.
REM
REM     native\build_crashlib.bat

setlocal enableextensions

set "PF86=%ProgramFiles(x86)%"
if not defined PF86 set "PF86=%ProgramFiles%"
set "VSWHERE=%PF86%\Microsoft Visual Studio\Installer\vswhere.exe"

if not exist "%VSWHERE%" goto :no_vswhere

for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALL=%%i"
if not defined VSINSTALL goto :no_toolchain

set "VCVARS=%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VCVARS%" goto :no_vcvars

call "%VCVARS%" >nul
if errorlevel 1 goto :vcvars_fail

set "ROOT=%~dp0.."
if not exist "%ROOT%\assets" mkdir "%ROOT%\assets"
if not exist "%ROOT%\build\crashlib" mkdir "%ROOT%\build\crashlib"

REM Object, pdb, and link artifacts (.exp/.lib/.ilk) go to build\crashlib so
REM *.pdb stays out of assets\ and build_msix's debug-artifact check passes.
REM /Fd pins the pdb path; the linker drops the rest next to the .dll. Only
REM the clean .dll is copied up to assets\.
cl /nologo /LD /Zi /O2 /W3 /EHsc /Fo"%ROOT%\build\crashlib\\" /Fd"%ROOT%\build\crashlib\\" /Fe"%ROOT%\build\crashlib\crashlib.dll" "%ROOT%\native\crashlib.c" /link kernel32.lib /SUBSYSTEM:WINDOWS
if errorlevel 1 goto :compile_fail

copy /Y "%ROOT%\build\crashlib\crashlib.dll" "%ROOT%\assets\crashlib.dll" >nul
if errorlevel 1 goto :compile_fail

echo.
echo Built: %ROOT%\assets\crashlib.dll  (symbols: %ROOT%\build\crashlib\crashlib.pdb)
endlocal & exit /b 0

:no_vswhere
echo ERROR: vswhere not found at %VSWHERE%
echo        Install Desktop development with C++ Build Tools.
exit /b 1

:no_toolchain
echo ERROR: no VS installation with the x64 C++ toolchain found.
echo        Install MSVC v143 - VS 2022 C++ x64/x86 build tools.
exit /b 1

:no_vcvars
echo ERROR: vcvars64.bat not found at %VCVARS%
exit /b 1

:vcvars_fail
echo ERROR: vcvars64.bat failed.
exit /b 1

:compile_fail
echo ERROR: compilation failed.
exit /b 1