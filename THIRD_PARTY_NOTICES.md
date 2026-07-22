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
