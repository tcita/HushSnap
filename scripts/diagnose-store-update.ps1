# ============================================
# Microsoft Store "Phantom Update" Diagnostic
# --------------------------------------------
# What it solves: when you suspect the Store "only updated the listing
#   copy/screenshots and didn't actually push a new package", use the
#   local logs to tell whether a real package was downloaded and installed.
#
# Core principle (the starting point of this whole diagnosis - grasp this
# before reading the sections below):
#   * A Store listing update (copy/screenshots) is pure metadata. It produces
#     NO Windows Update (WU) events.
#   * Only a real package push writes to the WindowsUpdateClient log and
#     carries a revision UUID; a changed revision UUID = changed package content.
#   * So the verdict must combine THREE dimensions - none alone is enough:
#       (1) Any download event?   none -> maybe copy-only; some -> at least a
#                                  package was pushed
#       (2) Did the revision change?  multiple revisions = content really
#                                     changed; same revision many times =
#                                     stale DO cache (CDN not synced yet)
#       (3) The actually-installed version  -> ground truth; separates
#                                              "downloaded" from "installed new"
#   * NEVER conclude "update succeeded" from a single download alone - this
#     is the single most common misjudgment.
#
# Usage: powershell -File scripts/diagnose-store-update.ps1
#        optional: -Hours 72  (lookback window, default 72h; too short a
#                   window misses historical stale-cache events)
# ============================================

param(
    [int]$Hours = 72,
    [string]$AppName = "HushSnap"
)

function Match-App($event, $name) {
    foreach ($p in $event.Properties) {
        if ($p.Value -and $p.Value -match $name) { return $true }
    }
    return $false
}

Write-Host "====================================" -ForegroundColor Cyan
Write-Host " Target: $AppName" -ForegroundColor Cyan
Write-Host " Time:   $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host " Window: last $Hours h" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""

# ====== 0. Installed version (ground truth) ======
# The hardest signal - read this first. It separates "a package was
# downloaded" from "the new version actually got installed".
# If not found, it's usually installed under another user/arch -
# -AllUsers is already used; retry elevated if still empty.
Write-Host "[0] Installed version (ground truth)" -ForegroundColor Yellow
$installed = Get-AppxPackage -AllUsers -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match $AppName -or $_.PackageFullName -match $AppName
}
if ($installed) {
    $installed | Sort-Object Version -Descending | ForEach-Object {
        Write-Host "  $($_.Name)  v$($_.Version)  ($($_.Architecture))" -ForegroundColor Green
    }
} else {
    Write-Host "  Not found via Get-AppxPackage -AllUsers" -ForegroundColor DarkYellow
    Write-Host "  >> May be uninstalled, or needs admin to see"
}
Write-Host ""

# ====== 1. WU download history ======
Write-Host "[1] Windows Update downloads ($Hours h)" -ForegroundColor Yellow
Write-Host "    revision change = package content change; same revision many times = stale DO cache" -ForegroundColor DarkGray
Write-Host ""

$events = Get-WinEvent -LogName 'Microsoft-Windows-WindowsUpdateClient/Operational' `
    -MaxEvents 2000 -ErrorAction SilentlyContinue | Where-Object {
    $_.TimeCreated -gt (Get-Date).AddHours(-$Hours)
}

$appEvents = $events | Where-Object { Match-App $_ $AppName }

if ($appEvents) {
    $appEvents | Sort-Object TimeCreated | ForEach-Object {
        $p = $_.Properties.Value
        $time = $_.TimeCreated.ToString('MM-dd HH:mm')
        $icon = if ($_.Id -eq 41) { 'OK ' } else { 'FAIL' }
        $color = if ($_.Id -eq 41) { 'Green' } else { 'Red' }
        $revShort = if ($p[1] -and $p[1].Length -gt 8) { $p[1].Substring(0, 8) } else { $p[1] }
        Write-Host "  [$time] $icon  rev=$revShort  $($p[0])" -ForegroundColor $color
    }

    # Count only successful downloads (Event 41) by distinct revision
    $revisions = ($appEvents | Where-Object { $_.Id -eq 41 }).Properties.Value `
        | Where-Object { $_ -match '^[0-9a-f]{8}-' } | Select-Object -Unique

    Write-Host ""
    Write-Host "  Unique revisions: $($revisions.Count)" -ForegroundColor $(if ($revisions.Count -le 1 -and $appEvents.Count -gt 1) { 'Red' } else { 'Green' })
} else {
    Write-Host "  No $AppName records found in $Hours h" -ForegroundColor DarkGray
}

