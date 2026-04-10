# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None
spec_file = globals().get('SPEC') or globals().get('__file__')
project_root = Path(spec_file).resolve().parent if spec_file else Path.cwd()
bundle_datas = [('ico.ico', '.')]

a = Analysis(
    ['HushSnap.py'],
    pathex=[],
    binaries=[],
    datas=bundle_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'openai', 'requests', 'httpx', 'pydantic', 'anyio', 'httpcore', 'urllib3',
        'tkinter', 'tcl', 'tk', '_tkinter', 'lib2to3',
        'unittest', 'pydoc', 'email', 'html', 'http', 'xml',
        'PyQt6.QtNetwork', 'PyQt6.QtSql', 'PyQt6.QtWebEngine', 'PyQt6.QtQml', 
        'PyQt6.QtQuick', 'PyQt6.QtMultimedia', 'PyQt6.QtBluetooth', 
        'PyQt6.QtNfc', 'PyQt6.QtSerialPort', 'PyQt6.QtDesigner', 
        'PyQt6.QtHelp', 'PyQt6.QtTest', 'PyQt6.QtXml', 'PyQt6.QtSvg',
        'aido'
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --- Less aggressive optimization for stability ---
def filter_binaries(binaries):
    # Only drop very obvious heavy but unused DLLs if necessary.
    # Restoring Network and Svg as they are often required by Qt internally.
    excluded_dlls = [
        'Qt6Pdf.dll', 'qpdf.dll'
    ]
    return [b for b in binaries if not any(dll.lower() in b[0].lower() for dll in excluded_dlls)]

def filter_datas(datas):
    # Keep only the core Chinese Qt translation pack; remove other translation modules.
    return [d for d in datas if not (
        'translations' in d[0].lower() and not ('qtbase_zh_cn' in d[0].lower())
    )]

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
