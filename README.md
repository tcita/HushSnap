# HushSnap

HushSnap is a global hotkey‑driven screenshot tool designed for speed and silence. Capture any screen region or the full screen instantly and have it saved to your clipboard. It also supports OCR, with more features currently in development.

## Development & Debugging
Run from source:

```powershell
python HushSnap.py --debug
```

## Unit Testing

HushSnap uses `pytest`. Tests live in `tests/` and cover config, hotkey flow, system interaction, and logging.

### Running Tests

```powershell
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

You can pass extra `pytest` args directly:

```powershell
# specific file
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1 tests/test_config.py

# keyword filter
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1 -k "hotkey" -vv
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

## Build Guide

### Prerequisites
- Run commands from the project root.
- Python and PyInstaller are installed.
- Inno Setup 6 is installed.

### 1) Full Release Build (EXE + Installer)
Update `hushsnap/__init__.py` first (the `__version__` value), then run the following command:

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

### 2) Dev Build (EXE Only, for Local Debugging)
If you only need the packaged EXE for local debugging (without generating an installer), run:

```powershell
powershell -ExecutionPolicy Bypass -File installer/build_dev.ps1
```

**This command will automatically:**
- Kill running `HushSnap.exe` processes.
- Clean build folders used by PyInstaller.
- Build `dist\HushSnap\HushSnap.exe` only (no installer output).

---

