"""Prepare QImage for OCR engines — format/DPR adaptation and safe padding.

Steps (all format/coordinate; NOT recognition enhancement):
- DPR = 1.0   (grabWindow preserves the screen's native DPR; OCR
  engines operate in raw-pixel space and ignore the metadata)
- Format = ARGB32  (consistent baseline for all engines)
- Safe-pad  (background-sampled pad to 960 px minimum side so that
  OCR engines receive enough pixel resolution to work with)
"""

from dataclasses import dataclass, field

from PyQt6 import QtCore, QtGui

DEFAULT_OCR_SCALE_FACTOR = 1.0

# Why 960px? 
# RapidOCR (PP-OCR) detection models (DBNet) typically have a 'limit_side_len' of 736 or 960.
# If an image's short side is below this limit, the engine performs a 'cv2.resize' upscaling.
# For tiny images (e.g. 32px), this results in a ~23x upscale, causing catastrophic 
# interpolation blur that destroys character features. By padding to 960px, we force 
# the engine to use a 1:1 scaling ratio, preserving original pixel fidelity.
SAFE_PAD_MIN_SIDE = 960


@dataclass(frozen=True)
class OcrPreprocessSettings:
    """Placeholder for future preprocessing configuration flags."""


@dataclass(frozen=True)
class OcrPreprocessStep:
    key: str
    label: str
    enabled: bool
    details: str = ""


@dataclass
class OcrPreprocessResult:
    image: QtGui.QImage
    settings: OcrPreprocessSettings
    resolved_scale_factor: float = DEFAULT_OCR_SCALE_FACTOR
    steps: list[OcrPreprocessStep] = field(default_factory=list)
    original_size: QtCore.QSize = field(default_factory=QtCore.QSize)

    @property
    def applied_steps(self) -> list[OcrPreprocessStep]:
        return [step for step in self.steps if step.enabled]

    def summary(self) -> str:
        parts: list[str] = []
        for step in self.applied_steps:
            if step.details:
                parts.append(f"{step.label} ({step.details})")
            else:
                parts.append(step.label)
        return " -> ".join(parts)


def _qimage_to_bgr(image: QtGui.QImage):
    """Convert a QImage to a writable NumPy BGR array (h, w, 3)."""
    import numpy as np

    rgb32 = image.convertToFormat(QtGui.QImage.Format.Format_RGB32)
    w = rgb32.width()
    h = rgb32.height()
    ptr = rgb32.bits()
    ptr.setsize(rgb32.sizeInBytes())
    # RGB32 stores 0xffRRGGBB → channel order B, G, R, X after reshape
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
    bgr = np.zeros((h, w, 3), dtype=np.uint8)
    bgr[:, :, 0] = arr[:, :, 0]  # B
    bgr[:, :, 1] = arr[:, :, 1]  # G
    bgr[:, :, 2] = arr[:, :, 2]  # R
    return bgr


def _bgr_to_qimage(bgr) -> QtGui.QImage:
    """Convert a BGR NumPy array (h, w, 3) back to ARGB32 QImage."""
    import numpy as np

    h, w = bgr.shape[:2]
    argb = np.zeros((h, w, 4), dtype=np.uint8)
    argb[:, :, 0] = bgr[:, :, 0]  # B
    argb[:, :, 1] = bgr[:, :, 1]  # G
    argb[:, :, 2] = bgr[:, :, 2]  # R
    argb[:, :, 3] = 255            # A (opaque)
    result = QtGui.QImage(argb.data, w, h, w * 4, QtGui.QImage.Format.Format_ARGB32)
    return result.copy()  # own the memory


def _pad_if_small(image: QtGui.QImage, min_side: int = SAFE_PAD_MIN_SIDE) -> QtGui.QImage:
    """Pad image to ensure both sides are at least *min_side*.
    Uses the average color of the four corners for padding to blend better.
    Content is centered; not stretched."""
    import numpy as np

    w = image.width()
    h = image.height()

    if min(w, h) >= min_side:
        return image

    target_w = max(w, min_side)
    target_h = max(h, min_side)
    bgr = _qimage_to_bgr(image)

    # Sample background color from 4 corners. 
    # Why? Static white/black borders create high-contrast artificial edges 
    # that can be misinterpreted by the detection model as UI borders or 
    # noise, leading to false negatives or shifted bounding boxes.
    corners = [bgr[0, 0], bgr[0, -1], bgr[-1, 0], bgr[-1, -1]]
    bg_color = np.mean(corners, axis=0).astype(np.uint8)

    y_off = (target_h - h) // 2
    x_off = (target_w - w) // 2

    padded = np.full((target_h, target_w, 3), bg_color, dtype=np.uint8)
    padded[y_off:y_off + h, x_off:x_off + w] = bgr

    return _bgr_to_qimage(padded)


def prepare_ocr_image(image_or_pixmap) -> QtGui.QImage:
    """Unify pixel format to ARGB32 and reset DPR to 1.0."""
    if isinstance(image_or_pixmap, QtGui.QPixmap):
        image = image_or_pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    else:
        image = image_or_pixmap.convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    if image.devicePixelRatio() != 1.0:
        image.setDevicePixelRatio(1.0)
    return image


def run_minimal_pipeline(
    image_or_pixmap,
    settings: OcrPreprocessSettings | None = None,
) -> OcrPreprocessResult:
    """Prepare the source image for OCR.

    Steps (always applied):
    1. Format/DPR normalisation (ARGB32, DPR 1.0)
    2. Safe background-sampled pad when any side < 960 px
    """
    _ = settings or OcrPreprocessSettings()
    steps: list[OcrPreprocessStep] = []

    image = prepare_ocr_image(image_or_pixmap)
    w0, h0 = image.width(), image.height()

    steps.append(
        OcrPreprocessStep(
            key="prepare_ocr",
            label="Prepare OCR Input",
            enabled=True,
            details="DPR 1.0, ARGB32",
        )
    )

    image = _pad_if_small(image)
    if image.width() != w0 or image.height() != h0:
        steps.append(
            OcrPreprocessStep(
                key="safe_pad",
                label="Safe Pad",
                enabled=True,
                details=f"{w0}x{h0} -> {image.width()}x{image.height()} (bg sampled)",
            )
        )

    return OcrPreprocessResult(
        image=image,
        settings=OcrPreprocessSettings(),
        resolved_scale_factor=1.0,
        steps=steps,
        original_size=QtCore.QSize(w0, h0),
    )
