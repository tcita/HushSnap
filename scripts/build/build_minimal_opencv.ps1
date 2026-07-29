<#
.SYNOPSIS
  Build a minimal static cv2.pyd (core + imgproc + imgcodecs only) for HushSnap.

.DESCRIPTION
  The official opencv-python wheel ships an 82 MB single-file cv2.pyd with
  ~90% of OpenCV's modules (dnn/ml/video/features2d/calib3d/...) that rapidocr
  never touches.  rapidocr is the only runtime cv2 consumer, and it uses ~30
  symbols, all in core/imgproc/imgcodecs (audited by tests/test_cv2_symbol_audit.py).
  PyInstaller excludes cannot help (one monolithic .pyd, not a split package).

  This script compiles OpenCV from source with BUILD_LIST=core,imgproc,imgcodecs,
  python3 and BUILD_SHARED_LIBS=OFF, yielding a ~47 MB single-file static cv2.pyd
  (validated end-to-end 2026-07-28: 30/30 symbols, OCR output identical to the
  82 MB wheel across CN/EN/code/classical-CN test images).

  Toolchain (install once, see memory/cv2-minimal-opencv-build.md):
    - CMake (on PATH)
    - Ninja (on PATH)
    - Visual Studio 2022 Build Tools + VCTools workload (vcvarsall.bat)
    - Python 3.13 (the interpreter HushSnap builds against)

  Output: opencv-build/output/cv2.cp313-win_amd64.pyd
  Next:  python scripts/build/verify_minimal_cv2.py --pyd <that path>
         python tests/run_ocr_minimal_cv2.py --pyd <that path>

.PARAMETER OpenCVVersion
  OpenCV tag to build.  Defaults to 5.0.0 (matches the official opencv-python
  wheel HushSnap ships; the 30 symbols rapidocr uses are classical and
  API-stable across 4.x/5.x).  Rebuild with -OpenCVVersion 4.10.0 to compare.

.PARAMETER ForceClean
  Re-run CMake configure from scratch (otherwise reuses opencv-build/cmake).

.PARAMETER NoIPP
  Build WITHOUT Intel IPP (-DWITH_IPP=OFF).  IPP is the pyd's biggest size
  component (~28 MB) but a perf layer, not function.  Use to A/B size vs OCR
  latency, especially on AMD where IPP (Intel-tuned) may not be used at all.
  With NoIPP the pyd drops to ~18 MB and imgproc falls back to OpenCV's own
  AVX2 implementations.

.NOTES
  Two fixes baked in from the original experiment (do not regress):
    * BUILD_SHARED_LIBS=OFF -- otherwise you get a 1.5 MB cv2.pyd shell + three
      opencv_*.dll (~48 MB), NOT a single-file replacement for the wheel.
    * NO --target install -- OpenCV's python3 install target ignores
      CMAKE_INSTALL_PREFIX and clobbers the active interpreter's site-packages
      (pip uninstall cannot clean non-RECORD files).  We take the pyd straight
      from build/lib/python3/.

  Contamination is defended in TWO layers:
    1. Structural: no --target install anywhere (see above).  All build output
       stays under opencv-build\ (src + cmake build tree) and opencv-build/output\
       (staged pyd); nothing is written to the interpreter's site-packages.
    2. Asserted: the script snapshots the system cv2\ package (file list + sizes)
       before building and asserts it is byte-identical afterwards.  If anything
       touched site-packages/cv2 during the build, the script throws LOUD with a
       recovery recipe rather than silently shipping a broken interpreter.

  The verify/regression scripts (verify_minimal_cv2.py, run_ocr_minimal_cv2.py)
  do NOT contaminate either: they path-inject the minimal pyd into a single
  process's sys.path (sys.path[0] + sys.modules.pop), which writes nothing to
  disk and vanishes when the process exits.

  Size vs speed -- Intel IPP dominates the pyd size:
    * WITH_IPP=ON (default): ~47 MB pyd.  IPPICV (~28 MB linked) accelerates the
      imgproc hot path (resize/cvtColor/warp) rapidocr calls on every image.
      IPP is Intel-tuned -- it may not be used at all on AMD CPUs, where the 28 MB
      could be pure dead weight.  A/B with -NoIPP before trusting it.
    * WITH_IPP=OFF (-NoIPP): ~18 MB pyd, imgproc falls back to OpenCV's own AVX2
      paths.  The 30 symbols + OCR output are identical either way (IPP is perf,
      not function).  Measure OCR latency on the target CPU to decide.
  Everything else pruned above (OpenCL/ITT/OpenEXR/OpenJPEG/TIFF/WEBP) is dead
  weight rapidocr provably never touches -- safe to drop unconditionally.
