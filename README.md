# HushSnap

HushSnap is a lightweight screenshot & OCR tool that lives in your system tray. Press a hotkey and a crosshair overlay appears instantly — select a region or click for the full screen, and the shot lands on your clipboard immediately. No pre-launching menus or windows; the UI surfaces only when you need it.

After capture, a thumbnail slides in at the bottom-right corner of your screen. This thumbnail is the heart of HushSnap's post-capture flow:

- **Hover** to pause auto-hide and reveal a close button.
- **Drag and drop** the thumbnail anywhere to save the image — into a chat, an email, a folder, or another app.
- **Left-click** the thumbnail to run **offline OCR**. Recognized text opens in a floating popup where you can edit, copy, resize, or pin the window to keep it visible. By default text is also auto-copied to your clipboard.
- **Right-click** for more options: open in your default image viewer, **pin** as a floating always-on-top window (with its own OCR), save to Desktop, or Save As… anywhere you choose.

HushSnap is controlled from the **system tray**: single-click the tray icon to start a capture, or right-click to access Screenshot, Settings, the config folder, and Quit. The **Settings** dialog (with General, Capture, and OCR pages) lets you rebind the global hotkey (default `Alt+Q`), switch UI language, change how long the thumbnail stays visible, toggle auto-copy behavior, adjust OCR font size, and more.

Because HushSnap works at the Windows level, the hotkey responds from any app, anywhere. Everything runs locally — your screenshots and OCR results never leave your device.

As the project continues to evolve, some parts of this README may occasionally lag behind the latest behavior or features.

## OCR Engine

HushSnap uses [**PP-OCR**](https://github.com/PaddlePaddle/PaddleOCR) as its sole OCR engine:

- **PP-OCR (via RapidOCR):** Runs PP-OCRv5 ONNX models in-process via the [`rapidocr`](https://github.com/RapidAI/RapidOCR) Python package (Apache 2.0). No external dependencies or language packs needed. Works offline. Uses a unified multilingual model covering CJK, Latin, and more. Ships with a compact embedded model that balances quality and performance across supported scripts.

The engine has been tested on standard screen captures (web, apps, UIs) within HushSnap's own capture→preprocess→OCR pipeline. **Simplified Chinese, Traditional Chinese, and English** are verified to work well. PP-OCRv5 also officially supports **Japanese** with reported good accuracy, though we haven't been able to verify this independently — Japanese-speaking users are welcome to share their experience. The model may recognize additional scripts (e.g., German, Spanish), but accuracy for those is uncharacterized.

## Third-Party Acknowledgment

The OCR workflow and product ideas in this project were inspired by the
[Text-Grab](https://github.com/TheJoeFin/Text-Grab) project, which is licensed
under the MIT License.

HushSnap is distributed under **Apache 2.0**. This project-level license
applies to HushSnap's original code and assets. Third-party projects keep their
own licenses; see `THIRD_PARTY_NOTICES.md` for attribution details.

## Development & Debugging

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run from source:

```powershell
python HushSnap.py
```

To enable debug mode, set `debug = true` in `hushsnap_config.toml` (the `--debug` CLI flag was removed because MSIX packages cannot receive command-line arguments).

**Key Features of Debug Mode:**

- **Isolation:** Running from source uses `%LOCALAPPDATA%\HushSnap_Dev`, ensuring your production settings remain untouched.
- **Traceability:** Sets log level to `DEBUG` and opens the log folder immediately upon startup.
- **Live Output:** Real-time logs are streamed to the terminal via the logging console handler (`StreamHandler`).
- **OCR Inspection:** Debug mode saves the preprocessed OCR image to `ocr_debug_preprocessed.png` in the data directory. Right-click the tray icon → "Config Folder" to open this location directly.
  - Source run: `%LOCALAPPDATA%\HushSnap_Dev\ocr_debug_preprocessed.png`
  - Packaged run: `%LOCALAPPDATA%\HushSnap\ocr_debug_preprocessed.png`

## Building (MSIX)

```powershell
build_msix.bat              # build unsigned MSIX; version auto-resolved from git tag
sign_for_local_test.bat     # self-sign the package for local install testing
```

The build requires HEAD at a git tag (e.g. `v0.3.0`). For local testing, `sign_for_local_test.bat` auto-creates a self-signed certificate and trusts it — run as Administrator.

---

## Installation

HushSnap is distributed exclusively through the **Microsoft Store**. Search for "HushSnap" in the Store app or visit the product page to install.

---

