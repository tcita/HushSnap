from PyQt6 import QtCore, QtGui


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


def to_high_contrast(image: QtGui.QImage) -> QtGui.QImage:
    """Convert image to OCR-friendly high-contrast RGB output."""
    grayscale = image.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
    width = max(1, grayscale.width())
    height = max(1, grayscale.height())

    smoothed = grayscale.scaled(
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

    data = image_bytes(smoothed)
    threshold = otsu_threshold(smoothed)

    dark_pixel_count = 0
    for pixel in data:
        if pixel <= threshold:
            dark_pixel_count += 1
    if dark_pixel_count > (len(data) // 2):
        for index in range(len(data)):
            data[index] = 255 - data[index]

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

    return smoothed.convertToFormat(QtGui.QImage.Format.Format_RGB32)


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


def create_padded_canvas(image: QtGui.QImage, pad: int = 32) -> tuple[QtGui.QImage, int]:
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


def preprocess_for_ocr(pixmap: QtGui.QPixmap, scale_factor: float) -> QtGui.QImage:
    """
    Prepare an OCR-oriented image:
    1) normalize source pixels, 2) scale, 3) add quiet-zone padding,
    4) bolden strokes, 5) convert to normalized high-contrast output.
    """
    source_image = normalize_source_image(pixmap)
    scaled_image = scale_image(source_image, scale_factor)
    padded_canvas, pad = create_padded_canvas(scaled_image)
    draw_boldened_text(padded_canvas, scaled_image, pad)
    return to_high_contrast(padded_canvas)
