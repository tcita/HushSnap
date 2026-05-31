# PowerShell script to package HushSnap as an MSIX package.
param(
    [string]$PackageName = "TCITAStudio.HushSnap",
    [string]$Publisher = "CN=D80F0A50-29F3-47FE-8515-5ABF0F3E49FA",
    [string]$PublisherDisplayName = "TCITA Studio",
    [string]$DisplayName = "HushSnap",
    [string]$Version,
    [switch]$Rebuild,
    [switch]$Sign
)

$ErrorActionPreference = "Stop"

# 1) Locate tools from the Windows SDK
$sdkPath = "C:\Program Files (x86)\Windows Kits\10\bin"
Write-Host "Locating Windows SDK packaging tools..." -ForegroundColor Cyan

$makeappx = Get-ChildItem -Path $sdkPath -Filter "makeappx.exe" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like "*\x64\*" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName

$signtool = Get-ChildItem -Path $sdkPath -Filter "signtool.exe" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like "*\x64\*" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName

if (-not $makeappx) {
    throw "makeappx.exe not found in Windows SDK bin directory ($sdkPath). Please ensure Windows 10/11 SDK is installed."
}

Write-Host "  [Found] MakeAppx: $makeappx" -ForegroundColor Green
if ($Sign) {
    if (-not $signtool) {
        throw "signtool.exe not found. Cannot sign the package."
    }
    Write-Host "  [Found] SignTool: $signtool" -ForegroundColor Green
}

# 2) Set up paths
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Resolve-Path (Join-Path $scriptDir "..")
$distDir = Join-Path $rootDir "dist\HushSnap"
$stageDir = Join-Path $rootDir "build\msix_stage"
$outputDir = Join-Path $rootDir "dist-installer"

# 3) Resolve and parse version from git tag
if (-not $Version) {
    $gitTag = & git -C $rootDir describe --tags --abbrev=0 2>$null
    if ($gitTag) {
        $rawVersion = $gitTag.TrimStart('v')
        Write-Host "Extracted version '$rawVersion' from git tag '$gitTag'" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "==========================================================" -ForegroundColor Red
        Write-Host "  ERROR: No git tag found in repository!" -ForegroundColor Red
        Write-Host "  The MSIX build requires an annotated tag to determine" -ForegroundColor Red
        Write-Host "  the version number (e.g. 'v0.3.0')." -ForegroundColor Red
        Write-Host "  Create one first: git tag -a v0.3.0 -m 'release v0.3.0'" -ForegroundColor Red
        Write-Host "  Or override manually: build_msix.ps1 -Version '0.3.0'" -ForegroundColor Red
        Write-Host "==========================================================" -ForegroundColor Red
        Write-Host ""
        throw "Version resolution failed: no git tag found."
    }
} else {
    $rawVersion = $Version
    Write-Host "Using provided version '$rawVersion'" -ForegroundColor Green
}

# Convert SemVer (e.g. 0.0.1) to Quad format (e.g. 0.0.1.0)
$versionParts = $rawVersion -split '\.'
while ($versionParts.Count -lt 4) {
    $versionParts += "0"
}
$quadVersion = ($versionParts[0..3] -join ".")
Write-Host "Configured MSIX package version to '$quadVersion'" -ForegroundColor Green

