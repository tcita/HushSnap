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

$sourceText = Get-Content -Path $sourceFile -Raw
$updatedSourceText = [regex]::Replace(
    $sourceText,
    '(?m)^APP_VERSION\s*=\s*"[^"]*"$',
    "APP_VERSION = `"$Version`""
)

if ($updatedSourceText -eq $sourceText) {
    throw "Could not find APP_VERSION assignment in $sourceFile"
}

Set-Content -Path $sourceFile -Value $updatedSourceText -Encoding UTF8NoBOM

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
