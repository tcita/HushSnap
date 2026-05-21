from dataclasses import dataclass, field

from PyQt6 import QtCore, QtGui

DEFAULT_OCR_SCALE_FACTOR = 1.0


@dataclass(frozen=True)
class OcrPreprocessSettings:
    auto_scale: bool = True
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


DEFAULT_OCR_PREPROCESS_SETTINGS = OcrPreprocessSettings()


def resolve_scale_factor(
    settings: OcrPreprocessSettings,
    auto_scale_factor: float | None = None,
) -> tuple[float, str]:
    if not settings.auto_scale:
        return 1.0, "off"
    if auto_scale_factor is not None and auto_scale_factor > 0:
        return auto_scale_factor, "auto"
    return 1.0, "auto"


def normalize_source_image(image_or_pixmap) -> QtGui.QImage:
    """Normalize DPR and pixel format to avoid HiDPI offset artifacts."""
    if isinstance(image_or_pixmap, QtGui.QPixmap):
        image = image_or_pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    else:
        image = image_or_pixmap.convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    if image.devicePixelRatio() != 1.0:
        image.setDevicePixelRatio(1.0)
    return image.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)


def scale_image(image: QtGui.QImage, scale_factor: float) -> QtGui.QImage:
    """Resize with smooth interpolation when scale is meaningfully different."""
    if abs(scale_factor - 1.0) <= 0.01:
        return image
    return image.scaled(
        max(1, int(round(image.width() * scale_factor))),
        max(1, int(round(image.height() * scale_factor))),
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    )


def default_preprocess_settings() -> OcrPreprocessSettings:
    return DEFAULT_OCR_PREPROCESS_SETTINGS


def run_minimal_pipeline(
    image_or_pixmap,
    settings: OcrPreprocessSettings | None = None,
    resolved_scale_factor: float | None = None,
) -> OcrPreprocessResult:
    """
    Minimal pipeline for modern deep-learning engines.
    Only performs normalization and scaling, skipping binarization/inversion.
    """
    active_settings = settings or DEFAULT_OCR_PREPROCESS_SETTINGS
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

    # 2. Scale
    effective_scale_factor, _ = resolve_scale_factor(active_settings, auto_scale_factor=resolved_scale_factor)
    image = scale_image(image, effective_scale_factor)
    steps.append(
        OcrPreprocessStep(
            key="scale",
            label="Scale",
            enabled=active_settings.auto_scale,
            details=f"{effective_scale_factor:.2f}x" if active_settings.auto_scale else "1.00x",
        )
    )

    # Final conversion to RGB32 for OCR engine compatibility
    image = image.convertToFormat(QtGui.QImage.Format.Format_RGB32)

    return OcrPreprocessResult(
        image=image,
        settings=active_settings,
        resolved_scale_factor=effective_scale_factor,
        steps=steps,
    )
