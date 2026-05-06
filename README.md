# HushSnap

HushSnap is a lightweight screenshot tool built to stay quietly in the background and be ready whenever you need it. It lives in the system tray, runs silently, and lets you instantly capture part or all of your screen, with every shot copied straight to your clipboard without interrupting your flow.

Designed around global shortcuts, HushSnap can capture from anywhere in Windows while staying out of sight and out of the way. The experience is intentionally minimal, silent, and fast, with customizable hotkeys that let you shape the workflow around your own habits. OCR is already supported, and as the project continues to evolve, some parts of this README may occasionally lag behind the latest behavior or features.

## OCR Engine

HushSnap ships with two OCR engines, selectable from the OCR popup:

- **RapidOCR (default):** Runs PP-OCRv4 ONNX models in-process via the [`rapidocr`](https://github.com/RapidAI/RapidOCR) Python package (Apache 2.0). No external dependencies or language packs needed. Works offline.
- **Windows OCR:** Uses `Windows.Media.Ocr`. Requires the relevant Windows language packs to be installed.

### Language Support

| Engine | Supported Languages |
|--------|-------------------|
| RapidOCR | Chinese (Simplified & Traditional), English, Japanese, Korean |
| Windows OCR | English, Simplified Chinese, Traditional Chinese |

- **RapidOCR** uses a unified PP-OCRv4 model that handles CJK + Latin characters natively. The language selector is hidden when RapidOCR is active — no language configuration is needed.
- **Windows OCR** language support depends on installed Windows language packs. `zh-CN` also accepts compatible packs (`zh-SG`, `zh-Hans`); `zh-TW` accepts `zh-HK`, `zh-MO`, `zh-Hant`.

## OCR Scope and Limitations

RapidOCR (PP-OCRv4) has been tested and performs well on standard screen captures including web pages, apps, and UIs. It is the recommended default engine.

Windows OCR relies on the built-in `Windows.Media.Ocr` library and may offer more predictable behavior in certain system configurations, though it requires the relevant language packs to be installed.

## Third-Party Acknowledgment

The OCR workflow and product ideas in this project were inspired by the
[Text-Grab](https://github.com/TheJoeFin/Text-Grab) project, which is licensed
under the MIT License.

HushSnap is distributed under **CC BY-NC-SA 4.0**. This project-level license
applies to HushSnap's original code and assets. Third-party projects keep their
own licenses; see `THIRD_PARTY_NOTICES.md` for attribution details.

## Development & Debugging

Install dependencies:

```powershell
pip install -r requirements.txt
```

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

