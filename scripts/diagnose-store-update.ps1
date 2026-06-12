# ============================================
# Microsoft Store "Phantom Update" Diagnostic
# Usage: powershell -File debug-store.ps1
# ============================================

$AppName = "HushSnap"

function Match-App($event, $name) {
    foreach ($p in $event.Properties) {
        if ($p.Value -and $p.Value -match $name) { return $true }
    }
    return $false
}

Write-Host "====================================" -ForegroundColor Cyan
Write-Host " Target: $AppName" -ForegroundColor Cyan
Write-Host " Time:   $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""

# ====== 1. WU download history (24h) ======
Write-Host "[1] Windows Update downloads (24h)" -ForegroundColor Yellow
Write-Host "    Same Revision appearing multiple times = stale DO cache" -ForegroundColor DarkGray
Write-Host ""

$events = Get-WinEvent -LogName 'Microsoft-Windows-WindowsUpdateClient/Operational' `
    -MaxEvents 2000 -ErrorAction SilentlyContinue | Where-Object {
    $_.TimeCreated -gt (Get-Date).AddHours(-24)
}

$appEvents = $events | Where-Object { Match-App $_ $AppName }

if ($appEvents) {
    $appEvents | ForEach-Object {
        $p = $_.Properties.Value
        $time = $_.TimeCreated.ToString('HH:mm')
        $icon = if ($_.Id -eq 41) { 'OK' } else { 'FAIL' }
        $color = if ($_.Id -eq 41) { 'Green' } else { 'Red' }
        $revShort = if ($p[1] -and $p[1].Length -gt 12) { $p[1].Substring(0, 12) + '...' } else { $p[1] }
        Write-Host "  [$time] $icon  rev=$revShort  $($p[0])" -ForegroundColor $color
    }

    # Count unique revisions from Event 41
    $revisions = ($appEvents | Where-Object { $_.Id -eq 41 }).Properties.Value `
        | Where-Object { $_ -match '^[0-9a-f]{8}-' } | Select-Object -Unique

    Write-Host ""
    Write-Host "  Unique revisions: $($revisions.Count)" -ForegroundColor $(if ($revisions.Count -le 1 -and $appEvents.Count -gt 1) { 'Red' } else { 'Green' })
    if ($revisions.Count -le 1 -and $appEvents.Count -gt 1) {
        Write-Host "  >>> STALE CACHE: same rev downloaded $($appEvents.Count)x, CDN not synced yet" -ForegroundColor Red
    }
} else {
    Write-Host "  No $AppName records found in 24h" -ForegroundColor DarkGray
}

# ====== 2. Failures ======
Write-Host ""
Write-Host "[2] Download failures (24h)" -ForegroundColor Yellow
$failures = $events | Where-Object { $_.Id -eq 31 }
$appFailures = $failures | Where-Object { Match-App $_ $AppName }

if ($appFailures) {
    $appFailures | ForEach-Object {
        $p = $_.Properties.Value
        Write-Host "  [$($_.TimeCreated.ToString('HH:mm'))] FAIL  $($p[0])  code: $($p[1])" -ForegroundColor Red
    }
} elseif ($failures.Count -gt 0) {
    Write-Host "  $($failures.Count) failure(s) from other apps (not $AppName)" -ForegroundColor DarkYellow
    $failures | ForEach-Object {
        $p = $_.Properties.Value
        Write-Host "    [$($_.TimeCreated.ToString('HH:mm'))] $($p[0])  code: $($p[1])" -ForegroundColor DarkGray
    }
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
Write-Host "  Mode: $($do[0].DownloadMode)  |  P2P: $($do[0].PercentPeerCaching)%"

# Files >10MB that downloaded in <3s = cache hits
$suspicious = $do | Where-Object {
    $_.FileSize -gt 10MB -and $_.DownloadDuration.TotalSeconds -lt 3 -and $_.Status -eq 'Complete'
}
if ($suspicious) {
    Write-Host "  Suspiciously fast downloads (cache hits):" -ForegroundColor Red
    $suspicious | ForEach-Object {
        $mb = [math]::Round($_.FileSize/1MB, 1)
        $sec = [math]::Round($_.DownloadDuration.TotalSeconds, 1)
        Write-Host "    ${mb}MB in ${sec}s" -ForegroundColor Red
    }
}

# ====== Verdict ======
Write-Host ""
Write-Host "====================================" -ForegroundColor Cyan
Write-Host " VERDICT" -ForegroundColor Yellow

if (-not $appEvents) {
    Write-Host "  No WU activity for $AppName in 24h" -ForegroundColor DarkYellow
    Write-Host "  >> If you just updated, Store may have skipped the download entirely"
    Write-Host "  >> Try: wsreset.exe"
} elseif ($appFailures.Count -gt 0) {
    Write-Host "  $AppName has download FAILURES -- network issue suspected" -ForegroundColor Red
} elseif ($revisions.Count -le 1 -and $appEvents.Count -gt 1) {
    Write-Host "  STALE CDN CACHE -- not your fault" -ForegroundColor Red
    Write-Host "  >> Wait a few hours, or run: wsreset.exe"
} else {
    Write-Host "  Revision changed, no failures -- update is real" -ForegroundColor Green
}

Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to exit"
