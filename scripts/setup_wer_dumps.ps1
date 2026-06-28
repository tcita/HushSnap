# Configure Windows Error Reporting to capture a full minidump whenever
# HushSnap.exe crashes. Run ONCE as Administrator:
#
#     powershell -ExecutionPolicy Bypass -File scripts\setup_wer_dumps.ps1
#
# Why: the rare OCR crash under investigation likely halts inside native
# onnxruntime code (the log will stop after `[OCR_CHAIN] recognize() engine
# call begin`). Python's faulthandler cannot capture a native access-violation
# stack, so without a .dmp we only know *that* it crashed in the engine, not
# *where*. WER writes a full dump to %LOCALAPPDATA%\HushSnap\dumps on every
# crash; open it in WinDbg/x64dbg for the native call stack.
#
# To disable later: Remove-Item "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\HushSnap.exe" -Recurse

#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'

$key = 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\HushSnap.exe'
New-Item -Path $key -Force | Out-Null

# DumpFolder is expanded per-user, so %LOCALAPPDATA% resolves correctly for
# whoever the crashing process runs as.
Set-ItemProperty -Path $key -Name 'DumpFolder' -Value '%LOCALAPPDATA%\HushSnap\dumps' -Type ExpandString
Set-ItemProperty -Path $key -Name 'DumpCount'  -Value 20                          -Type DWord
# DumpType 2 = full dump (includes heap + all threads). 0 = custom, 1 = mini.
Set-ItemProperty -Path $key -Name 'DumpType'   -Value 2                           -Type DWord

$dumpDir = Join-Path $env:LOCALAPPDATA 'HushSnap\dumps'
New-Item -Path $dumpDir -ItemType Directory -Force | Out-Null

Write-Host "WER LocalDumps configured for HushSnap.exe." -ForegroundColor Green
Write-Host "Crash dumps will be written to: $dumpDir"
Write-Host "Run scripts\stress_test_ocr.py to start testing."
