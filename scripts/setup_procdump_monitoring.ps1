# Register a logon scheduled task that auto-attaches procdump to HushSnap,
# so a native crash (e.g. the Qt6Gui use-after-free under investigation)
# produces a full minidump without needing WER LocalDumps — which does not
# reliably fire for MSIX packaged apps (they crash via MoAppCrash, whose
# ReportStatus carries EtwNonCollectReason=1, skipping classic LocalDumps).
#
# procdump runs OUT-OF-PROCESS: it attaches via the Windows Debug API and
# writes the dump itself when HushSnap dies. This sidesteps every problem
# that makes in-process dump capture unreliable under Python (VEH-context
# MiniDumpWriteDump failures, Python swallowing AVs as OSError, faulthandler
# intercepting SIGSEGV before any filter runs).
#
# Run ONCE as the current user (no admin needed — registers a per-user
# logon task):
#
#     powershell -ExecutionPolicy Bypass -File scripts\setup_procdump_monitoring.ps1
#
# To disable later:
#     powershell -ExecutionPolicy Bypass -File scripts\setup_procdump_monitoring.ps1 -Remove
#
# The task starts procdump on every login; procdump then waits (-w) for
# HushSnap.exe to launch and attaches (-e -ma) to write a full dump on any
# unhandled exception. Cost when HushSnap doesn't crash: a few MB of idle
# procdump process, ~0 CPU. If you stop developing HushSnap and uninstall
# it, procdump just waits forever for a process that never appears — also
# ~0 cost. Forget about it; it's harmless.

#Requires -Version 5.1

param(
    [switch]$Remove,
    [switch]$Stop,
    [switch]$Start,
    [string]$ProcDumpPath = "C:\Tools\procdump64.exe",
    [string]$DumpDir = "$env:LOCALAPPDATA\HushSnap\dumps",
    [int]   $MaxDumps = 20
)

$ErrorActionPreference = 'Stop'
# Per-user scheduled tasks live under "\HushSnap\..." only with admin rights
# (creating a task-folder needs elevated Task Scheduler access). A flat task
# name at the root folder registers with no elevation, which is what we want
# here — the script is meant to run as the ordinary logged-in user.
$TaskName = "HushSnapProcdumpCrashMonitor"

# This machine's Task Scheduler requires admin even for per-user tasks
# (observed: both Register-ScheduledTask and schtasks return "Access denied"
# for a non-elevated session here). Detect elevation and guide the operator
# to re-run elevated, instead of failing opaquely at the Register call.
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: this script must be run as Administrator on this machine" -ForegroundColor Red
    Write-Host "(Task Scheduler here rejects per-user task registration from a" -ForegroundColor Red
    Write-Host " non-elevated session)." -ForegroundColor Red
    Write-Host ""
    Write-Host "Right-click PowerShell -> Run as administrator, then:" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\setup_procdump_monitoring.ps1" -ForegroundColor Yellow
    exit 3
}

# ── Remove mode ───────────────────────────────────────────────────────────────
if ($Remove) {
    Write-Host "Removing scheduled task '$TaskName'..." -ForegroundColor Yellow
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Task removed." -ForegroundColor Green
    } else {
        Write-Host "Task not found (nothing to remove)." -ForegroundColor DarkGray
    }
    return
}

# ── Stop / Start mode (temporarily pause/resume monitoring without removing) ─
# Useful while developing with the packaged build + a debugger: a process can
# only have one debugger, so procdump's attach conflicts with WinDbg/IDE.
if ($Stop) {
    Write-Host "Stopping task (monitoring paused, task kept for resume)..." -ForegroundColor Yellow
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    # Also kill any procdump64 instance the task left running.
    Get-Process procdump64 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped. Resume with: -Start" -ForegroundColor Green
    return
}
if ($Start) {
    Write-Host "Starting task (resuming monitoring)..." -ForegroundColor Yellow
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host "Started." -ForegroundColor Green
    return
}

# ── Install mode ──────────────────────────────────────────────────────────────

