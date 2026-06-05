# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None
spec_file = globals().get('SPEC') or globals().get('__file__')
project_root = Path(spec_file).resolve().parent if spec_file else Path.cwd()

from PyInstaller.utils.hooks import collect_data_files

bundle_datas = [('ico.ico', '.')] + collect_data_files('rapidocr')

a = Analysis(
    ['HushSnap.py'],
    pathex=[],
    binaries=[],
    datas=bundle_datas,
    hiddenimports=[
	    'hushsnap',
	    'hushsnap.ocr',
	    'hushsnap.ocr.ppocr',
	    'hushsnap.ocr.recognition',
	    'hushsnap.ocr.models',
	    'hushsnap.ocr.preprocess',
	    'hushsnap.ocr.parsing',
	    'hushsnap.ocr.text',
	    'hushsnap.ocr.ocr_service',
	    'hushsnap.system',
	    'hushsnap.system.hotkey_manager',
	    'hushsnap.system.win32_window_utils',
	    'hushsnap.system.uninstall',
	    'hushsnap.ui',
	    'hushsnap.ui.tray',
	    'hushsnap.ui.settings_dialog',
	    'hushsnap.ui.styles',
	    'hushsnap.ui.ocr_popup',
	    'winrt.windows.applicationmodel',
	],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'openai', 'httpx', 'pydantic', 'anyio', 'httpcore',
        'tkinter', 'tcl', 'tk', '_tkinter', 'lib2to3',
        'unittest', 'pydoc',
        'PyQt6.QtNetwork', 'PyQt6.QtSql', 'PyQt6.QtWebEngine', 'PyQt6.QtQml',
        'PyQt6.QtQuick', 'PyQt6.QtMultimedia', 'PyQt6.QtBluetooth',
        'PyQt6.QtNfc', 'PyQt6.QtSerialPort', 'PyQt6.QtDesigner',
        'PyQt6.QtHelp', 'PyQt6.QtTest', 'PyQt6.QtXml', 'PyQt6.QtSvg',
        'openvino', 'openvino_telemetry',
        'cv2.videoio', 'cv2.samples', 'PIL._avif',
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
    # Keep only the core Chinese Qt translation pack; remove other translation modules.
    # Also strip rapidocr infer model variants (only mobile models are used).
    out = []
    for d in datas:
        src = d[0].lower()
        if 'translations' in src and 'qtbase_zh_cn' not in src and 'qtbase_zh_tw' not in src:
            continue
        if 'rapidocr/models/' in src.replace('\\', '/'):
            basename = src.replace('\\', '/').rsplit('/', 1)[-1]
            if '_infer.onnx' in basename:
                continue
            if 'ppocrv5_dict' in basename:
                continue
            if 'v4' in basename: # Exclude v4 models (using v5)
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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ico.ico'],
    manifest='installer/HushSnap.exe.manifest',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['python3.dll'],
    name='HushSnap',
)