#>
param(
    [string]$OpenCVVersion = "5.0.0",
    [switch]$ForceClean,
    [switch]$NoIPP
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)  # script lives in scripts/build/, one level below scripts/
$workDir  = Join-Path $repoRoot "opencv-build"
$srcDir   = Join-Path $workDir "src"
$buildDir = Join-Path $workDir "cmake"
$outDir   = Join-Path $workDir "output"
$outPyd   = Join-Path $outDir "cv2.cp313-win_amd64.pyd"

# --- locate toolchain --------------------------------------------------------
$python = (Get-Command python -ErrorAction Stop).Source
Write-Host "Python:      $python"

$cmake = (Get-Command cmake -ErrorAction Stop).Source
$ninja = (Get-Command ninja -ErrorAction Stop).Source
Write-Host "CMake:        $cmake"
Write-Host "Ninja:        $ninja"

# Find vcvarsall.bat via vswhere (robust across VS editions/install locations).
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    throw "vswhere.exe not found at $vswhere -- install the Visual Studio Installer."
}
$vcRoot = & $vswhere -latest -products * `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    -property installationPath
if (-not $vcRoot) {
    throw "No VS installation with VC tools found. Install VS 2022 Build Tools + VCTools workload."
}
$vcvars = Join-Path $vcRoot "VC\Auxiliary\Build\vcvarsall.bat"
if (-not (Test-Path $vcvars)) { throw "vcvarsall.bat not found at $vcvars" }
Write-Host "MSVC vcvars:  $vcvars"

# --- snapshot system cv2 (contamination guard) -------------------------------
# OpenCV's python3 install target ignores CMAKE_INSTALL_PREFIX and writes
# straight to the active interpreter's site-packages, clobbering the official
# opencv-python cv2/ package (pip uninstall cannot clean the non-RECORD files
# it leaves behind -- see memory/cv2-install-pollutes-site-packages).
# We NEVER run --target install (see NOTES), so this *should* be a no-op.  But
# "should" is not "verified": we snapshot the system cv2/ package now and assert
# it is byte-identical (same files, same sizes) after the build, so any
# contamination fails LOUD instead of silently breaking the interpreter's
# opencv-python.  mtime is deliberately excluded (AV tools touch it); size is
# the stable signal.
function Get-Cv2Fingerprint([string]$Dir) {
    if (-not (Test-Path $Dir)) { return "" }
    $entries = Get-ChildItem $Dir -Recurse -File | ForEach-Object {
        "{0}|{1}" -f $_.FullName.Substring($Dir.Length + 1), $_.Length
    }
    return ($entries | Sort-Object) -join "`n"
}
$sitePackages  = & $python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
$sysCv2Dir     = Join-Path $sitePackages "cv2"
$sysCv2Before  = Get-Cv2Fingerprint $sysCv2Dir
if ($sysCv2Before) {
    Write-Host "Guard: snapshotting system cv2 at $sysCv2Dir (will assert unchanged after build)"
} else {
    Write-Host "Guard: no system cv2/ package at $sysCv2Dir -- contamination check skipped"
}

# --- clone opencv source (preserved across runs to avoid re-clone) -----------
if (-not (Test-Path (Join-Path $srcDir "CMakeLists.txt"))) {
    if (Test-Path $srcDir) { Remove-Item -Recurse -Force $srcDir }
    New-Item -ItemType Directory -Force -Path $srcDir | Out-Null
    Write-Host "Cloning opencv $OpenCVVersion (--depth 1)..."
    git clone --depth 1 --branch $OpenCVVersion https://github.com/opencv/opencv.git $srcDir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed for opencv $OpenCVVersion" }
}

