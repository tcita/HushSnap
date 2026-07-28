# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None
spec_file = globals().get('SPEC') or globals().get('__file__')
project_root = Path(spec_file).resolve().parent if spec_file else Path.cwd()

from PyInstaller.utils.hooks import collect_data_files

# Collect SVG icons — loaded via filesystem reads (open / QIcon path), not Python imports
icons_glob = [str(f) for f in (project_root / 'hushsnap' / 'ui' / 'icons').glob('*.svg')]
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
