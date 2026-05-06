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

## RapidOCR-json (MIT)

- Project: [RapidOCR-json](https://github.com/hiroi-sora/RapidOCR-json)
- Author: hiroi-sora
- License: MIT License
- Usage in HushSnap: The executable `RapidOCR-json.exe` is bundled under
  `rapidocr/` and spawned as a subprocess to perform offline OCR inference.
  HushSnap communicates with it via JSON over stdin/stdout.

## PaddleOCR / PP-OCR Models (Apache 2.0)

- Project: [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)
- Copyright: Baidu Inc.
- License: Apache License 2.0
- Usage in HushSnap: Pre-trained PP-OCR ONNX model files (detection,
  classification, recognition) and the character dictionary are bundled under
  `rapidocr/models/` and loaded by RapidOCR-json at runtime.
  - `ch_PP-OCRv3_det_infer.onnx` — text detection
  - `ch_ppocr_mobile_v2.0_cls_infer.onnx` — text orientation classification
  - `rec_ch_PP-OCRv4_infer.onnx` — text recognition (simplified Chinese v4)
  - `dict_chinese.txt` — character dictionary

### Compliance Note

The above components are redistributed in binary form under the terms of the
Apache License 2.0. No source modifications have been made to RapidOCR-json
or the PP-OCR models. The original license texts can be found at:
- https://github.com/hiroi-sora/RapidOCR-json
- https://github.com/PaddlePaddle/PaddleOCR

