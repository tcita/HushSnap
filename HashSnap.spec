"""PyInstaller 打包配置。"""

# -*- mode: python ; coding: utf-8 -*-

datas = [('camera.ico', '.')]
binaries = []
hiddenimports = []


a = Analysis(
    ['HashSnap.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt6.QAxContainer',
        'PyQt6.QtBluetooth',
        'PyQt6.QtDBus',
        'PyQt6.QtDesigner',
        'PyQt6.QtHelp',
        'PyQt6.QtMultimedia',
        'PyQt6.QtMultimediaWidgets',
        'PyQt6.QtNfc',
        'PyQt6.QtOpenGL',
        'PyQt6.QtOpenGLWidgets',
        'PyQt6.QtPdf',
        'PyQt6.QtPdfWidgets',
        'PyQt6.QtPositioning',
        'PyQt6.QtPrintSupport',
        'PyQt6.QtQml',
        'PyQt6.QtQuick',
        'PyQt6.QtQuick3D',
        'PyQt6.QtQuickWidgets',
        'PyQt6.QtRemoteObjects',
        'PyQt6.QtSensors',
        'PyQt6.QtSerialPort',
        'PyQt6.QtSpatialAudio',
        'PyQt6.QtSql',
        'PyQt6.QtStateMachine',
        'PyQt6.QtSvg',
        'PyQt6.QtSvgWidgets',
        'PyQt6.QtTest',
        'PyQt6.QtTextToSpeech',
        'PyQt6.QtWebChannel',
        'PyQt6.QtWebSockets',
        'PyQt6.QtXml',
    ],
    noarchive=False,
    optimize=0,
)


def _norm_path(path):
    return path.replace('\\', '/').lower()


qt_bin_blocklist = {
    'pyqt6/qt6/bin/qt6pdf.dll',
    'pyqt6/qt6/bin/qt6svg.dll',
}

qt_plugin_allowlist = {
    'pyqt6/qt6/plugins/platforms/qwindows.dll',
    'pyqt6/qt6/plugins/styles/qmodernwindowsstyle.dll',
    'pyqt6/qt6/plugins/imageformats/qico.dll',
    'pyqt6/qt6/plugins/imageformats/qjpeg.dll',
}

qt_translation_allow_prefixes = (
    'pyqt6/qt6/translations/qtbase_en',
    'pyqt6/qt6/translations/qtbase_zh_cn',
    'pyqt6/qt6/translations/qt_en',
    'pyqt6/qt6/translations/qt_zh_cn',
)

filtered_binaries = []
for entry in a.binaries:
    dest = _norm_path(entry[0])

    if dest in qt_bin_blocklist:
        continue

    if 'pyqt6/qt6/plugins/' in dest and dest not in qt_plugin_allowlist:
        continue

    filtered_binaries.append(entry)

filtered_datas = []
for entry in a.datas:
    dest = _norm_path(entry[0])

    if 'pyqt6/qt6/translations/' in dest:
        if not any(dest.startswith(prefix) for prefix in qt_translation_allow_prefixes):
            continue

    filtered_datas.append(entry)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HashSnap',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    filtered_binaries,
    filtered_datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='HashSnap',
)



