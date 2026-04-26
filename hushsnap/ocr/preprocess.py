from dataclasses import dataclass, field

from PyQt6 import QtCore, QtGui

DEFAULT_AUTO_PADDING_OFFSET_PX = 8
DEFAULT_AUTO_PADDING_MIN_SIZE_PX = 64
DEFAULT_OCR_SCALE_FACTOR = 1.0


@dataclass(frozen=True)
class OcrPreprocessSettings:
    auto_scale: bool = True
    normalize_source: bool = True
    auto_add_padding: bool = True
    smooth: bool = True
    bolden_text: bool = True
    auto_invert: bool = True
    high_contrast: bool = True


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


def image_bytes(image: QtGui.QImage) -> memoryview:
    """Return a writable byte view for direct pixel manipulation."""
    bits = image.bits()
    bits.setsize(image.sizeInBytes())
    return memoryview(bits)


def otsu_threshold(grayscale_image: QtGui.QImage) -> int:
    """Compute Otsu threshold for an 8-bit grayscale image."""
    data = image_bytes(grayscale_image)
    histogram = [0] * 256
    for value in data:
        histogram[value] += 1

    total = len(data)
    if total == 0:
        return 160

    sum_total = 0
    for value in range(256):
        sum_total += value * histogram[value]

    sum_background = 0.0
    weight_background = 0
    max_variance = -1.0
    threshold = 160

    for value in range(256):
        weight_background += histogram[value]
        if weight_background == 0:
            continue

        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break

        sum_background += value * histogram[value]
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance_between = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance_between > max_variance:
            max_variance = variance_between
            threshold = value

    return threshold


def smooth_grayscale_image(image: QtGui.QImage) -> QtGui.QImage:
    grayscale = image.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
    width = max(1, grayscale.width())
    height = max(1, grayscale.height())
    return grayscale.scaled(
        width * 2,
        height * 2,
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    ).scaled(
        width,
        height,
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QtGui.QImage.Format.Format_Grayscale8)


def should_invert_grayscale(grayscale_image: QtGui.QImage, threshold: int) -> bool:
    data = image_bytes(grayscale_image)
    dark_pixel_count = 0
    for pixel in data:
        if pixel <= threshold:
            dark_pixel_count += 1
    return dark_pixel_count > (len(data) // 2)


def invert_grayscale_in_place(grayscale_image: QtGui.QImage) -> None:
    data = image_bytes(grayscale_image)
    for index in range(len(data)):
        data[index] = 255 - data[index]


def stretch_grayscale_contrast(grayscale_image: QtGui.QImage) -> QtGui.QImage:
    data = image_bytes(grayscale_image)
    histogram = [0] * 256
    for pixel in data:
        histogram[pixel] += 1

    total = len(data)
    low_target = int(total * 0.02)
    high_target = int(total * 0.98)

    low = 0
    cumulative = 0
    for value in range(256):
        cumulative += histogram[value]
        if cumulative >= low_target:
            low = value
            break

    high = 255
    cumulative = 0
    for value in range(255, -1, -1):
        cumulative += histogram[value]
        if (total - cumulative) <= high_target:
            high = value
            break

    if high <= low:
        low, high = 0, 255

    scale = 255.0 / max(1, (high - low))
    for index in range(len(data)):
        value = int((data[index] - low) * scale)
        if value < 0:
            value = 0
        elif value > 255:
            value = 255

        if value > 178:
            value = min(255, int(178 + (value - 178) * 1.9))
        elif value < 120:
            value = max(0, int(value * 0.88))

        data[index] = value

    return grayscale_image


def preprocess_grayscale(
    image: QtGui.QImage,
    grayscale_smooth: bool = True,
    auto_invert: bool = True,
) -> tuple[QtGui.QImage, bool]:
    """Smooth grayscale input and optionally invert dark-background captures. Assumes input is already Grayscale8."""
    if grayscale_smooth:
        # smooth_grayscale_image already handles scaling up/down and ensures Grayscale8
        grayscale = smooth_grayscale_image(image)
    else:
        grayscale = image.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)

    threshold = otsu_threshold(grayscale)
    was_inverted = False

    if auto_invert and should_invert_grayscale(grayscale, threshold):
        invert_grayscale_in_place(grayscale)
        was_inverted = True

    return grayscale, was_inverted


def to_high_contrast(
    image: QtGui.QImage,
    grayscale_smooth: bool = True,
    auto_invert: bool = True,
) -> tuple[QtGui.QImage, bool]:
    """Convert image to OCR-friendly high-contrast RGB output."""
    grayscale = image.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
    grayscale, was_inverted = preprocess_grayscale(
        grayscale,
        grayscale_smooth=grayscale_smooth,
        auto_invert=auto_invert,
    )
    contrasted = stretch_grayscale_contrast(grayscale)
    return contrasted.convertToFormat(QtGui.QImage.Format.Format_RGB32), was_inverted


def normalize_source_image(pixmap: QtGui.QPixmap) -> QtGui.QImage:
    """Normalize DPR and pixel format to avoid HiDPI offset artifacts."""
    image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    if image.devicePixelRatio() != 1.0:
        image.setDevicePixelRatio(1.0)
    # Optimization: Early conversion to Grayscale8 for the entire pipeline
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


