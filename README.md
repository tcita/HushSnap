# HushSnap

HushSnap is an offline screenshot & OCR tool - local, fully offline, with snappy recognition on everyday captures. Your screenshots and OCR results never leave your device. It lives in your system tray. Press a hotkey and a crosshair overlay appears instantly - select a region or click for the full screen, and the shot lands on your clipboard immediately. No pre-launching menus or windows; the UI surfaces only when you need it.

[HushSnap Website](https://tcita.github.io/HushSnap/)

[Demo Video](https://youtu.be/untWW6_Ea3M)

<table>
  <tr>
    <td align="center"><b>Click thumbnail → OCR</b></td>
    <td align="center"><b>Drag thumbnail → Save</b></td>
    <td align="center"><b>Edit → Redact</b></td>
  </tr>
  <tr>
    <td><img src="assets/demo-ocr.gif" alt="OCR demo" width="280"></td>
    <td><img src="assets/demo-drag-save.gif" alt="Drag and save demo" width="280"></td>
    <td><img src="assets/demo-editor-redact.gif" alt="Editor redaction demo" width="280"></td>
  </tr>
</table>

After capture, a thumbnail slides in at the bottom-right corner of your screen. This thumbnail is the heart of HushSnap's post-capture flow:

- **Hover** to pause auto-hide and reveal an action pill with **Edit**, **Pin**, and **Close** buttons.
- **Drag and drop** the thumbnail anywhere to save the image - into a chat, an email, a folder, or another app.
- **Left-click** the thumbnail to run **offline OCR**. Recognized text opens in a floating popup where you can edit, copy, resize, or pin the window to keep it visible. By default text is also auto-copied to your clipboard.
- **Edit** (the brush button on the action pill) opens the built-in **image editor** - annotate, redact, crop, rotate, resize, then copy or save. See [Image Editor](#image-editor) below.
- **Right-click** the thumbnail for **Copy Text from Image** (silent OCR - text straight to the clipboard, no popup) or **Save to Desktop**.
- Optionally overlay a decorative **vine ornament** on the thumbnail’s top-left corner (enable it in Settings - Capture). It is purely cosmetic; it does not change the thumbnail’s hit area or any behavior.

HushSnap is controlled from the **system tray**: single-click the tray icon to start a capture, or right-click to access Screenshot, Settings, the config folder, and Quit. The **Settings** dialog (with General, Capture, and OCR pages) lets you rebind the global hotkey (default `Alt+Q`), switch UI language, change how long the thumbnail stays visible, toggle auto-copy behavior, adjust OCR font size, toggle a decorative vine ornament on the thumbnail corner, and more.

Because HushSnap works at the Windows level, the hotkey responds from any app, anywhere. Everything runs locally - your screenshots and OCR results never leave your device.

As the project continues to evolve, some parts of this README may occasionally lag behind the latest behavior or features.

## Image Editor

The built-in image editor opens from the thumbnail's **Edit** button, or from the right-click menu on a pinned image. It's a lightweight, dark-themed window for touching up a capture before sharing - annotate, redact, crop, rotate, resize, then copy to clipboard or save. The window opens centered on the cursor's screen and remembers its size across sessions.

**Tools:** rectangle / ellipse / line (color, size, fill, optional arrowhead), text (font, size, color), brush, highlighter, mosaic (pixelate/redact), eraser, pan, and the crop / rotate / resize transforms. Plus undo/redo (`Ctrl+Z` / `Ctrl+Y`), fit-to-viewport (`Ctrl+0`), copy, and Save As… (`Ctrl+S`, PNG / JPEG / BMP). Transforms run as atomic sessions - **Esc** cancels - so the state stays unambiguous mid-edit.


## OCR Engine

HushSnap uses [**PP-OCRv6**](https://github.com/PaddlePaddle/PaddleOCR) as its sole OCR engine:

- **PP-OCRv6 (via RapidOCR):** Runs PP-OCRv6 small ONNX models in-process via the [`rapidocr`](https://github.com/RapidAI/RapidOCR) Python package (Apache 2.0). No external dependencies or language packs needed. Works offline. Uses a unified multilingual model covering **50 languages** in a single 7.7M-parameter model - surpassing the accuracy of the previous-generation v5 server model at a fraction of the size.

The engine supports the following 50 languages out of the box:

**Core:** Simplified Chinese, Traditional Chinese, English, Japanese

**Latin-script (46):** French, German, Italian, Spanish, Portuguese, Dutch, Polish, Romanian, Czech, Swedish, Norwegian, Danish, Finnish, Hungarian, Turkish, Vietnamese, Indonesian, Malay, Azerbaijani, Afrikaans, Bosnian, Croatian, Welsh, Estonian, Irish, Icelandic, Kurdish, Lithuanian, Latvian, Maltese, Māori, Occitan, Slovak, Slovenian, Albanian, Swahili, Tagalog, Uzbek, Latin, Serbian (Latin), Catalan, Basque, Galician, Luxembourgish, Romansh, Quechua

### Minimal cv2 build

`rapidocr` is the only runtime consumer of `cv2`, and it touches just 30 symbols across `core`/`imgproc`/`imgcodecs` (audited by `tests/test_cv2_symbol_audit.py`, which fails loud if a `rapidocr` upgrade pulls a pruned-module symbol). The official `opencv-python` wheel ships an 82 MB monolithic `cv2.pyd` with ~90% of its modules (dnn, video, features2d, ...) never touched. HushSnap ships a purpose-built **24.8 MB static `cv2.pyd`** (OpenCV 5.0.0, `WITH_IPP=OFF` + dead codecs/GPU/ffmpeg stripped) — 70% smaller, OCR output byte-identical to the official wheel.

The minimal cv2 lives in two committed places, kept in sync by `scripts/build/build_minimal_opencv.ps1`:

- **`third_party/cv2.cp313-win_amd64.pyd`** — the binary `HushSnap.spec`'s `swap_minimal_cv2` swaps in as the shipped `cv2.pyd` (the frozen `cv2/` package layout still comes from the `opencv-python` wheel).
- **`cv2/`** (repo root) — a complete minimal cv2 *package* (12 runtime `.py` + `cv2.pyd`) that **development and tests import directly**. Because `sys.path[0]` is the repo root, `import cv2` resolves here ahead of the site-packages wheel — so development runs against the same minimal cv2 users get, with `cv2.dnn`/`videoio`/etc. absent (a misuse fails immediately, mirroring production, instead of passing in dev and breaking for users). `.pyi` stubs are excluded: they are runtime-dead and would make `hasattr(cv2, "dnn")` misleadingly return `True`.

A version-consistency assertion in `HushSnap.spec` (`_assert_cv2_version_consistent`) cross-checks the pip `opencv-python` package version against the version embedded in the minimal pyd, failing the build on drift. `opencv-python==5.0.0.93` is pinned in `requirements.txt`.

Rebuild the minimal cv2 when OpenCV or the target Python changes:
```powershell
pwsh scripts/build/build_minimal_opencv.ps1 -NoIPP -ForceClean
# syncs both third_party/ and cv2/ automatically
```

## Third-Party Acknowledgment

The OCR workflow and product ideas in this project were inspired by the
[Text-Grab](https://github.com/TheJoeFin/Text-Grab) project, which is licensed
under the MIT License. The image editor's toolset design was referenced from
[Pinta](https://github.com/PintaProject/Pinta) (MIT). [ShareX](https://github.com/ShareX/ShareX)
(GPL-3.0) served as an excellent benchmark and reference throughout development -
see `THIRD_PARTY_NOTICES.md` for details.

## License

HushSnap is distributed under the **GNU General Public License v3.0**
([LICENSE.md](LICENSE.md)). See `THIRD_PARTY_NOTICES.md` for third-party
attribution.

Copyright © 2026 HushSnap.

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
- **OCR Inspection:** Debug mode saves detection-box images to the data directory. Right-click the tray icon → "Config Folder" to open this location directly.
  - `ocr_debug_words.png` - raw PP-OCR detector word boxes (red)
  - `ocr_debug_lines.png` - post-clustering line boxes (green, L0/L1/… badges)
  - Source run: `%LOCALAPPDATA%\HushSnap_Dev\`
  - Packaged run (MSIX): `%LOCALAPPDATA%\Packages\<PackageFamilyName>\LocalState\`
  - Packaged run (PyInstaller standalone): `%LOCALAPPDATA%\HushSnap\`

## Building (MSIX)

```powershell
build_msix.bat              # build unsigned MSIX; version auto-resolved from git tag
sign_for_local_test.bat     # self-sign the package for local install testing
```

The build requires HEAD at a git tag (e.g. `v0.3.0`). For local testing, `sign_for_local_test.bat` auto-creates a self-signed certificate and trusts it - run as Administrator.

---

## Installation

HushSnap is distributed exclusively through the [Microsoft Store](https://apps.microsoft.com/detail/9p0qzv5z8njz).

---

