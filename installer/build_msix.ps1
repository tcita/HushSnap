# PowerShell script to package HushSnap as an MSIX package.
param(
    [string]$PackageName = "TCITAStudio.HushSnap",
    [string]$Publisher = "CN=D80F0A50-29F3-47FE-8515-5ABF0F3E49FA",
    [string]$PublisherDisplayName = "TCITA Studio",
    [string]$DisplayName = "HushSnap",
    [string]$Version,
    [switch]$SkipValidation
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

function Write-Utf8FileExact {
    param(
        [string]$Path,
        [string]$Content,
        [bool]$IncludeBom = $false
    )
    $encoding = [System.Text.UTF8Encoding]::new($IncludeBom)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Test-Utf8Bom {
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    return $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
}

function Stop-HushSnapProcessesInPaths {
    param([string[]]$RootPaths)

    $resolvedRoots = @()
    foreach ($rootPath in $RootPaths) {
        if (Test-Path $rootPath) {
            $resolvedRoots += [System.IO.Path]::GetFullPath($rootPath).TrimEnd('\') + '\'
        }
    }

    if (-not $resolvedRoots) {
        return
    }

    $stopped = 0
    Get-Process -Name "HushSnap" -ErrorAction SilentlyContinue | ForEach-Object {
        $processPath = $null
        try {
            $processPath = [System.IO.Path]::GetFullPath($_.Path)
        } catch {
            return
        }

        foreach ($root in $resolvedRoots) {
            if ($processPath.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
                Write-Host "Stopping HushSnap process using build output: $processPath" -ForegroundColor Yellow
                $_ | Stop-Process -Force
                $stopped++
                break
            }
        }
    }

    if ($stopped -gt 0) {
        Start-Sleep -Milliseconds 800
    }
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

        # Extract excluded packages from the spec
        $excludesMatches = [regex]::Matches($specContent, "(?s)excludes\s*=\s*\[(.*?)\]")
        $excludesContent = if ($excludesMatches.Count -gt 0) { $excludesMatches[0].Groups[1].Value } else { "" }

        foreach ($pkg in $sourcePkgs) {
            $inHiddenimports = $specContent -match [regex]::Escape($pkg)
            $inExcludes = $excludesContent -match [regex]::Escape($pkg)
            if (-not $inHiddenimports -and -not $inExcludes) {
                Write-Fail "Package '$pkg' exists in source but is NOT in .spec hiddenimports (or excludes)"
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
        }
        Write-Pass "All __init__.py files present in source tree"
    }

    # 1.3 ── __init__.py version is clean "dev" (not leaked from crash) ─
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

    # 1.4 ── Critical Python imports resolve ────────────────────────
    $criticalModules = @("rapidocr", "onnxruntime", "PyQt6", "numpy", "winrt")
    foreach ($mod in $criticalModules) {
        $result = & python.exe -c "import $mod" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Cannot import '$mod': $result"
            $errors++
        }
    }
    Write-Pass "All critical Python modules importable"

    # 1.5 ── Git working tree status (warning only) ─────────────────
    $gitStatus = & git -C $RootDir status --porcelain 2>$null
    if ($gitStatus) {
        Write-Warn "Git working tree has uncommitted changes:"
        $gitStatus | ForEach-Object { Write-Host "          $_" -ForegroundColor Yellow }
        $warnings++
    } else {
        Write-Pass "Git working tree is clean"
    }

    # 1.6 ── HEAD must be exactly at a git tag for release ──────────
    $exactTag = & git -C $RootDir describe --tags --exact-match 2>$null
    if (-not $exactTag) {
        $nearestTag = & git -C $RootDir describe --tags --abbrev=0 2>$null
        if ($nearestTag) {
            $aheadCount = & git -C $RootDir rev-list --count "${nearestTag}..HEAD" 2>$null
            Write-Fail "HEAD is ${aheadCount} commit(s) ahead of tag ${nearestTag}. Tag the current HEAD before packaging: git tag -a vX.Y.Z -m 'release vX.Y.Z'"
        } else {
            Write-Fail "No git tag found. Create one: git tag -a v0.3.0 -m 'release v0.3.0'"
        }
        $errors++
    } else {
        Write-Pass "HEAD is exactly at git tag: $exactTag"
    }

    # 1.7 ── Default config template has debug = false ─────────────
    $configPyPath = Join-Path $pkgDir "config.py"
    $debugLine = Select-String -Path $configPyPath -Pattern '"debug"\s*:\s*False' | Select-Object -First 1
    $debugNotFrozen = Select-String -Path $configPyPath -Pattern '"debug"\s*:\s*not\s+_is_frozen' | Select-Object -First 1
    if ($debugLine -or $debugNotFrozen) {
        Write-Pass "Default config template: debug = false (or via not _is_frozen)"
    } else {
        $debugTrue = Select-String -Path $configPyPath -Pattern '"debug"\s*:\s*True' | Select-Object -First 1
        if ($debugTrue) {
            Write-Fail "Default config template has debug = True — should be False for release"
            $errors++
        } else {
            Write-Fail "Could not verify 'debug = false' in config.py _ensure_default_config_exists()"
            $errors++
        }
    }

    # 1.8 ── No runtime config/state files leaked into project ─────
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

    # 1.9 ── No .log files in project tree ──────────────────────────
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

    # 1.10 ── No debug/artifact files in project tree ───────────────
    # Exclude build\crashlib\ — crashlib's diagnostic symbols (crashlib.pdb) and
    # the MSVC linker's collateral vc140.pdb land there after build_crashlib.bat.
    # They are never bundled (the .spec does not reference crashlib at all) and
    # crashlib.pdb is needed by WinDbg symbol resolution
    # (see scripts/NATIVE_CRASH_DEBUGGING.md), so they are intentional, not stray
    # debug artifacts to clean up. This keeps the standalone crashlib tool from
    # blocking the MSIX build when it has been built locally.
    $debugArtifacts = Get-ChildItem -Path $RootDir -Recurse -Include @("ocr_debug_*.png", "*.pdb") -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\build\\crashlib\\' }
    if ($debugArtifacts) {
        foreach ($f in $debugArtifacts) {
            Write-Fail "Debug artifact found: $($f.FullName) — delete before packaging"
            $errors++
        }
    } else {
        Write-Pass "No debug artifacts (ocr_debug_*.png, *.pdb) in project tree"
    }

    # 1.11 ── Orphan .pyc files outside __pycache__ (stale cache) ──
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

    # 1.12 ── __pycache__ directories are present (info only) ───────
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
        if ($exeSizeMB -lt 7) {
            Write-Fail "HushSnap.exe is too small: ${exeSizeMB}MB (expected >7MB)"
            $errors++
        } else {
            Write-Pass "HushSnap.exe: ${exeSizeMB}MB"
        }
    }

    # 2.2 ── onnxruntime DLLs bundled ────────────────────────────────
    $onnxPatterns = @("*onnxruntime*", "*onnxruntime*.dll")
    $onnxFound = Get-ChildItem -Path $DistDir -Recurse -Include $onnxPatterns -ErrorAction SilentlyContinue
    if (-not $onnxFound) {
        Write-Fail "onnxruntime DLLs not found in dist — PP-OCR will fail at runtime"
        $errors++
    } else {
        Write-Pass "onnxruntime DLLs present ($($onnxFound.Count) files)"
    }

    # 2.3 ── PP-OCR model files bundled ────────────────────────────
    # Locked to exactly 2 onnx: det_small + rec_small. The cls (direction
    # classifier) model is stripped by .spec filter_datas - use_cls=False
    # at runtime, so it is never loaded. If the count ever changes (a 3rd
    # model slipped past the filter, or one went missing) it is a
    # release-blocking event that must be acknowledged by editing this
    # assertion, not silently packaged. A same-count model swap (e.g.
    # det_small -> det_server ~100MB) is caught by the size cap.
    $modelDir = Join-Path $DistDir "_internal\rapidocr\models"
    $modelFiles = Get-ChildItem -Path $modelDir -Filter "*.onnx" -ErrorAction SilentlyContinue
    if (-not $modelFiles -or $modelFiles.Count -ne 2) {
        $found = if ($modelFiles) { $modelFiles.Name -join ', ' } else { '(none)' }
        Write-Fail "PP-OCR onnx count != 2 (found $($modelFiles.Count)): $found"
        $errors++
    } else {
        Write-Pass "PP-OCR model files present ($($modelFiles.Count) .onnx files)"
    }

    # 2.3b ── PP-OCR model total size cap (catches same-count swap) ─
    if ($modelFiles -and $modelFiles.Count -eq 2) {
        $onnxTotalMB = [math]::Round(($modelFiles | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
        if ($onnxTotalMB -gt 50) {
            Write-Fail "PP-OCR onnx total ${onnxTotalMB}MB > 50MB cap - a small model was likely swapped for a server variant"
            $errors++
        } else {
            Write-Pass "PP-OCR onnx total size: ${onnxTotalMB}MB"
        }
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
    if ($totalSizeMB -lt 150) {
        Write-Fail "Dist total size too small: ${totalSizeMB}MB (expected >150MB)"
        $errors++
    } elseif ($totalSizeMB -gt 350) {
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

# ══════════════════════════════════════════════════════════════════════
#  1. Locate makeappx.exe from the Windows SDK
# ══════════════════════════════════════════════════════════════════════

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

# ══════════════════════════════════════════════════════════════════════
#  2. Paths
# ══════════════════════════════════════════════════════════════════════

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Resolve-Path (Join-Path $scriptDir "..")
$distDir = Join-Path $rootDir "dist\HushSnap"
$stageDir = Join-Path $rootDir "build\msix_stage"

# ══════════════════════════════════════════════════════════════════════
#  3. Version resolution
# ══════════════════════════════════════════════════════════════════════

if (-not $Version) {
    $exactTag = & git -C $rootDir describe --tags --exact-match 2>$null
    if ($exactTag) {
        $rawVersion = $exactTag.TrimStart('v')
        Write-Host "Extracted version '$rawVersion' from exact git tag '$exactTag'" -ForegroundColor Green
    } else {
        $nearestTag = & git -C $rootDir describe --tags --abbrev=0 2>$null
        if ($nearestTag) {
            $aheadCount = & git -C $rootDir rev-list --count "${nearestTag}..HEAD" 2>$null
            Write-Host ""
            Write-Host "==========================================================" -ForegroundColor Red
            Write-Host "  ERROR: HEAD is ${aheadCount} commit(s) ahead of tag" -ForegroundColor Red
            Write-Host "  Nearest tag: ${nearestTag}" -ForegroundColor Red
            Write-Host "  Tag the current HEAD first:" -ForegroundColor Red
            Write-Host "    git tag -a vX.Y.Z -m 'release vX.Y.Z'" -ForegroundColor Red
            Write-Host "  Or override: build_msix.ps1 -Version 'X.Y.Z'" -ForegroundColor Red
            Write-Host "==========================================================" -ForegroundColor Red
            Write-Host ""
            throw "Version resolution failed: HEAD is not at a git tag."
        } else {
            Write-Host ""
            Write-Host "==========================================================" -ForegroundColor Red
            Write-Host "  ERROR: No git tag found in repository!" -ForegroundColor Red
            Write-Host "  Create one first: git tag -a v0.3.0 -m 'release v0.3.0'" -ForegroundColor Red
            Write-Host "  Or override: build_msix.ps1 -Version '0.3.0'" -ForegroundColor Red
            Write-Host "==========================================================" -ForegroundColor Red
            Write-Host ""
            throw "Version resolution failed: no git tag found."
        }
    }
} else {
    $rawVersion = $Version
    Write-Host "Using provided version '$rawVersion'" -ForegroundColor Green
}

# Convert SemVer → Quad (MSIX requires 4 parts)
$versionParts = $rawVersion -split '\.'
while ($versionParts.Count -lt 4) {
    $versionParts += "0"
}
$quadVersion = ($versionParts[0..3] -join ".")
Write-Host "Configured MSIX package version to '$quadVersion'" -ForegroundColor Green

# ══════════════════════════════════════════════════════════════════════
#  CHECKPOINT 1 — Pre-build validation
# ══════════════════════════════════════════════════════════════════════

if (-not $SkipValidation) {
    Invoke-PreBuildValidation -RootDir $rootDir -SpecPath (Join-Path $rootDir "HushSnap.spec")
}

# ══════════════════════════════════════════════════════════════════════
#  4. PyInstaller build
# ══════════════════════════════════════════════════════════════════════

$initPyPath = Join-Path $rootDir "hushsnap\__init__.py"

# Clean previous dist output (PyInstaller --clean handles the analysis
# cache; we only need to ensure the output directory is fresh).
if (Test-Path $distDir) {
    Remove-Item -Path $distDir -Recurse -Force -ErrorAction SilentlyContinue
}

# Inject version into __init__.py, build, then restore.
$initPyHadBom = Test-Utf8Bom -Path $initPyPath
$initPyBackup = Get-Content -Path $initPyPath -Raw
try {
    Write-Host "Injecting version '$rawVersion' into __init__.py for build..." -ForegroundColor Cyan
    $patchedInit = $initPyBackup -replace '__version__\s*=\s*"[^"]*"', "__version__ = `"$rawVersion`""
    Write-Utf8FileExact -Path $initPyPath -Content $patchedInit -IncludeBom $initPyHadBom

    Write-Host "Building HushSnap with PyInstaller (clean)..." -ForegroundColor Cyan

    Stop-HushSnapProcessesInPaths -RootPaths @($distDir, $stageDir)

    $specPath = Join-Path $rootDir "HushSnap.spec"
    & pyinstaller "--noconfirm", "--clean", $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }

    if (-not (Test-Path $distDir)) {
        throw "PyInstaller build directory not found at: $distDir"
    }
} finally {
    Write-Host "Restoring __init__.py to dev placeholder..." -ForegroundColor Cyan
    Write-Utf8FileExact -Path $initPyPath -Content $initPyBackup -IncludeBom $initPyHadBom
}

# ══════════════════════════════════════════════════════════════════════
#  CHECKPOINT 2 — Post-PyInstaller validation
# ══════════════════════════════════════════════════════════════════════

if (-not $SkipValidation) {
    Invoke-PostPyInstallerValidation -DistDir $distDir
}

# ══════════════════════════════════════════════════════════════════════
#  5. Prepare staging directory
# ══════════════════════════════════════════════════════════════════════

Write-Host "Preparing staging folder..." -ForegroundColor Cyan
if (Test-Path $stageDir) {
    $retryCount = 0
    $maxRetries = 5
    while ($true) {
        try {
            Remove-Item -Path $stageDir -Recurse -Force -ErrorAction Stop
            break
        } catch {
            if (++$retryCount -ge $maxRetries) {
                throw "Failed to remove staging directory after $maxRetries attempts: $_"
            }
            Write-Host "  Retry $retryCount/$maxRetries — file in use, waiting 2s..." -ForegroundColor Yellow
            Start-Sleep -Seconds 2
        }
    }
}
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null

Copy-Item -Path "$distDir\*" -Destination $stageDir -Recurse -Force

# ══════════════════════════════════════════════════════════════════════
#  6. Generate visual assets
# ══════════════════════════════════════════════════════════════════════

$assetsStageDir = Join-Path $stageDir "Assets"
New-Item -ItemType Directory -Path $assetsStageDir -Force | Out-Null

Write-Host "Generating PNG visual assets from ico.ico..." -ForegroundColor Cyan

# Regenerate hushsnap.ico from assets/logo.png before deriving the store
# tiles from it. This keeps the rounded-square mask (transparent corners)
# as the single source of truth — the .ico in the repo is a build artifact,
# refreshed here so the build never ships a stale pre-rounded icon. The
# taskbar / tray read this same .ico at runtime (bundled by PyInstaller),
# so taskbar, tray, and store all stay in sync.
$iconGeneratorScript = Join-Path $rootDir "scripts\generate_icon.py"
$logoSource = Join-Path $rootDir "assets\logo.png"
if (Test-Path $iconGeneratorScript) {
    Write-Host "  regenerating hushsnap.ico from $logoSource (rounded corners)..." -ForegroundColor DarkGray
    & python.exe $iconGeneratorScript --source $logoSource | Out-Null
} else {
    Write-Host "  [warn] scripts\generate_icon.py not found — using existing hushsnap.ico as-is" -ForegroundColor Yellow
}

$icoPath = Join-Path $rootDir "hushsnap.ico"
$generatorScript = Join-Path $rootDir "installer\generate_msix_assets.py"
& python.exe $generatorScript $icoPath $assetsStageDir

# ══════════════════════════════════════════════════════════════════════
#  7. Compile AppxManifest.xml
# ══════════════════════════════════════════════════════════════════════

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

# ══════════════════════════════════════════════════════════════════════
#  8. Package MSIX
# ══════════════════════════════════════════════════════════════════════

$outputDir = Join-Path $rootDir "dist-installer"
$msixFilename = "HushSnap.msix"

if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}
$msixPath = Join-Path $outputDir $msixFilename

# Remove stale .msix first — MakeAppx /o can silently fail if the file is
# locked (e.g. Windows Explorer is viewing the folder).
if (Test-Path $msixPath) {
    Remove-Item -Path $msixPath -Force -ErrorAction Stop
    Write-Host "Removed previous MSIX: $msixPath" -ForegroundColor Cyan
}

# Compile resources.pri inside $stageDir so Windows Modern Resource Technology (MRT)
# can index and resolve alternative visual assets (specifically the targetsize-xx_altform-unplated.png
# files used to render transparent corners on the taskbar without BackgroundColor plating).
$makepri = Join-Path (Split-Path $makeappx) "makepri.exe"
if (Test-Path $makepri) {
    Write-Host "Compiling resources.pri..." -ForegroundColor Cyan
    $configFile = Join-Path $stageDir "priconfig.xml"
    $priFile = Join-Path $stageDir "resources.pri"
    
    # 1. Generate temp config
    & $makepri createconfig /cf $configFile /dq "lang-en-US" /pv "10.0.0" /o | Out-Null
    
    # 2. Compile resources.pri into the stage directory
    & $makepri new /pr $stageDir /cf $configFile /of $priFile /o | Out-Null
    
    # 3. Clean up the temp config
    if (Test-Path $configFile) {
        Remove-Item -Path $configFile -Force
    }
    Write-Host "  [Success] resources.pri compiled successfully" -ForegroundColor Green
} else {
    Write-Warning "makepri.exe not found next to makeappx.exe. Skipping PRI compilation."
}

Write-Host "Packaging staging folder into MSIX..." -ForegroundColor Cyan
& $makeappx pack /d $stageDir /p $msixPath
if ($LASTEXITCODE -ne 0) {
    throw "MakeAppx failed with exit code $LASTEXITCODE"
}
Write-Host "  [Success] MSIX package created: $msixPath" -ForegroundColor Green

# ══════════════════════════════════════════════════════════════════════
#  CHECKPOINT 3 — Post-MSIX validation
# ══════════════════════════════════════════════════════════════════════

if (-not $SkipValidation) {
    Invoke-PostMSIXValidation -MsixPath $msixPath -StageDir $stageDir
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "HushSnap MSIX Packaging successfully completed!" -ForegroundColor Green
Write-Host "Output File: $msixPath" -ForegroundColor Green
Write-Host "Note: This package is UNSIGNED. Sign it before local testing:" -ForegroundColor Yellow
Write-Host "  SignTool sign /fd SHA256 /a /f <cert>.pfx /p <password> $msixPath" -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green
