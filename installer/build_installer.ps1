param(
    [string]$IsccPath = "ISCC.exe"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$issPath = Join-Path $scriptDir "HashSnapInstaller.iss"

if (-not (Test-Path $issPath)) {
    throw "Installer script not found: $issPath"
}

& $IsccPath $issPath
