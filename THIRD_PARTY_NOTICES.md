# Third-Party Notices

This file records third-party attributions and license references used by
HushSnap.

## Text-Grab (MIT)

- Project: [Text-Grab](https://github.com/TheJoeFin/Text-Grab)
- License: MIT License
- Usage in HushSnap: OCR workflow and product ideas referenced while designing
  HushSnap features.

## RapidOCR (Apache 2.0)

- Project: [RapidOCR](https://github.com/RapidAI/RapidOCR)
- Author: SWHL
- License: Apache License 2.0
- Usage in HushSnap: The `rapidocr` Python package (v3.9.1+) runs PP-OCRv6
  ONNX models via onnxruntime in-process. Models are bundled with the package.

## Pinta (MIT)

- Project: [Pinta](https://github.com/PintaProject/Pinta)
- License: MIT License
- Usage in HushSnap: The built-in image editor's toolset design was referenced
  from Pinta, an open-source raster graphics editor.

## ShareX (GPL-3.0)

- Project: [ShareX](https://github.com/ShareX/ShareX)
- Copyright: © 2007-2026 ShareX Team
- License: GNU General Public License v3.0
- Usage in HushSnap: Used as a design template and reference for the
  screen-capture workflow; the HushSnap implementation is written in
  Python/Qt.

## PyQt6 (GPL-3.0-only)

- Project: [PyQt6](https://www.riverbankcomputing.com/software/pyqt/)
- Copyright: © Riverbank Computing Limited
- License: GNU General Public License v3.0 (GPL-3.0-only); a commercial license is
  also available from Riverbank.
- Usage in HushSnap: The entire GUI is built on PyQt6 — windows, tray icon,
  thumbnails, popups, and the image editor. HushSnap uses the GPL version;
  this is the reason HushSnap itself is distributed under GPL v3.0.

## PyQt6-Qt6 (LGPL-3.0-only)

- Project: [PyQt6-Qt6](https://www.riverbankcomputing.com/software/pyqt/)
- Copyright: © The Qt Company Ltd. (Qt libraries); packaging by Riverbank
  Computing Limited
- License: GNU Lesser General Public License v3.0 (LGPL-3.0-only). Qt is also
  available under GPL and commercial licenses.
- Usage in HushSnap: The actual Qt 6 shared libraries (QtWidgets, QtGui,
  QtCore, etc.) that PyQt6 wraps. Bundled as a wheel automatically installed
  with PyQt6.

## Pillow (MIT-CMU)

- Project: [Pillow](https://python-pillow.org/)
- Copyright: © 2010+ Jeffrey 'Alex' Clark and contributors; © 1997–2011
  Secret Labs AB; © 1995–2011 Fredrik Lundh
- License: MIT-CMU (historically known as the PIL License)
- Usage in HushSnap: Image loading, format conversion, and pixel access for
  the entire capture → editor → save pipeline.

## onnxruntime (MIT)

- Project: [ONNX Runtime](https://onnxruntime.ai/)
- Copyright: © Microsoft Corporation
- License: MIT License
- Usage in HushSnap: Runs the PP-OCRv6 ONNX models (text detection and
  recognition) in-process via the `rapidocr` package. Pinned to 1.21.1.

## opencv-python (Apache 2.0, minimal custom build)

- Project: [OpenCV](https://opencv.org/)
- Copyright: © OpenCV contributors
- License: Apache License 2.0
- Usage in HushSnap: Image processing for OCR pre/post-processing and editor
  transforms. The shipped MSIX uses a purpose-built 24.8 MB static `cv2.pyd`
  (70% smaller than the official wheel; OCR output byte-identical). See the
  [Minimal cv2 build](#minimal-cv2-build) section in the README for details.

## PP-OCR Models (Apache 2.0)

- Project: [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)
- Copyright: Baidu Inc.
- License: Apache License 2.0
- Usage in HushSnap: Pre-trained PP-OCRv6 ONNX model files are bundled with
  the `rapidocr` Python package and loaded at runtime.
  - `PP-OCRv6_det_small.onnx` - text detection
  - `PP-OCRv6_rec_small.onnx` - text recognition
  - `ppocrv6_dict.txt` - character dictionary
  - `ch_ppocr_mobile_v2.0_cls_mobile.onnx` - text orientation classifier (constructed at engine init even with use_cls=False; not called at inference)
