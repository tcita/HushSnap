# HushSnap

HushSnap is a lightweight screenshot tool built to stay quietly in the background and be ready whenever you need it. It lives in the system tray, runs silently, and lets you instantly capture part or all of your screen, with every shot copied straight to your clipboard without interrupting your flow.

Designed around global shortcuts, HushSnap can capture from anywhere in Windows while staying out of sight and out of the way. The experience is intentionally minimal, silent, and fast, with customizable hotkeys that let you shape the workflow around your own habits. **OCR is supported locally on your Windows device and does not require an internet connection.**

As the project continues to evolve, some parts of this README may occasionally lag behind the latest behavior or features.

## OCR Engine

HushSnap uses **RapidOCR** as its sole OCR engine:

- **RapidOCR:** Runs PP-OCRv5 ONNX models in-process via the [`rapidocr`](https://github.com/RapidAI/RapidOCR) Python package (Apache 2.0). No external dependencies or language packs needed. Works offline. Uses a unified CJK+Latin model. Ships with a Chinese-centric embedded model but can recognize text in other languages as well.

The engine has been tested on standard screen captures (web, apps, UIs) within HushSnap's own capture→preprocess→OCR pipeline. **Simplified Chinese, Traditional Chinese, and English** are verified to work well. The underlying model may recognize additional languages (e.g., Japanese, German, Spanish), but their accuracy is unverified — use at your own discretion.

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

## Installation

HushSnap is distributed exclusively through the **Microsoft Store**. Search for "HushSnap" in the Store app or visit the product page to install.

---