def create_padded_canvas(image: QtGui.QImage) -> tuple[QtGui.QImage, int]:
    """Add a small quiet zone only when the image is below the target minimum size."""
    if image.width() >= DEFAULT_AUTO_PADDING_MIN_SIZE_PX and image.height() >= DEFAULT_AUTO_PADDING_MIN_SIZE_PX:
        return image, 0

    background_color = image.pixelColor(0, 0)
    if background_color.alpha() < 255:
        background_color.setAlpha(255)

    pad = DEFAULT_AUTO_PADDING_OFFSET_PX
    canvas = QtGui.QImage(
        max(image.width() + (pad * 2), DEFAULT_AUTO_PADDING_MIN_SIZE_PX + (pad * 2)),
        max(image.height() + (pad * 2), DEFAULT_AUTO_PADDING_MIN_SIZE_PX + (pad * 2)),
        image.format(),
    )
    canvas.fill(background_color)
    return canvas, pad


def draw_boldened_text(dst: QtGui.QImage, src: QtGui.QImage, pad: int) -> None:
    """
    Draw source image with small offsets to thicken OCR strokes.
    Triple drawing is a pragmatic heuristic that improves OCR confidence.
    """
    if dst is src:
        src = src.copy()

    painter = QtGui.QPainter(dst)
    try:
        if dst.format() != src.format():
            # Ensure consistent drawing behavior
            src = src.convertToFormat(dst.format())
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(1.0)
        painter.drawImage(pad, pad, src)
        painter.drawImage(pad + 1, pad, src)
        painter.drawImage(pad, pad + 1, src)
    finally:
        painter.end()


def default_preprocess_settings() -> OcrPreprocessSettings:
    return DEFAULT_OCR_PREPROCESS_SETTINGS


def run_preprocess_pipeline(
    pixmap: QtGui.QPixmap,
    settings: OcrPreprocessSettings | None = None,
    resolved_scale_factor: float | None = None,
) -> OcrPreprocessResult:
    """
    Prepare an OCR-oriented image through a configurable single-path pipeline.
    """
    active_settings = settings or DEFAULT_OCR_PREPROCESS_SETTINGS
    steps: list[OcrPreprocessStep] = []

    # 1. Normalize and Convert to Grayscale early
    if active_settings.normalize_source:
        image = normalize_source_image(pixmap)
    else:
        image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
    steps.append(
        OcrPreprocessStep(
            key="normalize_source",
            label="Grayscale",
            enabled=True,
            details="8-bit",
        )
    )

    # 2. Scale
    effective_scale_factor, scale_mode = resolve_scale_factor(active_settings, auto_scale_factor=resolved_scale_factor)
    image = scale_image(image, effective_scale_factor)
    steps.append(
        OcrPreprocessStep(
            key="scale",
            label="Scale",
            enabled=active_settings.auto_scale,
            details=f"{effective_scale_factor:.2f}x" if active_settings.auto_scale else "1.00x",
        )
    )

    # 3. Padding
    source_before_padding = image
    pad = 0
    padding_applied = False
    if active_settings.auto_add_padding:
        image, pad = create_padded_canvas(source_before_padding)
        padding_applied = pad > 0
    steps.append(
        OcrPreprocessStep(
            key="padding",
            label="Padding",
            enabled=active_settings.auto_add_padding,
            details=f"+{pad}px" if padding_applied else "none",
        )
    )

    # 4. Bolden
    if active_settings.bolden_text:
        draw_boldened_text(image, source_before_padding, pad)
    elif padding_applied:
        # If we added padding but NOT boldening, we still need to draw the original image onto the canvas
        painter = QtGui.QPainter(image)
        try:
            painter.drawImage(pad, pad, source_before_padding)
        finally:
            painter.end()

    steps.append(
        OcrPreprocessStep(
            key="bolden",
            label="Bolden",
            enabled=active_settings.bolden_text,
            details="triple-draw" if active_settings.bolden_text else "off",
        )
    )

    # 5. Smooth
    if active_settings.smooth:
        image = smooth_grayscale_image(image)
    steps.append(
        OcrPreprocessStep(
            key="smooth",
            label="Smooth",
            enabled=active_settings.smooth,
            details="bilinear-resampling" if active_settings.smooth else "off",
        )
    )

    # 6. Auto Invert
    threshold = otsu_threshold(image)
    was_auto_inverted = False
    if active_settings.auto_invert and should_invert_grayscale(image, threshold):
        invert_grayscale_in_place(image)
        was_auto_inverted = True
    steps.append(
        OcrPreprocessStep(
            key="auto_invert",
            label="Invert",
            enabled=active_settings.auto_invert,
            details="inverted" if was_auto_inverted else "normal",
        )
    )

    # 7. High Contrast
    if active_settings.high_contrast:
        image = stretch_grayscale_contrast(image)
    
    # Final conversion to RGB32 for OCR engine compatibility
    image = image.convertToFormat(QtGui.QImage.Format.Format_RGB32)

    steps.append(
        OcrPreprocessStep(
            key="high_contrast",
            label="High Contrast",
            enabled=active_settings.high_contrast,
            details="otsu-stretch" if active_settings.high_contrast else "off",
        )
    )

    return OcrPreprocessResult(
        image=image,
        settings=active_settings,
        resolved_scale_factor=effective_scale_factor,
        steps=steps,
    )


def preprocess_for_ocr(
    pixmap: QtGui.QPixmap,
    settings: OcrPreprocessSettings | None = None,
) -> QtGui.QImage:
    """
    Backward-compatible helper that returns the processed image only.
    """
    active_settings = settings or default_preprocess_settings()
    return run_preprocess_pipeline(pixmap, settings=active_settings).image
