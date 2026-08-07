<#
.SYNOPSIS
Verify no network-capable code leaked into the frozen build.
Run AFTER pyinstaller HushSnap.spec completes.
#>
param(
    [string]$DistDir = "$PSScriptRoot\..\..\dist\HushSnap"
)

$ErrorActionPreference = 'Stop'
$internal = Join-Path $DistDir '_internal'

Write-Host "=== Checking _internal for network packages ==="

# Directories that must NOT exist in the build
$forbiddenDirs = @('requests', 'urllib3', 'idna')
foreach ($d in $forbiddenDirs) {
    $p = Join-Path $internal $d
    if (Test-Path $p) {
        Write-Host "FAIL: $d/ directory found in _internal" -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK: $d/ absent"
}

# certifi and charset_normalizer: were bundled before via requests' transitive
# deps.  With the stubs in place they should also be absent.
$shouldBeGone = @('certifi', 'charset_normalizer')
foreach ($d in $shouldBeGone) {
    $p = Join-Path $internal $d
    if (Test-Path $p) {
        Write-Host "FAIL: $d/ directory found in _internal" -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK: $d/ absent"
}

Write-Host "`n=== Scanning HushSnap.exe for network indicators ==="
$exe = Join-Path $DistDir 'HushSnap.exe'
if (-not (Test-Path $exe)) {
    Write-Host "SKIP: HushSnap.exe not found at $exe"
    exit 0
}

# Strings that suggest network capability in the frozen build
$forbiddenStrings = @(
    'modelscope.cn',
    'RapidAI/RapidOCR',
    'huggingface.co',
    'urlopen',
    'cacert.pem'
)

$exeBytes = [System.IO.File]::ReadAllBytes($exe)
$exeText = [System.Text.Encoding]::ASCII.GetString($exeBytes)

foreach ($s in $forbiddenStrings) {
    if ($exeText.Contains($s)) {
        Write-Host "FAIL: forbidden string '$s' found in HushSnap.exe" -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK: '$s' absent"
}

Write-Host "`n=== PASS: No network capability detected ===" -ForegroundColor Green
