# -*- mode: python ; coding: utf-8 -*-
import os
import re
from pathlib import Path

block_cipher = None
spec_file = globals().get('SPEC') or globals().get('__file__')
project_root = Path(spec_file).resolve().parent if spec_file else Path.cwd()

from PyInstaller.utils.hooks import collect_data_files

# Collect SVG icons — loaded via filesystem reads (open / QIcon path), not Python imports
icons_glob = [str(f) for f in (project_root / 'hushsnap' / 'ui' / 'icons').glob('*') if f.suffix in ('.svg', '.png')]
bundle_datas = [('hushsnap.ico', '.')] + collect_data_files('rapidocr') + [(f, 'hushsnap/ui/icons') for f in icons_glob]

a = Analysis(
    ['HushSnap.py'],
    pathex=['_stubs'],  # requests/tqdm stubs → blocks real ~3 MB network chain
    binaries=[],
    datas=bundle_datas,
    hiddenimports=[
	    'hushsnap',
	    'hushsnap.ocr',
	    'hushsnap.ocr.ppocr',
	    'hushsnap.ocr.engine',
	    'hushsnap.ui.thumbnail',
	    'hushsnap.ocr.models',
	    'hushsnap.ocr.preprocess',
	    'hushsnap.ocr.text',
	    'hushsnap.ocr.ocr_service',
	    'hushsnap.system',
	    'hushsnap.system.hotkey_manager',
	    'hushsnap.system.win32_window_utils',
	    'hushsnap.ui',
	    'hushsnap.ui.tray',
	    'hushsnap.ui.settings_dialog',
	    'hushsnap.ui.styles',
	    'hushsnap.ui.ocr_popup',
	    'hushsnap.ui.editor',
	    'hushsnap.ui.editor.tools',
	    'hushsnap.ui.editor.widgets',
	    'winrt.windows.applicationmodel',
	    'winrt.windows.foundation',
	],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'tcl', 'tk', '_tkinter', 'lib2to3',
        'unittest', 'pydoc',
        'PyQt6.QtNetwork', 'PyQt6.QtSql', 'PyQt6.QtWebEngine', 'PyQt6.QtQml',
        'PyQt6.QtQuick', 'PyQt6.QtMultimedia', 'PyQt6.QtBluetooth',
        'PyQt6.QtNfc', 'PyQt6.QtSerialPort', 'PyQt6.QtDesigner',
        'PyQt6.QtHelp', 'PyQt6.QtTest', 'PyQt6.QtXml',
        'openvino', 'openvino_telemetry',
        'cv2.videoio', 'cv2.samples', 'PIL._avif',
        'hushsnap.benchmark',
        # Heavy libs pulled into the dev env by OTHER tools (e.g. a CUDA/ML
        # stack), not used by HushSnap. PyInstaller's static analysis sees
        # them on the path and bundles them anyway - ~190 MB of dead weight
        # (llvmlite.dll alone is 115 MB). rapidocr uses onnxruntime, never
        # numba/scipy. Exclude the whole chain. (The build venv makes these
        # unreachable in practice, but they stay as a defensive net in case
        # someone packages against a polluted interpreter.)
        'numba', 'llvmlite',
        'scipy', 'highspy',
        # pandas + its IO deps arrive via gradio (another tool's dependency);
        # rapidocr does not import pandas. ~13 MB.
        'pandas', 'pyarrow', 'fastparquet',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --- Less aggressive optimization for stability ---
def filter_binaries(binaries):
    # Only drop very obvious heavy but unused DLLs if necessary.
    excluded_dlls = [
        'Qt6Pdf.dll', 'qpdf.dll',
        'opencv_videoio_ffmpeg', # 27MB, not needed for screenshots
        'Qt6Qml.dll', 'Qt6Quick.dll', 'Qt6VirtualKeyboard.dll',
        'Qt6Network.dll', # 1.7MB, no network usage
        'opengl32sw.dll' # 5.5MB, software renderer fallback (most systems have HW)
    ]
    result = [b for b in binaries if not any(dll.lower() in b[0].lower() for dll in excluded_dlls)]

    # --- winrt bundled VC++ runtime duplicate (ABI hazard) -----------------
    # winrt-runtime 3.x wheels ship a private copy of MSVCP140.dll pinned to
    # 14.29.30157.0 (VS 2019, 2020) inside winrt/. PyInstaller follows the
    # wheel RECORD and bundles it verbatim. The rest of the app (PyQt6, cv2,
    # onnxruntime, compiled with VS 2022) carries 14.44.35211.0 at
    # _internal/msvcp140.dll. A frozen process that loads BOTH MSVCP140s gets
    # an ABI mismatch: STL object layouts differ across the two versions, and
    # the private winrt copy shadows the system-grade one for winrt's pyd
    # (DLL search prefers the same directory). This manifested as a recurring
    # 0xc0000005 access violation at offset 0x13080 inside MSVCP140 whenever
    # winrt loaded (Launch-at-Startup path) - silent in packaged builds
    # (native AV in a windowed app with no console).
    # VC++ runtime is forward-compatible: 14.44 satisfies winrt's 14.29 link,
    # so dropping the private copy lets winrt fall back to the single shared
    # _internal/msvcp140.dll. Also drop the matching vcruntime/concrt if the
    # wheel ever bundles them, for the same reason.
    def _is_winrt_vcruntime(dest):
        # dest is the relative path inside _internal (TOC entry's first elem).
        parts = dest.replace('\\', '/').lower().split('/')
        if 'winrt' not in parts:
            return False
        return parts[-1] in ('msvcp140.dll', 'vcruntime140.dll',
                             'vcruntime140_1.dll', 'concrt140.dll')
    result = [b for b in result if not _is_winrt_vcruntime(b[0])]

    # --- Prefer system CRT over Python's bundled copy -----------------------
    # Python 3.13 ships VCRUNTIME140.dll 14.42; the system VC++ Redist (14.44
    # as of 2026-08) is newer and patched.  Swap the top-level
    # VCRUNTIME140.dll (and _1.dll) source to System32 so the MSIX always
    # carries the latest system CRT regardless of the Python install.
    #
    # NOTE: This is NOT a fix for the onnxruntime import slowdown — that
    # regression (1.22+ → ~7 s) is caused by SetupDiGetClassDevsA GPU
    # enumeration inside PyInit, not CRT version.  The real fix is pinning
    # onnxruntime==1.21.1 (last version without DML provider probing).
    # See memory/ for full investigation.
    _SYSTEM32 = r'C:\Windows\System32'
    result = [
        (
            dest,
            os.path.join(_SYSTEM32, os.path.basename(dest)),
            kind,
        )
        if (
            dest.replace('\\', '/').count('/') == 1
            and os.path.basename(dest).lower()
            in ('vcruntime140.dll', 'vcruntime140_1.dll')
            and os.path.exists(os.path.join(_SYSTEM32, os.path.basename(dest)))
        )
        else (dest, src, kind)
        for (dest, src, kind) in result
    ]
    return result

def filter_datas(datas):
    # Drop ALL Qt translation packs (.qm). HushSnap does its own i18n via
    # hushsnap/translations.py, not Qt's QTranslator system - no code ever
    # calls QTranslator.load() / app.installTranslator(), so the .qm files
    # PyQt6 auto-collects (~10 MB across 217 files) are pure dead weight.
    # If a future change starts loading Qt's own translations for standard
    # dialogs (QMessageBox Yes/No/OK, QFileDialog), re-add a zh_CN/zh_TW
    # whitelist here.
    out = []
    for d in datas:
        if 'translations' in d[0].lower():
            continue
        out.append(d)
    return out

def strip_model_urls(datas):
    """Replace remote URLs in rapidocr/default_models.yaml with bare filenames.

    rapidocr uses this YAML to look up model download URLs (model_dir) and
    dictionary URLs (dict_url).  HushSnap ships all ONNX models pre-bundled
    and SHA256-verified, so the URLs are dead weight — and a network risk if
    the code-level stubs (_stubs/requests, _stubs/tqdm) are ever bypassed.

    This function replaces every model_dir URL with just the filename
    (e.g. ``PP-OCRv6_det_small.onnx``) and removes every dict_url entry.
    SHA256 values are preserved so _should_skip_download can still verify
    the pre-bundled files."""
    import tempfile
    import yaml

    out = []
    for entry in datas:
        dest, source = entry[0], entry[1]
        dest_norm = dest.replace('\\', '/')
        if dest_norm == 'rapidocr/default_models.yaml':
            with open(source, 'r', encoding='utf-8') as fh:
                data = yaml.safe_load(fh)
            _walk_and_strip_urls(data)
            tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.yaml', delete=False, encoding='utf-8')
            yaml.dump(data, tmp, default_flow_style=False, allow_unicode=True)
            tmp.close()
            out.append((dest, tmp.name, entry[2]))
        else:
            out.append(entry)
    return out


def _walk_and_strip_urls(node):
    """Recursively walk the YAML tree.  For dicts: replace model_dir URLs
    with bare filenames, delete dict_url keys.  For lists: recurse."""
    if isinstance(node, dict):
        # Strip model_dir: keep only Path(url).name
        if 'model_dir' in node:
            url = node['model_dir']
            if isinstance(url, str) and url.startswith('http'):
                node['model_dir'] = Path(url).name
        # Delete dict_url (ONNX dicts are embedded in model metadata)
        node.pop('dict_url', None)
        # Recurse into nested dicts
        for _key, value in node.items():
            _walk_and_strip_urls(value)
    elif isinstance(node, list):
        for item in node:
            _walk_and_strip_urls(item)


a.binaries = filter_binaries(a.binaries)
a.datas = strip_model_urls(filter_datas(a.datas))

# --- Belt-and-suspenders: strip any network module that leaked past the stubs ---
# pathex=['_stubs'] ensures PyInstaller finds our requests/tqdm stubs FIRST
# during Analysis.  Because those stubs import nothing third-party, the import
# tracer stops there — urllib3, certifi, charset_normalizer, and idna are never
# added to a.pure / a.binaries in the first place.
#
# This filter is a safety net: if a future PyInstaller version changes its
# module-finding behaviour, or a new dependency pulls in a network package
# through an unexpected path, we strip it here rather than shipping live
# HTTP-capable code.
_NETWORK_MODULE_PREFIXES = (
    'urllib3', 'certifi', 'charset_normalizer', 'idna',
)
_NETWORK_BINARY_DIRS = (
    'charset_normalizer',  # cd.*.pyd, md.*.pyd
)

a.pure = [
    m for m in a.pure
    if not any(m[0].startswith(p) for p in _NETWORK_MODULE_PREFIXES)
]
a.binaries = [
    b for b in a.binaries
    if not any(d in b[0].replace('\\', '/') for d in _NETWORK_BINARY_DIRS)
]

# --- Minimal cv2.pyd swap (82MB official -> 24.8MB purpose-built) -------------
# rapidocr is the only runtime cv2 consumer and uses 30 symbols, all in
# core/imgproc/imgcodecs (audited by tests/test_cv2_symbol_audit.py).  The
# official opencv-python cv2.pyd is an 82MB monolithic build with ~90% unused
# modules.  scripts/build/build_minimal_opencv.ps1 compiles a 24.8MB static
# single-file pyd (OpenCV 5.0.0, WITH_IPP=OFF + dead codecs/GPU stripped);
# its OCR output is byte-identical to the official wheel.  (At 4.10 this was
# 14.3MB; 5.0 grew ~10MB because the geometry module split out of imgproc and
# drags in flann+usac as transitive deps -- not controllable by codec flags.)
# The built pyd is committed at third_party/cv2.cp313-win_amd64.pyd so the
# build needs no compile step.
#
# This swaps ONLY the cv2.pyd binary source -- the frozen cv2/ package layout
# (opencv's __init__.py bootstrap, config*.py) is unchanged.  Verified: the
# bootstrap loads the tagged minimal pyd via importlib.import_module("cv2")
# and rapidocr runs end-to-end (5/5, char-identical) under this layout.
# Rebuild the pyd when OpenCV or the target Python (cp313) version changes:
#   pwsh scripts/build/build_minimal_opencv.ps1 -NoIPP -ForceClean
#   cp opencv-build/output/cv2.cp313-win_amd64.pyd third_party/
def swap_minimal_cv2(binaries):
    minimal_pyd = str(project_root / 'third_party' / 'cv2.cp313-win_amd64.pyd')
    if not (project_root / 'third_party' / 'cv2.cp313-win_amd64.pyd').is_file():
        raise SystemExit(
            "third_party/cv2.cp313-win_amd64.pyd not found. "
            "Build it: pwsh scripts/build/build_minimal_opencv.ps1 -NoIPP -ForceClean, "
            "then cp opencv-build/output/cv2.cp313-win_amd64.pyd third_party/"
        )
    # PyInstaller binaries TOC entries are 3-tuples: (dest_relpath, source_abspath, kind).
    # We swap the source of the cv2 extension module only.  Match by dest name so it
    # works whether the frozen entry is 'cv2/cv2.pyd' or 'cv2/cv2.cp313-win_amd64.pyd'.
    cv2_pyd_names = {'cv2.pyd', 'cv2.cp313-win_amd64.pyd'}
    out, swapped = [], False
    for entry in binaries:
        dest, source, kind = entry  # (dest_relpath, source_abspath, kind)
        dest_name = Path(dest).name.lower()
        if dest_name in cv2_pyd_names:
            out.append((dest, minimal_pyd, kind))
            swapped = True
        else:
            out.append(entry)
    if not swapped:
        raise SystemExit(
            "cv2.pyd not found in PyInstaller binaries -- expected the opencv-python "
            "cv2.pyd to be collected. Did the cv2 package layout change?"
        )
    return out

a.binaries = swap_minimal_cv2(a.binaries)

# --- cv2 version-consistency assertion --------------------------------------
# Guard against drift between pip-installed opencv-python (whose cv2/version.py
# PyInstaller collects) and the third_party/ minimal pyd build version.
# swap_minimal_cv2 only replaces the cv2.pyd binary source; the cv2/ package
# layout (__init__.py bootstrap + version.py) still comes from pip's full wheel.
# If pip upgrades opencv-python but the minimal pyd is not rebuilt, the
# bootstrap and pyd fall out of sync.
def _pyd_opencv_version(pyd_path):
    """Embedded OpenCV library version (3-seg X.Y.Z) in the minimal pyd.
    getBuildInformation() writes "OpenCV version is 'X.Y.Z'" into .rdata;
    both official and self-built pyds contain it."""
    data = Path(pyd_path).read_bytes()
    m = re.search(rb"OpenCV version is '(\d+\.\d+\.\d+)'", data)
    if not m:
        raise SystemExit(
            f"Could not extract OpenCV version from minimal pyd: {pyd_path}\n"
            "The pyd may not be a standard OpenCV build."
        )
    return m.group(1).decode()

def _collected_opencv_version(datas):
    """The opencv_version (4-seg X.Y.Z.W) from PyInstaller-collected
    cv2/version.py (sourced from pip's opencv-python wheel). None if absent."""
    for entry in datas:
        dest = entry[0].replace("\\", "/")
        if dest == "cv2/version.py":
            txt = Path(entry[1]).read_text(encoding="utf-8")
            m = re.search(r'opencv_version\s*=\s*["\']([^"\']+)["\']', txt)
            if m:
                return m.group(1)
    return None

def _assert_cv2_version_consistent(datas):
    pyd_ver = _pyd_opencv_version(
        str(project_root / "third_party" / "cv2.cp313-win_amd64.pyd"))
    coll_ver = _collected_opencv_version(datas)
    if coll_ver is None:
        raise SystemExit(
            "PyInstaller did not collect cv2/version.py -- is opencv-python installed?"
        )
    coll_lib = ".".join(coll_ver.split(".")[:3])  # first 3 of 4 segs = library version
    if coll_lib != pyd_ver:
        raise SystemExit(
            f"cv2 version drift: pip opencv-python collected = {coll_ver} "
            f"(library {coll_lib}), third_party minimal pyd build = {pyd_ver}.\n"
            "pip upgraded opencv-python but the minimal pyd was not rebuilt. Fix:\n"
            f"  pwsh scripts/build/build_minimal_opencv.ps1 -OpenCVVersion {coll_lib} -NoIPP -ForceClean\n"
            "  cp opencv-build/output/cv2.cp313-win_amd64.pyd third_party/\n"
            "  and pin opencv-python to the matching version in requirements.txt."
        )
    print(f"[spec] cv2 version consistent: pip opencv-python={coll_ver} == minimal pyd={pyd_ver}")

_assert_cv2_version_consistent(a.datas)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HushSnap',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX disabled: it was never effective (UPX binary not installed, so
    # upx=True was a silent no-op), and for MSIX packaging it is actively
    # harmful - UPX's high-entropy output defeats MakeAppx's deflate pass
    # (a .dll that deflate alone compresses to ~45% becomes ~0% re-compressible
    # after UPX), making the downloaded MSIX larger, not smaller. UPX only
    # helps unpacked footprint, which is irrelevant for an MSIX install.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['hushsnap.ico'],
    manifest='installer/HushSnap.exe.manifest',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='HushSnap',
)
