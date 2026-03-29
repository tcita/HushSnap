# HushSnap

HushSnap is a global hotkey‑driven screenshot tool designed for speed and silence. Capture any screen region or the full screen instantly and have it saved to your clipboard. It also supports OCR, with more features currently in development.

## 🛠 Development & Debugging
Run from source:

```powershell
python HushSnap.py --debug
```

Run packaged EXE in debug mode:

```powershell
.\dist\HushSnap\HushSnap.exe --debug
```

**Key Features of Debug Mode:**
- **Isolation:** Running from source uses `%LOCALAPPDATA%\HushSnap_Dev`, ensuring your production settings remain untouched.
- **Traceability:** Sets log level to `DEBUG` and opens the log folder immediately upon startup.
- **Live Output:** Real-time logs are streamed to the terminal via the logging console handler (`StreamHandler`).
- **OCR Inspection:** `--debug` saves the preprocessed OCR image to `ocr_debug_preprocessed.png` in the data directory.
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

---

