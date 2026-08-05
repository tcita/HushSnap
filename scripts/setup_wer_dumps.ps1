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
# Dumps land at %LOCALAPPDATA%\hushsnap_dumps (flat directory, not nested
# under an old config directory).
#
# To disable later:
#   Remove-Item "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\HushSnap.exe" -Recurse
#   Remove-Item "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\python.exe" -Recurse

#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'

$key = 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\HushSnap.exe'
New-Item -Path $key -Force | Out-Null

Set-ItemProperty -Path $key -Name 'DumpFolder' -Value '%LOCALAPPDATA%\hushsnap_dumps' -Type ExpandString
Set-ItemProperty -Path $key -Name 'DumpCount'  -Value 20                          -Type DWord
Set-ItemProperty -Path $key -Name 'DumpType'   -Value 1                           -Type DWord
# DumpType 1 = minidump (call stacks + module list; ~tens of MB).
# DumpType 2 = full dump (entire process memory; ~committed size).
# Full dumps are excessive for a ~1.5 GB Python process — minidump has
# everything needed to diagnose a native crash inside onnxruntime.

# Also cover dev-mode crashes (python HushSnap.py). Process name matching means
# every Python crash on this machine writes a dump, so keep a smaller cap.
$pyKey = 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\python.exe'
New-Item -Path $pyKey -Force | Out-Null
Set-ItemProperty -Path $pyKey -Name 'DumpFolder' -Value '%LOCALAPPDATA%\hushsnap_dumps' -Type ExpandString
Set-ItemProperty -Path $pyKey -Name 'DumpCount'  -Value 10                          -Type DWord
Set-ItemProperty -Path $pyKey -Name 'DumpType'   -Value 1                           -Type DWord

$dumpDir = Join-Path $env:LOCALAPPDATA 'hushsnap_dumps'
New-Item -Path $dumpDir -ItemType Directory -Force | Out-Null

Write-Host "WER LocalDumps configured for HushSnap.exe and python.exe." -ForegroundColor Green
Write-Host "Crash dumps will be written to: $dumpDir"
