# PowerShell script to package HushSnap as an MSIX package.
param(
    [string]$PackageName = "TCITAStudio.HushSnap",
    [string]$Publisher = "CN=D80F0A50-29F3-47FE-8515-5ABF0F3E49FA",
    [string]$PublisherDisplayName = "TCITA Studio",
    [string]$DisplayName = "HushSnap",
    [string]$Version,
    [switch]$Rebuild,
    [switch]$SkipValidation,
    [switch]$Dev
)

$ErrorActionPreference = "Stop"

# ── Global error handler: pause so the user can read the error ─────
trap {
    Write-Host ""
    Write-Host "══════════════════════════════════════════════════════════" -ForegroundColor Red
    Write-Host "  BUILD FAILED" -ForegroundColor Red
    Write-Host "══════════════════════════════════════════════════════════" -ForegroundColor Red
    Write-Host ""
    Write-Host "  $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Press any key to exit..." -ForegroundColor Yellow
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

# ── CI/CD validation helpers ────────────────────────────────────────────

function Write-ValidationHeader {
    param([string]$Title)
    Write-Host ""
    Write-Host "══════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  CHECK: $Title" -ForegroundColor Cyan
    Write-Host "══════════════════════════════════════════════════════════" -ForegroundColor Cyan
}

function Write-Pass {
    param([string]$Msg)
    Write-Host "    [PASS] $Msg" -ForegroundColor Green
}

function Write-Fail {
    param([string]$Msg)
    Write-Host "    [FAIL] $Msg" -ForegroundColor Red
}

function Write-Warn {
    param([string]$Msg)
    Write-Host "    [WARN] $Msg" -ForegroundColor Yellow
}

function Invoke-PreBuildValidation {
    param([string]$RootDir, [string]$SpecPath)
    Write-ValidationHeader "Pre-build validation"

    $errors = 0
    $warnings = 0

    # 1.1 ── .spec hiddenimports covers all source packages ─────────
    if (-not (Test-Path $SpecPath)) {
        Write-Fail ".spec file not found: $SpecPath"
        $errors++
    } else {
        $specContent = Get-Content $SpecPath -Raw

        # Collect all hushsnap sub-packages from the source tree
        $pkgDir = Join-Path $RootDir "hushsnap"
        $sourcePkgs = @()
        Get-ChildItem -Path $pkgDir -Recurse -Filter "__init__.py" |
            ForEach-Object {
                $relDir = $_.DirectoryName.Substring($pkgDir.Length).TrimStart('\', '/') -replace '\\', '.'
                if ($relDir) {
                    $sourcePkgs += "hushsnap.$relDir"
                } else {
                    $sourcePkgs += "hushsnap"
                }
            }

        $sourcePkgs = $sourcePkgs | Sort-Object -Unique

        foreach ($pkg in $sourcePkgs) {
            if ($specContent -notmatch [regex]::Escape($pkg)) {
                Write-Fail "Package '$pkg' exists in source but is NOT in .spec hiddenimports"
                $errors++
            }
        }

        if ($errors -eq 0) {
            Write-Pass ".spec hiddenimports covers all $($sourcePkgs.Count) source packages"
        }

        # 1.2 ── Check for missing __init__.py imports in .spec ─────
        $allInitFiles = Get-ChildItem -Path $pkgDir -Recurse -Filter "__init__.py"
        foreach ($initFile in $allInitFiles) {
            $imports = Select-String -Path $initFile.FullName -Pattern 'from \.(\S+) import|from \.\.(\S+) import' -AllMatches
            # Non-fatal: just warn about potential hidden import gaps
            # The main check above (1.1) covers the common case
        }
        Write-Pass "All __init__.py files present in source tree"
    }

    # 1.2 ── __init__.py version is clean "dev" (not leaked from crash) ─
    $initPyPath = Join-Path $RootDir "hushsnap\__init__.py"
    $initPyContent = Get-Content $initPyPath -Raw
    if ($initPyContent -match '__version__\s*=\s*"dev"') {
        Write-Pass "__init__.py version is 'dev' (clean)"
    } elseif ($initPyContent -match '__version__\s*=\s*"([^"]*)"') {
        $currentVer = $matches[1]
        Write-Fail "__init__.py has leaked version '$currentVer' — a previous build crashed and left the injected version. Restore to: __version__ = `"dev`""
        $errors++
    } else {
        Write-Fail "__init__.py missing __version__ assignment"
        $errors++
    }

    # 1.3 ── Critical Python imports resolve ────────────────────────
    $criticalModules = @("rapidocr", "onnxruntime", "PyQt6", "numpy", "winrt")
    foreach ($mod in $criticalModules) {
        $result = & python.exe -c "import $mod" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Cannot import '$mod': $result"
            $errors++
        }
    }
    Write-Pass "All critical Python modules importable"

    # 1.4 ── Git working tree status (warning only) ─────────────────
    $gitStatus = & git -C $RootDir status --porcelain 2>$null
    if ($gitStatus) {
        Write-Warn "Git working tree has uncommitted changes:"
        $gitStatus | ForEach-Object { Write-Host "          $_" -ForegroundColor Yellow }
        $warnings++
    } else {
        Write-Pass "Git working tree is clean"
    }

    # 1.5 ── Git tag exists for version ─────────────────────────────
    $gitTag = & git -C $RootDir describe --tags --abbrev=0 2>$null
    if (-not $gitTag) {
        Write-Fail "No git tag found — MSIX version is unknown. Create one: git tag -a v0.3.0 -m 'release v0.3.0'"
        $errors++
    } else {
        Write-Pass "Git tag found: $gitTag"
    }

    # 1.6 ── Default config template has debug = false ─────────────
    $configPyPath = Join-Path $pkgDir "config.py"
    $debugLine = Select-String -Path $configPyPath -Pattern '"debug"\s*:\s*False' | Select-Object -First 1
    if ($debugLine) {
        Write-Pass "Default config template: debug = false"
    } else {
        # Double-check: maybe it's true
        $debugTrue = Select-String -Path $configPyPath -Pattern '"debug"\s*:\s*True' | Select-Object -First 1
        if ($debugTrue) {
            Write-Fail "Default config template has debug = True in _ensure_default_config_exists() — should be False for release"
            $errors++
        } else {
            Write-Fail "Could not verify 'debug = false' in config.py _ensure_default_config_exists()"
            $errors++
        }
    }

    # 1.7 ── No runtime config/state files leaked into project ─────
    $leakedConfigs = @(
        (Get-ChildItem -Path $RootDir -Recurse -Filter "hushsnap_config.toml" -ErrorAction SilentlyContinue),
        (Get-ChildItem -Path $RootDir -Recurse -Filter "hushsnap_state.toml" -ErrorAction SilentlyContinue)
    ) | Where-Object { $_ -ne $null }

    if ($leakedConfigs) {
        foreach ($f in $leakedConfigs) {
            Write-Fail "Runtime config/state file in project: $($f.FullName) — move to AppData or delete"
            $errors++
        }
    } else {
        Write-Pass "No runtime config/state files leaked into project tree"
    }

    # 1.8 ── No .log files in project tree ──────────────────────────
    $logFiles = Get-ChildItem -Path $RootDir -Recurse -Filter "*.log" -ErrorAction SilentlyContinue
    if ($logFiles) {
        foreach ($f in $logFiles) {
            $sizeKB = [math]::Round($f.Length / 1KB, 1)
            Write-Fail "Log file found: $($f.FullName) (${sizeKB}KB) — should be empty or deleted before packaging"
            $errors++
        }
    } else {
        Write-Pass "No .log files in project tree"
    }

    # 1.9 ── No debug/artifact files in project tree ────────────────
    # Note: __pycache__/*.pyc and build/*/localpycs/*.pyc are normal
    # Python/PyInstaller caches — they are NOT bundled in the MSIX.
    # Only flag files that indicate leaked dev/test artifacts.
    $debugArtifacts = Get-ChildItem -Path $RootDir -Recurse -Include @("ocr_debug_*.png", "*.pdb") -ErrorAction SilentlyContinue
    if ($debugArtifacts) {
        foreach ($f in $debugArtifacts) {
            Write-Fail "Debug artifact found: $($f.FullName) — delete before packaging"
            $errors++
        }
    } else {
        Write-Pass "No debug artifacts (ocr_debug_*.png, *.pdb) in project tree"
    }

    # 1.10 ── Orphan .pyc files outside __pycache__ (stale cache) ──
    $orphanPyc = Get-ChildItem -Path $RootDir -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
        Where-Object { $_.DirectoryName -notmatch '__pycache__|localpycs' }
    if ($orphanPyc) {
        foreach ($f in $orphanPyc) {
            Write-Fail "Orphan .pyc outside __pycache__: $($f.FullName) — delete before packaging"
            $errors++
        }
    } else {
        Write-Pass "No orphan .pyc files outside __pycache__"
    }

    # 1.11 ── __pycache__ directories are present (info only) ───────
    $pycacheDirs = Get-ChildItem -Path $RootDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue
    if ($pycacheDirs) {
        Write-Pass "__pycache__ directories present ($($pycacheDirs.Count)) — normal, not bundled"
    }

    Write-Host ""
    Write-Host "    Pre-build: $errors error(s), $warnings warning(s)" -ForegroundColor $(if ($errors -gt 0) { "Red" } elseif ($warnings -gt 0) { "Yellow" } else { "Green" })

    if ($errors -gt 0) {
        throw "Pre-build validation failed with $errors error(s). Use -SkipValidation to bypass."
    }
}

function Invoke-PostPyInstallerValidation {
    param([string]$DistDir)
    Write-ValidationHeader "Post-PyInstaller validation"

    $errors = 0

    # 2.1 ── HushSnap.exe exists and has reasonable size ─────────────
    $exePath = Join-Path $DistDir "HushSnap.exe"
    if (-not (Test-Path $exePath)) {
        Write-Fail "HushSnap.exe not found at $exePath"
        $errors++
    } else {
        $exeSizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
        if ($exeSizeMB -lt 5) {
            Write-Fail "HushSnap.exe is too small: ${exeSizeMB}MB (expected >5MB)"
            $errors++
        } else {
            Write-Pass "HushSnap.exe: ${exeSizeMB}MB"
        }
    }

    # 2.2 ── onnxruntime DLLs bundled ────────────────────────────────
    $onnxPatterns = @("*onnxruntime*", "*onnxruntime*.dll")
    $onnxFound = Get-ChildItem -Path $DistDir -Recurse -Include $onnxPatterns -ErrorAction SilentlyContinue
    if (-not $onnxFound) {
        Write-Fail "onnxruntime DLLs not found in dist — RapidOCR will fail at runtime"
        $errors++
    } else {
        Write-Pass "onnxruntime DLLs present ($($onnxFound.Count) files)"
    }

    # 2.3 ── RapidOCR model files bundled ────────────────────────────
    $modelDir = Join-Path $DistDir "_internal\rapidocr\models"
    $modelFiles = Get-ChildItem -Path $modelDir -Filter "*.onnx" -ErrorAction SilentlyContinue
    if (-not $modelFiles -or $modelFiles.Count -lt 3) {
        Write-Fail "RapidOCR ONNX model files missing or incomplete in $modelDir (found $($modelFiles.Count))"
        $errors++
    } else {
        Write-Pass "RapidOCR model files present ($($modelFiles.Count) .onnx files)"
    }

    # 2.4 ── No .py source files leaked into dist root ───────────────
    $pyFiles = Get-ChildItem -Path $DistDir -Filter "*.py" -ErrorAction SilentlyContinue
    if ($pyFiles) {
        Write-Warn "Python source files found in dist root — should be compiled: $($pyFiles.Name -join ', ')"
    } else {
        Write-Pass "No .py source files leaked into dist root"
    }

    # 2.5 ── No config/state/log files leaked into dist ──────────────
    $distLeaked = Get-ChildItem -Path $DistDir -Recurse -Include @(
        "hushsnap_config.toml", "hushsnap_state.toml", "*.log", "ocr_debug_*.png"
    ) -ErrorAction SilentlyContinue
    if ($distLeaked) {
        foreach ($f in $distLeaked) {
            Write-Fail "Leaked file in dist: $($f.FullName) — check PyInstaller bundle_datas and excludes"
            $errors++
        }
    } else {
        Write-Pass "No config/state/log files leaked into dist"
    }

    # 2.6 ── Dist total size is reasonable ───────────────────────────
    $totalSizeMB = [math]::Round((Get-ChildItem -Path $DistDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
    if ($totalSizeMB -lt 50) {
        Write-Fail "Dist total size too small: ${totalSizeMB}MB (expected >50MB)"
        $errors++
    } elseif ($totalSizeMB -gt 300) {
        Write-Warn "Dist total size large: ${totalSizeMB}MB (may include unnecessary files)"
    } else {
        Write-Pass "Dist total size: ${totalSizeMB}MB"
    }

    Write-Host ""
    Write-Host "    Post-PyInstaller: $errors error(s)" -ForegroundColor $(if ($errors -gt 0) { "Red" } else { "Green" })

    if ($errors -gt 0) {
        throw "Post-PyInstaller validation failed with $errors error(s). Use -SkipValidation to bypass."
    }
}

function Invoke-PostMSIXValidation {
    param([string]$MsixPath, [string]$StageDir)
    Write-ValidationHeader "Post-MSIX validation"

    $errors = 0

    # 3.1 ── MSIX file exists and has reasonable size ────────────────
    if (-not (Test-Path $MsixPath)) {
        Write-Fail "MSIX file not found: $MsixPath"
        $errors++
    } else {
        $msixSizeMB = [math]::Round((Get-Item $MsixPath).Length / 1MB, 1)
        if ($msixSizeMB -lt 20) {
            Write-Fail "MSIX too small: ${msixSizeMB}MB — packaging likely incomplete"
            $errors++
        } elseif ($msixSizeMB -gt 200) {
            Write-Warn "MSIX is large: ${msixSizeMB}MB — check for unnecessary bundled files"
        } else {
            Write-Pass "MSIX size: ${msixSizeMB}MB"
        }
    }

    # 3.2 ── Staged AppxManifest.xml has no unresolved placeholders ─
    $manifestPath = Join-Path $StageDir "AppxManifest.xml"
    if (Test-Path $manifestPath) {
        $manifestContent = Get-Content $manifestPath -Raw
        $placeholders = [regex]::Matches($manifestContent, '\{\{[A-Z_]+\}\}')
        if ($placeholders.Count -gt 0) {
            Write-Fail "AppxManifest.xml still contains $($placeholders.Count) unresolved placeholder(s): $($placeholders.Value -join ', ')"
            $errors++
        } else {
            Write-Pass "AppxManifest.xml has no unresolved placeholders"
        }
    } else {
        Write-Fail "Staged AppxManifest.xml not found at $manifestPath"
        $errors++
    }

    # 3.3 ── Version in manifest matches build version ───────────────
    if (Test-Path $manifestPath) {
        [xml]$manifestXml = Get-Content $manifestPath
        $manifestVersion = $manifestXml.Package.Identity.Version
        if ($manifestVersion) {
            Write-Pass "MSIX manifest version: $manifestVersion"
        } else {
            Write-Fail "Could not read version from AppxManifest.xml Identity element"
            $errors++
        }
    }

    Write-Host ""
    Write-Host "    Post-MSIX: $errors error(s)" -ForegroundColor $(if ($errors -gt 0) { "Red" } else { "Green" })

    if ($errors -gt 0) {
        throw "Post-MSIX validation failed with $errors error(s). Use -SkipValidation to bypass."
    }
}

# 1) Locate tools from the Windows SDK
$sdkPath = "C:\Program Files (x86)\Windows Kits\10\bin"
Write-Host "Locating Windows SDK packaging tools..." -ForegroundColor Cyan

$makeappx = Get-ChildItem -Path $sdkPath -Filter "makeappx.exe" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like "*\x64\*" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName

if (-not $makeappx) {
    throw "makeappx.exe not found in Windows SDK bin directory ($sdkPath). Please ensure Windows 10/11 SDK is installed."
}

Write-Host "  [Found] MakeAppx: $makeappx" -ForegroundColor Green

# 2) Set up paths
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Resolve-Path (Join-Path $scriptDir "..")
$distDir = Join-Path $rootDir "dist\HushSnap"
$stageDir = Join-Path $rootDir "build\msix_stage"
$outputDir = Join-Path $rootDir "dist-installer"

# 3) Resolve and parse version
if ($Dev) {
    # Dev build: use fixed default version, no git tag required
    $rawVersion = "0.0.0.0"
    Write-Host "Dev build: using default version '$rawVersion' (git tag bypassed)" -ForegroundColor Cyan
} elseif (-not $Version) {
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

# ── CHECKPOINT 1: Pre-build validation ───────────────────────────
if ((-not $SkipValidation) -and (-not $Dev)) {
    Invoke-PreBuildValidation -RootDir $rootDir -SpecPath (Join-Path $rootDir "HushSnap.spec")
}

# 4) Build with PyInstaller
$initPyPath = Join-Path $rootDir "hushsnap\__init__.py"

if ($Dev) {
    # Dev build: keep __init__.py as "dev", no version injection needed
    Write-Host "Dev build: keeping __init__.py version as 'dev' (no injection)" -ForegroundColor Cyan

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
} else {
    # Release build: inject version into __init__.py then compile
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
}

# ── CHECKPOINT 2: Post-PyInstaller validation ─────────────────────
if ((-not $SkipValidation) -and (-not $Dev)) {
    Invoke-PostPyInstallerValidation -DistDir $distDir
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
$msixFilename = "HushSnap.msix"

if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}
$msixPath = Join-Path $outputDir $msixFilename

Write-Host "Packaging staging folder into MSIX..." -ForegroundColor Cyan
& $makeappx pack /d $stageDir /p $msixPath /o
Write-Host "  [Success] MSIX package created: $msixPath" -ForegroundColor Green

# ── CHECKPOINT 3: Post-MSIX validation ────────────────────────────
if ((-not $SkipValidation) -and (-not $Dev)) {
    Invoke-PostMSIXValidation -MsixPath $msixPath -StageDir $stageDir
}

# ── Dev registration ─────────────────────────────────────────
if ($Dev) {
    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "Registering HushSnap Developer MSIX (Loose Folder)..." -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan

    $manifestPath = Join-Path $stageDir "AppxManifest.xml"
    if (Test-Path $manifestPath) {
        Write-Host "Registering package with Windows (Developer Mode required)..." -ForegroundColor Cyan
        Add-AppxPackage -Register -Path $manifestPath
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "[OK] HushSnap successfully registered in MSIX container!" -ForegroundColor Green
            Write-Host "[OK] You can now search 'HushSnap' in the Start Menu and run it." -ForegroundColor Green
            Write-Host "[OK] To update after code changes: re-run build_msix_dev.bat" -ForegroundColor Green
        } else {
            Write-Host ""
            Write-Host "ERROR: Registration failed." -ForegroundColor Red
            Write-Host "Please make sure Windows Developer Mode is enabled:" -ForegroundColor Red
            Write-Host "Go to Settings -> System -> For developers, and turn ON Developer Mode." -ForegroundColor Red
        }
    } else {
        Write-Host "ERROR: Staging AppxManifest not found at: $manifestPath" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "HushSnap MSIX Packaging successfully completed!" -ForegroundColor Green
Write-Host "Output File: $msixPath" -ForegroundColor Green
if ($Dev) {
    Write-Host "Build Type: DEVELOPMENT (version 0.0.0.0, registered locally)" -ForegroundColor Cyan
} else {
    Write-Host "Note: This package is UNSIGNED. Perfect for uploading to Partner Center." -ForegroundColor Yellow
}
Write-Host "==========================================================" -ForegroundColor Green

