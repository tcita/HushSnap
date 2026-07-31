# -*- mode: python ; coding: utf-8 -*-
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
    pathex=[],
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
    return [b for b in binaries if not any(dll.lower() in b[0].lower() for dll in excluded_dlls)]

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

a.binaries = filter_binaries(a.binaries)
a.datas = filter_datas(a.datas)

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
