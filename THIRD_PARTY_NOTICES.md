# Third-Party Notices

This file records third-party attributions and license references used by
HushSnap.

## Text-Grab (MIT)

- Project: [Text-Grab](https://github.com/TheJoeFin/Text-Grab)
- License: MIT License
- Usage in HushSnap: OCR-related product ideas and implementation approach were
  referenced while designing HushSnap features.

### Compliance Note

HushSnap references the Text-Grab approach and acknowledges the source above.
If any upstream source code is copied or adapted in the future, keep the
original MIT copyright notice and license text together with those copied
portions.

## RapidOCR (Apache 2.0)

- Project: [RapidOCR](https://github.com/RapidAI/RapidOCR)
- Author: SWHL
- License: Apache License 2.0
- Usage in HushSnap: The `rapidocr` Python package (v3.8.1+) runs PP-OCR
  ONNX models via onnxruntime in-process. Models are bundled with the package.

## Pinta (MIT)

- Project: [Pinta](https://github.com/PintaProject/Pinta)
- License: MIT License
- Usage in HushSnap: The built-in image editor's toolset design (brush,
  highlighter, eraser, mosaic, text, pan, undo/redo, zoom) and interaction
  patterns were referenced from Pinta, an open-source raster graphics editor.

## PP-OCR Models (Apache 2.0)

- Project: [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)
- Copyright: Baidu Inc.
- License: Apache License 2.0
- Usage in HushSnap: Pre-trained PP-OCRv5 ONNX model files are bundled with
  the `rapidocr` Python package and loaded at runtime.
  - `ch_PP-OCRv5_det_mobile.onnx` — text detection
  - `ch_ppocr_mobile_v2.0_cls_mobile.onnx` — text orientation classification
  - `ch_PP-OCRv5_rec_mobile.onnx` — text recognition
  - `ppocr_keys_v1.txt` — character dictionary


