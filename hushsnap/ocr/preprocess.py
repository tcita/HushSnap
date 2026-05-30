from dataclasses import dataclass, field

from PyQt6 import QtGui

DEFAULT_OCR_SCALE_FACTOR = 1.0


@dataclass(frozen=True)
class OcrPreprocessSettings:
    normalize_source: bool = True


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


def normalize_source_image(image_or_pixmap) -> QtGui.QImage:
    """Normalize DPR and pixel format to avoid HiDPI offset artifacts."""
    if isinstance(image_or_pixmap, QtGui.QPixmap):
        image = image_or_pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    else:
        image = image_or_pixmap.convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    if image.devicePixelRatio() != 1.0:
        image.setDevicePixelRatio(1.0)
    return image.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)


def run_minimal_pipeline(
    image_or_pixmap,
    settings: OcrPreprocessSettings | None = None,
) -> OcrPreprocessResult:
    """Normalize source image to Grayscale8 for OCR engine compatibility."""
    active_settings = settings or OcrPreprocessSettings()
    steps: list[OcrPreprocessStep] = []

    # 1. Normalize (Ensure 1.0 DPR and 8-bit grayscale for speed)
    if active_settings.normalize_source:
        image = normalize_source_image(image_or_pixmap)
    else:
        if isinstance(image_or_pixmap, QtGui.QPixmap):
            image = image_or_pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
        else:
            image = image_or_pixmap.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)

    steps.append(
        OcrPreprocessStep(
            key="normalize_source",
            label="Minimal Norm",
            enabled=True,
            details="Grayscale8",
        )
    )

    return OcrPreprocessResult(
        image=image,
        settings=active_settings,
        resolved_scale_factor=1.0,
        steps=steps,
    )
