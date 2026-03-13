# 构建安装包的脚本。
param(
    [string]$IsccPath = "ISCC.exe",
    [string]$Version,
    [string]$PyInstallerPath = "pyinstaller",
    [string]$SpecPath = "HashSnap.spec"
)

$ErrorActionPreference = "Stop"

function Invoke-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$StepName
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$StepName failed with exit code $LASTEXITCODE"
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Resolve-Path (Join-Path $scriptDir "..")
$issPath = Join-Path $scriptDir "HashSnapInstaller.iss"
$packageInitFile = Join-Path $rootDir "hashsnap\__init__.py"
$resolvedSpecPath = Join-Path $rootDir $SpecPath

if (-not (Test-Path $issPath)) {
    throw "Installer script not found: $issPath"
}
if (-not (Test-Path $packageInitFile)) {
    throw "Package init file not found: $packageInitFile"
}
if (-not (Test-Path $resolvedSpecPath)) {
    throw "PyInstaller spec not found: $resolvedSpecPath"
}

if (-not $Version) {
    $versionMatch = Select-String -Path $packageInitFile -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $versionMatch) {
        throw "Could not find __version__ assignment in $packageInitFile"
    }
    $Version = $versionMatch.Matches[0].Groups[1].Value
}

if ($Version -notmatch '^\d+\.\d+\.\d+([\-+][0-9A-Za-z\.-]+)?$') {
    throw "Invalid version '$Version'. Expected SemVer-like format, e.g. 1.0.1"
}

Push-Location $rootDir
try {
    $distDir = Join-Path $rootDir "dist\HashSnap"
    if (Test-Path $distDir) {
        Remove-Item -Path $distDir -Recurse -Force
    }

    Invoke-ExternalCommand -Executable $PyInstallerPath -Arguments @(
        "--clean",
        $resolvedSpecPath
    ) -StepName "PyInstaller build"

    Invoke-ExternalCommand -Executable $IsccPath -Arguments @("/DMyAppVersion=$Version", $issPath) -StepName "Inno Setup build"
}
finally {
    Pop-Location
}