# 1. Locate procdump.
$pd = $null
foreach ($candidate in @($ProcDumpPath,
                         (Get-Command procdump64.exe -ErrorAction SilentlyContinue).Source,
                         (Get-Command procdump.exe   -ErrorAction SilentlyContinue).Source)) {
    if ($candidate -and (Test-Path $candidate)) { $pd = $candidate; break }
}
if (-not $pd) {
    Write-Host "ERROR: procdump not found." -ForegroundColor Red
    Write-Host "  Looked for: $ProcDumpPath, and on PATH."
    Write-Host "  Install it from https://learn.microsoft.com/sysinternals/downloads/procdump"
    Write-Host "  (download, unzip to C:\Tools\procdump64.exe) then re-run this script."
    exit 2
}
Write-Host "procdump: $pd" -ForegroundColor Green

# 2. Ensure dump dir exists.
New-Item -ItemType Directory -Force -Path $DumpDir | Out-Null
Write-Host "dump dir: $DumpDir" -ForegroundColor Green

# 3. Build the procdump command line.
#    -accepteula         : silent EULA acceptance (else it prompts and hangs)
#    -e                  : write dump on any unhandled exception (1st chance
#                          also caught with -e 1, but plain -e = unhandled,
#                          which is what we want — matches a real crash)
#    -ma                 : full memory dump (matches WER DumpType=2; needed to
#                          read the dangling RCX / vtable and resolve the Qt
#                          object in the 6/28 access-violation investigation)
#    -w                  : wait for the process to START if not running, then
#                          attach. Keeps monitoring across HushSnap restarts.
#    -n <MaxDumps>       : cap number of dumps before procdump exits (disk
#                          protection — a crash loop won't fill the disk)
#    HushSnap.exe        : target by image name (matches any HushSnap.exe,
#                          including the MSIX packaged one)
#    dump file template  : <DumpDir>\HushSnap_<pid>_<timestamp>.dmp
$dumpTemplate = Join-Path $DumpDir "HushSnap_%d_%t.dmp"
$pdArgs = @("-accepteula", "-e", "-ma", "-w", "-n", $MaxDumps, "HushSnap.exe", $dumpTemplate)

# Launch procdump with NO visible window via a VBScript wrapper.
# `powershell -WindowStyle Hidden` still flashes an empty console briefly
# (the window is created before it can be hidden), which is what left the
# "blank terminal" the operator saw. wscript.exe running a .vbs has no
# console at all, and WScript.Shell.Run(..., 0, False) starts the target
# with a hidden window — zero flash. The .vbs is generated next to the
# script so the task just runs `wscript.exe <vbs>` at logon.
$vbsPath = Join-Path $DumpDir "procdump_monitor.vbs"
# Build the procdump command line as a single quoted string for the .vbs.
$pdCmdLine = "$pd $($pdArgs -join ' ')"
$vbs = @"
' Auto-generated by setup_procdump_monitoring.ps1
' Launches procdump hidden and returns immediately. wscript itself has no
' console, so no window ever appears. procdump runs detached, hidden.
Set sh = CreateObject("WScript.Shell")
sh.Run "$pdCmdLine", 0, False
"@
$vbs | Out-File -FilePath $vbsPath -Encoding ASCII -Force
Write-Host "vbs wrapper: $vbsPath" -ForegroundColor DarkGray

$actionExec = "wscript.exe"
$actionArg  = "`"$vbsPath`""

# 4. Register the scheduled task — runs at user logon, no admin needed.
$action    = New-ScheduledTaskAction -Execute $actionExec -Argument $actionArg
$trigger   = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -StartWhenAvailable `
                -ExecutionTimeLimit ([TimeSpan]::Zero)   # run indefinitely (procdump stays attached)

# Re-create the task (remove old if present).
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Replaced existing task." -ForegroundColor DarkGray
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Auto-attach procdump to HushSnap on login; write a full minidump on crash. See scripts/setup_procdump_monitoring.ps1." `
    -Force | Out-Null

# 5. Start it now (don't wait for next logon).
Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Done. procdump is now monitoring HushSnap.exe." -ForegroundColor Green
Write-Host "  Dumps on crash go to: $DumpDir"
Write-Host "  Max dumps before procdump exits: $MaxDumps (disk protection)"
Write-Host "  Restarts automatically on every login."
Write-Host ""
Write-Host "To remove later:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\setup_procdump_monitoring.ps1 -Remove"
