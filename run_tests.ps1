param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"

if (-not $PytestArgs -or $PytestArgs.Count -eq 0) {
    $PytestArgs = @("tests", "-q")
}

$repoRoot = $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$activeVenvPython = $null
if ($env:VIRTUAL_ENV) {
    $activeVenvPython = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
}

if (Test-Path $venvPython) {
    & $venvPython -m pytest @PytestArgs
    exit $LASTEXITCODE
}

if ($activeVenvPython -and (Test-Path $activeVenvPython)) {
    & $activeVenvPython -m pytest @PytestArgs
    exit $LASTEXITCODE
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) {
    & $pythonCmd.Source -m pytest @PytestArgs
    exit $LASTEXITCODE
}

$pyCmd = Get-Command py -ErrorAction SilentlyContinue
if ($pyCmd) {
    & $pyCmd.Source -m pytest @PytestArgs
    exit $LASTEXITCODE
}

Write-Error @"
No Python interpreter found.
Please do one of the following:
1) Create a local virtual environment at .\.venv
2) Activate your existing venv before running this script
3) Ensure python or py launcher is available in PATH
"@