# --- import MSVC env into this PowerShell session ----------------------------
Write-Host "Loading MSVC environment (x64)..."
# vcvarsall prints a banner; suppress it, then `set` dumps the env we import.
$envOut = & cmd /c "`"$vcvars`" x64 >nul 2>&1 && set"
foreach ($line in $envOut) {
    if ($line -match '^(.*?)=(.*)$') {
        Set-Item -Path "env:$($matches[1])" -Value $matches[2]
    }
}

# --- configure + build -------------------------------------------------------
if ($ForceClean -and (Test-Path $buildDir)) {
    Remove-Item -Recurse -Force $buildDir
}
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

$pyInclude = (& $python -c "import sysconfig; print(sysconfig.get_path('include'))").Trim()

# IPP setting: ON by default (fast), OFF with -NoIPP (smaller).  PowerShell has
# no ternary expression; resolve to a literal string here for the arg list below.
$ippFlag = if ($NoIPP) { "OFF" } else { "ON" }

# Configure via an argument array + splatting, NOT backtick line-continuation.
# Two reasons:
#   * PowerShell's comma operator mis-parses an unquoted
#     `-DBUILD_LIST=core,imgproc,imgcodecs,python3` (it reads the comma-list
#     as an array literal -> "Missing argument in parameter list").  Each arg
#     as its own QUOTED string element keeps the commas literal.
#   * Backtick continuation across native-command args is fragile (it broke in
#     a cmd wrapper historically).  Array splatting is the portable idiom.
# Flags recap:
#   BUILD_SHARED_LIBS=OFF      -> single static .pyd, directly comparable to the wheel.
#   BUILD_LIST                  -> only the three modules rapidocr needs + python3 bindings.
#   WITH_FFMPEG=OFF / BUILD_opencv_videoio=OFF
#                                -> kill the ~15MB FFMPEG DLL download at configure time.
#                                   OpenCV's top-level CMakeLists checks FFMPEG / videoio
#                                   UNCONDITIONALLY (before BUILD_LIST module resolution),
#                                   so detect_ffmpeg.cmake downloads opencv_videoio_ffmpeg_64.dll
#                                   even though videoio ends up disabled (HAVE_opencv_videoio=OFF,
#                                   never linked, never staged).  The download is pure waste:
#                                   rapidocr has zero videoio symbols.  Turning both OFF skips
#                                   the download entirely.  Defensive belt-and-suspenders: even
#                                   with these off, BUILD_LIST alone already excludes videoio.
#   PYTHON3_EXECUTABLE/INCLUDE  -> pin to the HushSnap build interpreter (FindPython3
#                                  could otherwise pick another install from the registry).
#   no --target install         -> see NOTES; we harvest the pyd from build/lib/python3.
$cmakeArgs = @(
    "-S", $srcDir,
    "-B", $buildDir,
    "-G", "Ninja",
    "-DCMAKE_BUILD_TYPE=Release",
    "-DBUILD_SHARED_LIBS=OFF",
    "-DBUILD_LIST=core,imgproc,imgcodecs,python3",
    "-DWITH_FFMPEG=OFF",
    "-DBUILD_opencv_videoio=OFF",
    # --- dead-weight infrastructure rapidocr never touches (config audit) ---
    # OpenCL/GPU: rapidocr operates on plain numpy/Mat and NEVER calls cv2.UMat
    # or cv2.ocl (the ONLY OpenCL entry points -- verified by grep).  Inference
    # runs on ONNXRuntime CPU, so OpenCL dispatch code is compiled in but never
    # executed.  Drop the OCL backend + AMD FFT/BLAS variants.
    "-DWITH_OPENCL=OFF",
    "-DWITH_OPENCLAMDFFT=OFF",
    "-DWITH_OPENCLAMDBLAS=OFF",
    # ITT: Intel instrumentation/tracing profiling hooks -- never used at runtime.
    "-DWITH_ITT=OFF",
    # Image codecs rapidocr doesn't need: it decodes via PIL (Image.open), and
    # cv2 imgcodecs is only on the debug-vis imencode(.png) path HushSnap never
    # runs.  OpenEXR is 22MB of build objects yet its codec is runtime-disabled
    # (opencv#21326).  KEEP PNG (+ JPEG safety margin) -> only those link in.
    # WITH_JASPER=OFF: with OpenJPEG OFF, OpenCV falls back to building Jasper
    # (the legacy JPEG2000 lib) -- but Jasper's codec is ALSO runtime-disabled
    # by default (imgcodecs/CMakeLists.txt:62-65, needs OPENCV_IO_FORCE_JASPER,
    # never set).  So Jasper is pure build waste; turn it OFF at the source.
    "-DWITH_OPENEXR=OFF",
    "-DWITH_OPENJPEG=OFF",
    "-DWITH_JASPER=OFF",
    "-DWITH_TIFF=OFF",
    "-DWITH_WEBP=OFF",
    # Intel IPP: ~28MB of the pyd.  IPP is a perf layer (accelerates imgproc
    # hot path), NOT function -- 30 symbols + OCR output are identical on/off.
    # Default ON (fast); -NoIPP -> OFF (~18MB, imgproc falls back to OpenCV's
    # own AVX2).  IPP is Intel-tuned; on AMD it may not be used at all -- A/B
    # size + OCR latency before deciding.  See NOTES "Size vs speed".
    "-DWITH_IPP=$ippFlag",
    "-DPYTHON3_EXECUTABLE=$python",
    "-DPYTHON3_INCLUDE_DIR=$pyInclude",
    "-DOPENCV_SKIP_PYTHON_WARNING=ON",
    "-DCMAKE_INSTALL_PREFIX=$outDir"
)
& $cmake @cmakeArgs
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

& $cmake --build $buildDir --config Release
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

# --- locate + stage the built pyd -------------------------------------------
# Static build places the pyd under build/lib/python3/cv2.cp313-win_amd64.pyd.
$builtPyd = Get-ChildItem -Path $buildDir -Recurse -Filter "cv2.cp313-*.pyd" `
    -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $builtPyd) {
    throw "Build finished but cv2.cp313-*.pyd not found under $buildDir"
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
Copy-Item $builtPyd.FullName $outPyd -Force

$sizeMB = [math]::Round((Get-Item $outPyd).Length / 1MB, 1)

# --- assert system cv2 untouched (contamination guard) -----------------------
if ($sysCv2Before) {
    $sysCv2After = Get-Cv2Fingerprint $sysCv2Dir
    if ($sysCv2After -ne $sysCv2Before) {
        Write-Warning "SYSTEM cv2/ PACKAGE CHANGED DURING BUILD -- possible contamination!"
        Write-Warning "changed entries (relpath|size), before vs after differ."
        Write-Warning "Recover: pip uninstall -y opencv-python; Remove-Item -Recurse the cv2\ dir; pip install --no-deps opencv-python==5.0.0.93"
        throw "System cv2/ package was modified during the build -- aborting to protect the interpreter's opencv-python."
    }
    Write-Host "Guard: system cv2/ package unchanged after build (no contamination)"
}

Write-Host ""
Write-Host "Done. Minimal cv2.pyd -> $outPyd ($sizeMB MB)"

# --- sync the minimal pyd + package layout into the repo ---------------------
# Two consumers of the minimal cv2, both must stay in sync with this build:
#   1. third_party/cv2.cp313-win_amd64.pyd  -- HushSnap.spec swap_minimal_cv2
#      swaps this in as the shipped cv2.pyd binary (keeps its ABI-tagged name).
#   2. cv2/ (repo root)                     -- the minimal cv2 PACKAGE that
#      development/pytest import directly (sys.path[0] = repo root wins over
#      site-packages).  Holds cv2.pyd (untagged name) + the 12 runtime .py
#      files (bootstrap/config/version) sourced from the site-packages wheel.
$thirdPartyPyd = Join-Path $repoRoot "third_party\cv2.cp313-win_amd64.pyd"
Copy-Item $outPyd $thirdPartyPyd -Force
Write-Host "Synced: $thirdPartyPyd"

$repoCv2 = Join-Path $repoRoot "cv2"
New-Item -ItemType Directory -Force -Path $repoCv2 | Out-Null
# Minimal pyd as cv2.pyd (untagged -- bootstrap's importlib.import_module("cv2")
# resolves the package's cv2.pyd).  The 12 runtime .py files come from the
# site-packages wheel's cv2/ package (the package layout OpenCV's CMake build
# does NOT generate -- it is a wheel-packaging layer).  .pyi type stubs are
# deliberately excluded: they are runtime-dead and cause hasattr(cv2,"dnn")
# to misleadingly return True (empty namespace shells).
Copy-Item $outPyd (Join-Path $repoCv2 "cv2.pyd") -Force
$runtimePy = @(
    "__init__.py","config.py","config-3.py","load_config_py2.py","load_config_py3.py",
    "version.py","data\__init__.py","mat_wrapper\__init__.py",
    "misc\__init__.py","misc\version.py","typing\__init__.py","utils\__init__.py"
)
foreach ($f in $runtimePy) {
    $src = Join-Path $sysCv2Dir $f
    $dst = Join-Path $repoCv2 $f
    New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
    Copy-Item $src $dst -Force
}
Write-Host "Synced: $repoCv2 (cv2.pyd + $($runtimePy.Count) runtime .py)"

Write-Host "Next:  python scripts/build/verify_minimal_cv2.py"
Write-Host "       python tests/run_ocr_minimal_cv2.py"
