"""Prepare QImage for OCR engines — format/DPR adaptation.

Steps (all format/coordinate; NOT recognition enhancement):
- DPR = 1.0   (grabWindow preserves the screen's native DPR; OCR
  engines operate in raw-pixel space and ignore the metadata)
- Format = RGB32   (4-byte-aligned, [B,G,R,X] on little-endian;
  ARGB32 is byte-identical for the first 3 channels and also
  tolerated by downstream code)
"""

from dataclasses import dataclass, field

from PyQt6 import QtCore, QtGui
try:
    from PIL import Image
except ImportError:
    Image = None

DEFAULT_OCR_SCALE_FACTOR = 1.0


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


def prepare_ocr_image(image_or_pixmap) -> QtGui.QImage:
    """Unify pixel format to RGB32 and reset DPR to 1.0.

    RGB32 is guaranteed 4-byte-aligned (no row padding), so downstream code
    can safely reshape via ``np.frombuffer(bits()).reshape(h, w, 4)`` without
    worrying about stride mismatches.

    Supports QPixmap, QImage, and PIL Image.
    """
    if isinstance(image_or_pixmap, QtGui.QPixmap):
        image = image_or_pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_RGB32)
    elif Image and isinstance(image_or_pixmap, Image.Image):
        # Convert PIL Image to QImage
        pil_img = image_or_pixmap
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        image = QtGui.QImage(
            data,
            pil_img.size[0],
            pil_img.size[1],
            QtGui.QImage.Format.Format_RGBA8888
        ).copy().convertToFormat(QtGui.QImage.Format.Format_RGB32)
    else:
        # Assume it's a QImage
        image = image_or_pixmap.convertToFormat(QtGui.QImage.Format.Format_RGB32)

    if image.devicePixelRatio() != 1.0:
        image.setDevicePixelRatio(1.0)
    return image


def run_minimal_pipeline(
    image_or_pixmap,
    settings: OcrPreprocessSettings | None = None,
) -> OcrPreprocessResult:
    """Prepare the source image for OCR.

    Steps (always applied):
    1. Format/DPR normalisation (RGB32, DPR 1.0)
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
            details="DPR 1.0, RGB32",
        )
    )

    return OcrPreprocessResult(
        image=image,
        settings=OcrPreprocessSettings(),
        resolved_scale_factor=1.0,
        steps=steps,
        original_size=QtCore.QSize(w0, h0),
    )
