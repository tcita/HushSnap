@echo off
chcp 65001 >nul
:: Sign the already-built HushSnap.msix for local testing.
:: This script does NOT recompile - it only signs the existing package.
:: Run as Administrator!
net session >nul 2>&1
if not %errorLevel% == 0 (
    echo ERROR: Please right-click and run as Administrator.
    pause
    exit /b 1
)

set MSIX_SRC=%~dp0dist-installer\HushSnap.msix
set OUT_DIR=%~dp0dist-installer-test
set MSIX_DST=%OUT_DIR%\HushSnap_Test_Signed.msix

if not exist "%MSIX_SRC%" (
    echo ERROR: dist-installer\HushSnap.msix not found.
    echo Please run build_msix.bat first to build the package.
    pause
    exit /b 1
)

echo =====================================================
echo Signing HushSnap.msix for Local Testing...
echo =====================================================
echo.

powershell -ExecutionPolicy Bypass -Command ^
    "$sdkPath = 'C:\Program Files (x86)\Windows Kits\10\bin';" ^
    "$signtool = Get-ChildItem -Path $sdkPath -Filter 'signtool.exe' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like '*\x64\*' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName;" ^
    "if (-not $signtool) { Write-Host 'ERROR: signtool.exe not found.' -ForegroundColor Red; exit 1 }" ^
    "$subject = 'CN=D80F0A50-29F3-47FE-8515-5ABF0F3E49FA';" ^
    "$cert = Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.Subject -eq $subject } | Select-Object -First 1;" ^
    "if (-not $cert) {" ^
    "    Write-Host 'No local cert found, creating new self-signed certificate...' -ForegroundColor Yellow;" ^
    "    $cert = New-SelfSignedCertificate -Type Custom -Subject $subject -KeyUsage DigitalSignature -FriendlyName 'HushSnap Local Test Cert' -CertStoreLocation 'Cert:\LocalMachine\My' -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3');" ^
    "}" ^
    "Write-Host 'Certificate thumbprint: ' $cert.Thumbprint -ForegroundColor Cyan;" ^
    "$rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root','LocalMachine');" ^
    "$rootStore.Open('ReadWrite'); $rootStore.Add($cert); $rootStore.Close();" ^
    "$peopleStore = New-Object System.Security.Cryptography.X509Certificates.X509Store('TrustedPeople','LocalMachine');" ^
    "$peopleStore.Open('ReadWrite'); $peopleStore.Add($cert); $peopleStore.Close();" ^
    "Write-Host 'Certificate trusted in Root + TrustedPeople.' -ForegroundColor Green;" ^
    "$outDir = '%OUT_DIR%';" ^
    "if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }" ^
    "Copy-Item '%MSIX_SRC%' '%MSIX_DST%' -Force;" ^
    "& $signtool sign /fd SHA256 /s My /sm /sha1 $cert.Thumbprint '%MSIX_DST%';" ^
    "if ($LASTEXITCODE -eq 0) {" ^
    "    Write-Host '';" ^
    "    Write-Host '[OK] Signed package saved to: dist-installer-test\HushSnap_Test_Signed.msix' -ForegroundColor Green;" ^
    "    Write-Host '[OK] Double-click the file above to install.' -ForegroundColor Green;" ^
    "} else { Write-Host 'ERROR: signtool failed.' -ForegroundColor Red }"

echo.
pause
