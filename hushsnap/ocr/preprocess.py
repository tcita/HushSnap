"""Prepare QImage for OCR engines — format and DPR adaptation only.

This module is NOT recognition enhancement. It does no sharpening,
binarization, grayscale conversion, or denoising. It exists as a
boundary where future OCR input preparation steps can be added.

Currently the single step ensures:
- DPR = 1.0  (grabWindow preserves the screen's native DPR; OCR
  engines operate in raw-pixel space and ignore the metadata)
- Format = ARGB32  (consistent baseline for all engines)
"""

from dataclasses import dataclass, field

from PyQt6 import QtGui

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
    """Unify pixel format to ARGB32 and reset DPR to 1.0.

    This is format/coordinate adaptation, NOT recognition enhancement.
    OCR engines work in raw pixels and ignore DPR metadata, so we
    normalise the coordinate space to avoid downstream confusion.
    """
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
    """Prepare the source image for OCR (format/DPR adaptation only).

    Not recognition enhancement — no sharpening, binarisation, grayscale
    conversion, or denoising.  The single step ensures every engine
    receives a QImage in a predictable format and coordinate space.
    """
    _ = settings or OcrPreprocessSettings()  # accepted for future configuration
    steps: list[OcrPreprocessStep] = []

    image = prepare_ocr_image(image_or_pixmap)

    steps.append(
        OcrPreprocessStep(
            key="prepare_ocr",
            label="Prepare OCR Input",
            enabled=True,
            details="DPR 1.0, ARGB32",
        )
    )

    return OcrPreprocessResult(
        image=image,
        settings=OcrPreprocessSettings(),
        resolved_scale_factor=1.0,
        steps=steps,
    )
