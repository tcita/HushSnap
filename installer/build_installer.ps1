param(
    [string]$IsccPath = "ISCC.exe",
    [string]$Version,
    [string]$PyInstallerPath = "pyinstaller",
    [switch]$InstallerOnly
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Resolve-Path (Join-Path $scriptDir "..")
$issPath = Join-Path $scriptDir "HashSnapInstaller.iss"
$versionFile = Join-Path $rootDir "version.txt"
$sourceFile = Join-Path $rootDir "HashSnap.py"

if (-not (Test-Path $issPath)) {
    throw "Installer script not found: $issPath"
}
if (-not (Test-Path $sourceFile)) {
    throw "Source file not found: $sourceFile"
}

if (-not $Version) {
    if (-not (Test-Path $versionFile)) {
        throw "Version file not found: $versionFile"
    }
    $Version = (Get-Content -Path $versionFile -Raw).Trim()
}

if ($Version -notmatch '^\d+\.\d+\.\d+([\-+][0-9A-Za-z\.-]+)?$') {
    throw "Invalid version '$Version'. Expected SemVer-like format, e.g. 1.0.1"
}

$sourceLines = Get-Content -Path $sourceFile
$versionLineIndex = -1
for ($i = 0; $i -lt $sourceLines.Count; $i++) {
    if ($sourceLines[$i] -match '^\s*APP_VERSION\s*=') {
        $versionLineIndex = $i
        break
    }
}

if ($versionLineIndex -lt 0) {
    throw "Could not find APP_VERSION assignment in $sourceFile"
}

$sourceLines[$versionLineIndex] = "APP_VERSION = `"$Version`""
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($sourceFile, $sourceLines, $utf8NoBom)

Push-Location $rootDir
try {
    if (-not $InstallerOnly) {
        & $PyInstallerPath --noconsole --onefile --clean HashSnap.py
    }
    & $IsccPath "/DMyAppVersion=$Version" $issPath
}
finally {
    Pop-Location
}