# ====== 2. Failures ======
Write-Host ""
Write-Host "[2] Download failures ($Hours h)" -ForegroundColor Yellow
$failures = $events | Where-Object { $_.Id -eq 31 }
$appFailures = $failures | Where-Object { Match-App $_ $AppName }

if ($appFailures) {
    $appFailures | ForEach-Object {
        $p = $_.Properties.Value
        Write-Host "  [$($_.TimeCreated.ToString('MM-dd HH:mm'))] FAIL  $($p[0])  code: $($p[1])" -ForegroundColor Red
    }
} elseif ($failures.Count -gt 0) {
    Write-Host "  $($failures.Count) failure(s) from other apps (not $AppName)" -ForegroundColor DarkYellow
} else {
    Write-Host "  No failures -- network is fine" -ForegroundColor Green
}

# ====== 3. DO cache ======
Write-Host ""
Write-Host "[3] DO cache overview" -ForegroundColor Yellow
$do = Get-DeliveryOptimizationStatus -ErrorAction SilentlyContinue
$complete = ($do | Where-Object { $_.Status -eq 'Complete' }).Count
$partial = ($do | Where-Object { $_.Status -eq 'Caching' }).Count
$totalMB = [math]::Round(($do | Measure-Object FileSizeInCache -Sum).Sum / 1MB, 0)
Write-Host "  Cached: $totalMB MB  |  Complete: $complete  |  Partial: $partial"
Write-Host "  (Complete=0 with all Caching means a package is still transferring or just finished but not flushed)" -ForegroundColor DarkGray

# ====== Verdict ======
Write-Host ""
Write-Host "====================================" -ForegroundColor Cyan
Write-Host " VERDICT" -ForegroundColor Yellow

# Priority (most certain to least):
#   1. No WU events at all  -> Store may have only updated the listing copy
#      (listing updates produce no WU events)
#   2. Download failures    -> network/CDN issue
#   3. Same revision many times -> stale DO cache, CDN hasn't synced the
#      new version (not your fault)
#   4. Only one download    -> proves only that a package was fetched,
#      NOT that it's a new version! Trust [0] instead
#   5. Multiple distinct revisions -> package content really changed,
#      high confidence
# Caveat: never misread case 4 as "update succeeded".
if (-not $appEvents) {
    Write-Host "  No WU activity for $AppName in $Hours h" -ForegroundColor DarkYellow
    Write-Host "  >> Store may have only updated the listing copy (copy updates produce no WU events)"
    Write-Host "  >> Or the package hasn't rolled out yet. Try: wsreset.exe then re-check"
} elseif ($appFailures.Count -gt 0) {
    Write-Host "  $AppName has download FAILURES -- network issue suspected" -ForegroundColor Red
} elseif ($revisions.Count -le 1 -and $appEvents.Count -gt 1) {
    Write-Host "  STALE CDN CACHE -- not your fault" -ForegroundColor Red
    Write-Host "  >> Same revision downloaded $($appEvents.Count)x, CDN hasn't synced the new version"
    Write-Host "  >> Wait a few hours, or run: wsreset.exe"
} elseif ($appEvents.Count -eq 1) {
    Write-Host "  Only ONE download detected -- version UNVERIFIED" -ForegroundColor DarkYellow
    Write-Host "  >> This only proves a package was fetched once, not that it's the new version"
    Write-Host "  >> Trust [0]'s installed version; if unexpected, uninstall+reinstall forces a re-pull"
} else {
    Write-Host "  $($revisions.Count) distinct revisions downloaded -- update is likely REAL" -ForegroundColor Green
    Write-Host "  >> Package content really changed; still confirm against [0]'s installed version"
}

Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to exit"
