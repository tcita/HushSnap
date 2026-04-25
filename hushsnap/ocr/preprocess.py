from dataclasses import dataclass, field, replace

from PyQt6 import QtCore, QtGui

DEFAULT_PADDING_PX = 32
DEFAULT_OCR_SCALE_FACTOR = 1.0


@dataclass(frozen=True)
class OcrPreprocessSettings:
    scale_factor: float = DEFAULT_OCR_SCALE_FACTOR
    normalize_source: bool = True
    add_padding: bool = True
    padding_px: int = DEFAULT_PADDING_PX
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


def to_high_contrast(
    image: QtGui.QImage,
    auto_invert: bool = True,
) -> tuple[QtGui.QImage, bool]:
    """Convert image to OCR-friendly high-contrast RGB output."""
    smoothed = smooth_grayscale_image(image)
    threshold = otsu_threshold(smoothed)
    was_inverted = False

    if auto_invert and should_invert_grayscale(smoothed, threshold):
        invert_grayscale_in_place(smoothed)
        was_inverted = True

    contrasted = stretch_grayscale_contrast(smoothed)
    return contrasted.convertToFormat(QtGui.QImage.Format.Format_RGB32), was_inverted


def normalize_source_image(pixmap: QtGui.QPixmap) -> QtGui.QImage:
    """Normalize DPR and pixel format to avoid HiDPI offset artifacts."""
    image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    if image.devicePixelRatio() != 1.0:
        image.setDevicePixelRatio(1.0)
    return image


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


def create_padded_canvas(image: QtGui.QImage, pad: int = DEFAULT_PADDING_PX) -> tuple[QtGui.QImage, int]:
    """
    Create centered quiet-zone padding using sampled background color.
    The returned image is opaque to avoid alpha-blended gray text.
    """
    background_color = image.pixelColor(0, 0)
    if background_color.alpha() < 255:
        background_color.setAlpha(255)

    canvas = QtGui.QImage(
        image.width() + (pad * 2),
        image.height() + (pad * 2),
        QtGui.QImage.Format.Format_RGB32,
    )
    canvas.fill(background_color)
    return canvas, pad


def draw_boldened_text(dst: QtGui.QImage, src: QtGui.QImage, pad: int) -> None:
    """
    Draw source image with small offsets to thicken OCR strokes.
    Triple drawing is a pragmatic heuristic that improves OCR confidence.
    """
    painter = QtGui.QPainter(dst)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(1.0)
        painter.drawImage(pad, pad, src)
        painter.drawImage(pad + 1, pad, src)
        painter.drawImage(pad, pad + 1, src)
    finally:
        painter.end()


def default_preprocess_settings(scale_factor: float | None = None) -> OcrPreprocessSettings:
    if scale_factor is None:
        return DEFAULT_OCR_PREPROCESS_SETTINGS
    return replace(DEFAULT_OCR_PREPROCESS_SETTINGS, scale_factor=scale_factor)


def run_preprocess_pipeline(
    pixmap: QtGui.QPixmap,
    settings: OcrPreprocessSettings | None = None,
) -> OcrPreprocessResult:
    """
    Prepare an OCR-oriented image through a configurable single-path pipeline.
    """
    active_settings = settings or DEFAULT_OCR_PREPROCESS_SETTINGS
    steps: list[OcrPreprocessStep] = []

    if active_settings.normalize_source:
        image = normalize_source_image(pixmap)
    else:
        image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    steps.append(
        OcrPreprocessStep(
            key="normalize_source",
            label="Normalize Source",
            enabled=active_settings.normalize_source,
        )
    )

    scale_applied = abs(active_settings.scale_factor - 1.0) > 0.01
    image = scale_image(image, active_settings.scale_factor)
    steps.append(
        OcrPreprocessStep(
            key="scale",
            label="Scale",
            enabled=scale_applied,
            details=f"{active_settings.scale_factor:.2f}x" if scale_applied else "1.00x",
        )
    )

    pad = 0
    if active_settings.add_padding:
        padded_canvas, pad = create_padded_canvas(image, active_settings.padding_px)
        if active_settings.bolden_text:
            draw_boldened_text(padded_canvas, image, pad)
        else:
            painter = QtGui.QPainter(padded_canvas)
            try:
                painter.drawImage(pad, pad, image)
            finally:
                painter.end()
        image = padded_canvas
    elif active_settings.bolden_text:
        boldened_image = QtGui.QImage(image.size(), QtGui.QImage.Format.Format_RGB32)
        fill_color = image.pixelColor(0, 0)
        if fill_color.alpha() < 255:
            fill_color.setAlpha(255)
        boldened_image.fill(fill_color)
        draw_boldened_text(boldened_image, image, 0)
        image = boldened_image
    steps.append(
        OcrPreprocessStep(
            key="padding",
            label="Add Padding",
            enabled=active_settings.add_padding,
            details=f"{active_settings.padding_px}px" if active_settings.add_padding else "",
        )
    )
    steps.append(
        OcrPreprocessStep(
            key="bolden",
            label="Bolden Text",
            enabled=active_settings.bolden_text,
            details="triple-draw",
        )
    )

    was_auto_inverted = False
    if active_settings.high_contrast:
        image, was_auto_inverted = to_high_contrast(image, auto_invert=active_settings.auto_invert)
    elif image.format() != QtGui.QImage.Format.Format_RGB32:
        image = image.convertToFormat(QtGui.QImage.Format.Format_RGB32)
    steps.append(
        OcrPreprocessStep(
            key="auto_invert",
            label="Auto Invert",
            enabled=active_settings.high_contrast and active_settings.auto_invert and was_auto_inverted,
            details="dark background detected" if was_auto_inverted else "",
        )
    )
    steps.append(
        OcrPreprocessStep(
            key="high_contrast",
            label="High Contrast",
            enabled=active_settings.high_contrast,
        )
    )

    return OcrPreprocessResult(
        image=image,
        settings=active_settings,
        steps=steps,
    )


def preprocess_for_ocr(
    pixmap: QtGui.QPixmap,
    scale_factor: float | None = None,
    settings: OcrPreprocessSettings | None = None,
) -> QtGui.QImage:
    """
    Backward-compatible helper that returns the processed image only.
    """
    active_settings = settings or default_preprocess_settings(scale_factor=scale_factor)
    return run_preprocess_pipeline(pixmap, settings=active_settings).image