# 4) Inject version into __init__.py then compile with PyInstaller
$initPyPath = Join-Path $rootDir "hushsnap\__init__.py"
$initPyBackup = Get-Content -Path $initPyPath -Raw
try {
    Write-Host "Injecting version '$rawVersion' into __init__.py for build..." -ForegroundColor Cyan
    $patchedInit = $initPyBackup -replace '__version__\s*=\s*"[^"]*"', "__version__ = `"$rawVersion`""
    $patchedInit | Set-Content -Path $initPyPath -Encoding UTF8 -NoNewline
    # Ensure trailing newline (Set-Content -NoNewline strips it)
    Add-Content -Path $initPyPath -Value "`n"

    Write-Host "Building HushSnap with PyInstaller..." -ForegroundColor Cyan

    # Kill running HushSnap processes to release file handles
    Get-Process -Name "HushSnap" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 800

    if ($Rebuild) {
        if (Test-Path $distDir) {
            Remove-Item -Path $distDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        $buildDir = Join-Path $rootDir "build\HushSnap"
        if (Test-Path $buildDir) {
            Remove-Item -Path $buildDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    $specPath = Join-Path $rootDir "HushSnap.spec"
    $pyinstallerArgs = @("--noconfirm")
    if ($Rebuild) {
        $pyinstallerArgs += "--clean"
    }
    $pyinstallerArgs += $specPath

    & pyinstaller $pyinstallerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }

    if (-not (Test-Path $distDir)) {
        throw "PyInstaller build directory not found at: $distDir"
    }
} finally {
    Write-Host "Restoring __init__.py to dev placeholder..." -ForegroundColor Cyan
    $initPyBackup | Set-Content -Path $initPyPath -Encoding UTF8 -NoNewline
    Add-Content -Path $initPyPath -Value "`n"
}

# 5) Clean and prepare staging directory
Write-Host "Preparing staging folder..." -ForegroundColor Cyan
if (Test-Path $stageDir) {
    Remove-Item -Path $stageDir -Recurse -Force
}
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null

# Copy build files to staging directory
Write-Host "Copying HushSnap binaries to staging directory..." -ForegroundColor Cyan
Copy-Item -Path "$distDir\*" -Destination $stageDir -Recurse -Force

# 6) Generate Visual Assets
$assetsStageDir = Join-Path $stageDir "Assets"
New-Item -ItemType Directory -Path $assetsStageDir -Force | Out-Null

Write-Host "Generating PNG visual assets from ico.ico..." -ForegroundColor Cyan
$icoPath = Join-Path $rootDir "ico.ico"
$generatorScript = Join-Path $rootDir "tools\generate_msix_assets.py"

& python.exe $generatorScript $icoPath $assetsStageDir

# 7) Compile AppxManifest.xml
Write-Host "Compiling AppxManifest.xml..." -ForegroundColor Cyan
$manifestTemplatePath = Join-Path $scriptDir "AppxManifest_template.xml"
if (-not (Test-Path $manifestTemplatePath)) {
    throw "AppxManifest template not found at: $manifestTemplatePath"
}

$manifestTemplate = Get-Content -Path $manifestTemplatePath -Raw
$manifestContent = $manifestTemplate `
    -replace '\{\{PACKAGE_NAME\}\}', $PackageName `
    -replace '\{\{PUBLISHER_ID\}\}', $Publisher `
    -replace '\{\{VERSION\}\}', $quadVersion `
    -replace '\{\{DISPLAY_NAME\}\}', $DisplayName `
    -replace '\{\{PUBLISHER_DISPLAY_NAME\}\}', $PublisherDisplayName

$manifestPath = Join-Path $stageDir "AppxManifest.xml"
$manifestContent | Set-Content -Path $manifestPath -Encoding UTF8
Write-Host "  [Created] AppxManifest.xml" -ForegroundColor Green

# 8) Packaging
if ($Sign) {
    $outputDir = Join-Path $rootDir "dist-installer-test"
    $msixFilename = "HushSnap_Test_Signed.msix"
} else {
    $msixFilename = "HushSnap.msix"
}

if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}
$msixPath = Join-Path $outputDir $msixFilename

Write-Host "Packaging staging folder into MSIX..." -ForegroundColor Cyan
& $makeappx pack /d $stageDir /p $msixPath /o
Write-Host "  [Success] MSIX package created: $msixPath" -ForegroundColor Green

# 9) Optional Local Self-Signing
if ($Sign) {
    Write-Host "Starting local signing process..." -ForegroundColor Cyan
    try {
        # Check if the user is running as Administrator (required to generate and trust certificate)
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
        $isAdmin = $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
        
        if (-not $isAdmin) {
            Write-Warning "Local signing requires administrative privileges to register and trust the certificate."
            Write-Warning "Skipping signing process. Please re-run this script in an Elevated (Admin) PowerShell window to sign."
        } else {
            $certSubject = $Publisher
            Write-Host "Looking for existing certificate with subject '$certSubject'..." -ForegroundColor Cyan
            $cert = Get-ChildItem -Path Cert:\LocalMachine\My | Where-Object { $_.Subject -eq $certSubject } | Select-Object -First 1
            
            if (-not $cert) {
                Write-Host "No matching local certificate found. Creating new self-signed certificate..." -ForegroundColor Yellow
                $cert = New-SelfSignedCertificate -Type Custom -Subject $certSubject -KeyUsage DigitalSignature -FriendlyName "HushSnap Local Test Certificate" -CertStoreLocation "Cert:\LocalMachine\My" -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3")
                Write-Host "  [Created] Self-signed certificate thumbprint: $($cert.Thumbprint)" -ForegroundColor Green
            } else {
                Write-Host "  [Found] Existing certificate thumbprint: $($cert.Thumbprint)" -ForegroundColor Green
            }
            
            # Ensure certificate is trusted by importing to both Root and TrustedPeople stores
            Write-Host "Ensuring certificate is trusted by the local machine..." -ForegroundColor Cyan
            
            $rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("Root", "LocalMachine")
            $rootStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
            $rootStore.Add($cert)
            $rootStore.Close()
            
            $peopleStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("TrustedPeople", "LocalMachine")
            $peopleStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
            $peopleStore.Add($cert)
            $peopleStore.Close()
            
            Write-Host "  [Trusted] Certificate imported into Root and Trusted People stores." -ForegroundColor Green
            
            # Sign the MSIX package
            Write-Host "Signing MSIX package with signtool..." -ForegroundColor Cyan
            & $signtool sign /fd SHA256 /s My /sm /sha1 $cert.Thumbprint $msixPath
            Write-Host "  [Success] MSIX package successfully signed!" -ForegroundColor Green
        }
    } catch {
        Write-Warning "Failed to sign MSIX package: $_"
        Write-Warning "The unsigned package at $msixPath is still valid for Partner Center submission."
    }
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "HushSnap MSIX Packaging successfully completed!" -ForegroundColor Green
Write-Host "Output File: $msixPath" -ForegroundColor Green
if (-not $Sign) {
    Write-Host "Note: This package is UNSIGNED. Perfect for uploading to Partner Center." -ForegroundColor Yellow
    Write-Host "If you want to install and test locally, run: .\installer\build_msix.ps1 -Sign (Requires Admin)" -ForegroundColor Yellow
}
Write-Host "==========================================================" -ForegroundColor Green

