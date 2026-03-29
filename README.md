# HushSnap

HushSnap is a global hotkey-driven screenshot tool designed for speed and silence. Capture any screen region or the full screen instantly and have it saved to your clipboard.

## ✨ Key Features

- **Global Hotkey:** Instant capture with a customizable shortcut (default: `Ctrl+Alt+A`).
- **High-Performance OCR:** 
  - **Text-Grab Engine:** Powered by Windows OCR with custom heuristics to fix common misidentifications (e.g., `of`, `in`, `if`).
  - **Language Selection:** Toggle between `en-US` and `zh-CN` directly from the OCR popup.
  - **Heuristic Correction:** Automatically fixes Latin text misidentified as CJK characters.
  - **Persistent Settings:** Remembers your last used OCR language.
- **Visual Preprocessing:** Enhanced image processing (contrast boost, sharpening, and mandatory padding) for maximum recognition accuracy.
- **Lightweight & Silent:** Runs in the system tray with minimal resource footprint.

## 🛠 Development & Debugging
Run from source:

```powershell
python HushSnap.py --debug
```

Enable OCR flow + save OCR preprocessed image (without full debug logging):

```powershell
python HushSnap.py --debug_ocr
```

Enable both:

```powershell
python HushSnap.py --debug --debug_ocr
```

Packaged EXE also accepts the same flags:

```powershell
.\dist\HushSnap\HushSnap.exe --debug_ocr
```

**Key Features of Debug Mode:**
- **Isolation:** Running from source uses `%LOCALAPPDATA%\HushSnap_Dev`, ensuring your production settings remain untouched.
- **Traceability:** Sets log level to `DEBUG` and opens the log folder immediately upon startup.
- **Live Output:** Real-time logs are streamed to the terminal via the logging console handler (`StreamHandler`).
- **OCR Inspection:** `--debug` or `--debug_ocr` saves the preprocessed OCR image to `ocr_debug_preprocessed.png` in the data directory.
  - Source run: `%LOCALAPPDATA%\HushSnap_Dev\ocr_debug_preprocessed.png`
  - Packaged run: `%LOCALAPPDATA%\HushSnap\ocr_debug_preprocessed.png`

---

## 🏗 Build Guide

### Prerequisites
- Run commands from the project root.
- Python and PyInstaller are installed.
- Inno Setup 6 is installed.

### 1) One-Command Build (Recommended)
Update `hushsnap/__init__.py` first (the `__version__` value), then run the following command to generate the final installer:

```powershell
powershell -ExecutionPolicy Bypass -File installer/build_installer.ps1
```

**This command will automatically:**
- Read the version from `hushsnap/__init__.py`.
- Build the app via PyInstaller as a `onedir` bundle based on `HushSnap.spec`.
- Package the entire `dist\HushSnap\` folder into the installer.

**Output:**
- `dist\HushSnap\HushSnap.exe`
- `dist-installer\HushSnap-Setup.exe`

### 2) Optional: Build EXE Only
If you only need to test the packaged app without creating an installer, run:
```powershell
pyinstaller --clean HushSnap.spec
```

### 3) Optional: Add App to Startup (Manual Test)
To test the application's startup behavior on your local machine:
```powershell
$exe = (Resolve-Path '.\dist\HushSnap\HushSnap.exe').Path
$startup = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup 'HushSnap.lnk'

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($linkPath)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = Split-Path $exe
$shortcut.IconLocation = "$exe,0"
$shortcut.Save()
```

---

