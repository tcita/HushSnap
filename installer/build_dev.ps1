# Dev/debug build script: kill process + build EXE only (no installer).
param(
    [string]$PyInstallerPath = "pyinstaller",
    [string]$SpecPath = "HushSnap.spec",
    [switch]$Clean
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

function Remove-DirectoryIfExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [int]$MaxAttempts = 8,
        [int]$DelayMs = 700
    )

    if (-not (Test-Path $Path)) {
        return
    }

    $lastError = $null
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
            return
        }
        catch {
            $lastError = $_

            # Try to stop processes whose executable path is inside the target folder
            # (common when the built EXE is still locked).
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Path, [System.StringComparison]::OrdinalIgnoreCase) } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

            # Fallback by process name in case executable path is unavailable.
            Get-Process -Name "HushSnap" -ErrorAction SilentlyContinue | Stop-Process -Force

            Start-Sleep -Milliseconds $DelayMs
        }
    }

    throw "Failed to remove '$Path' after $MaxAttempts attempts. Last error: $($lastError.Exception.Message)"
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Resolve-Path (Join-Path $scriptDir "..")
$resolvedSpecPath = Join-Path $rootDir $SpecPath

if (-not (Test-Path $resolvedSpecPath)) {
    throw "PyInstaller spec not found: $resolvedSpecPath"
}

Push-Location $rootDir
try {
    # 1) Stop running app processes to release file handles.
    Get-Process -Name "HushSnap" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 800

    # 2) Clean build directories with retry (only if -Clean is passed).
    # Keeping these by default enables extremely fast incremental compilation.
    if ($Clean) {
        Remove-DirectoryIfExists -Path (Join-Path $rootDir "dist\HushSnap")
        Remove-DirectoryIfExists -Path (Join-Path $rootDir "build\HushSnap")
    }

    # 3) Build onedir EXE only (no installer output).
    $pyinstallerArgs = @()
    if ($Clean) {
        $pyinstallerArgs += "--clean"
    }
    $pyinstallerArgs += $resolvedSpecPath

    Invoke-ExternalCommand -Executable $PyInstallerPath -Arguments $pyinstallerArgs -StepName "PyInstaller build"
}
finally {
    Pop-Location
}
