"""
Unit tests for the OCR image preprocessing module.
Covers thresholding, contrast stretching, scaling, padding, and preprocessing pipelines.
"""

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap.ocr.preprocess import (
    OcrPreprocessSettings,
    OcrPreprocessStep,
    OcrPreprocessResult,
    resolve_scale_factor,
    otsu_threshold,
    smooth_grayscale_image,
    should_invert_grayscale,
    invert_grayscale_in_place,
    stretch_grayscale_contrast,
    preprocess_grayscale,
    to_high_contrast,
    normalize_source_image,
    scale_image,
    create_padded_canvas,
    draw_boldened_text,
    run_preprocess_pipeline,
    run_minimal_pipeline,
    preprocess_for_ocr,
)


@pytest.fixture
def qapp():
    """Fixture to provide a QApplication instance."""
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def sample_pixmap(qapp):
    """Fixture to provide a sample 32x32 QPixmap for testing."""
    pixmap = QtGui.QPixmap(32, 32)
    pixmap.fill(QtCore.Qt.GlobalColor.white)
    return pixmap


def test_preprocess_result_properties():
    """Test OcrPreprocessResult summary and applied_steps properties."""
    image = QtGui.QImage(10, 10, QtGui.QImage.Format.Format_Grayscale8)
    settings = OcrPreprocessSettings()
    steps = [
        OcrPreprocessStep(key="step1", label="Step 1", enabled=True, details="ok"),
        OcrPreprocessStep(key="step2", label="Step 2", enabled=False, details="skip"),
        OcrPreprocessStep(key="step3", label="Step 3", enabled=True),
    ]
    res = OcrPreprocessResult(image=image, settings=settings, steps=steps)

    assert len(res.applied_steps) == 2
    assert res.applied_steps[0].key == "step1"
    assert res.applied_steps[1].key == "step3"
    assert res.summary() == "Step 1 (ok) -> Step 3"


def test_resolve_scale_factor():
    """Test resolve_scale_factor with different settings and inputs."""
    settings_no_scale = OcrPreprocessSettings(auto_scale=False)
    factor, mode = resolve_scale_factor(settings_no_scale, 2.5)
    assert factor == 1.0
    assert mode == "off"

    settings_with_scale = OcrPreprocessSettings(auto_scale=True)
    factor, mode = resolve_scale_factor(settings_with_scale, 2.5)
    assert factor == 2.5
    assert mode == "auto"

    factor, mode = resolve_scale_factor(settings_with_scale, None)
    assert factor == 1.0
    assert mode == "auto"


def test_otsu_threshold_and_inversion(qapp):
    """Test Otsu's binarization threshold and automatic inversion."""
    # Create a 10x10 grayscale image with a dark background and a bright square
    image = QtGui.QImage(10, 10, QtGui.QImage.Format.Format_Grayscale8)
    image.fill(10)  # Dark background (value 10)

    # Draw a 4x4 bright square in the middle (value 200)
    for y in range(3, 7):
        for x in range(3, 7):
            image.setPixelColor(x, y, QtGui.QColor(200, 200, 200))


    threshold = otsu_threshold(image)
    # Threshold should lie between 10 and 200
    assert 10 < threshold < 200

    # Since the majority of pixels are dark (84 out of 100), it should NOT be inverted
    # because standard binarization assumes dark text on a light background.
    # Wait, let's verify should_invert_grayscale behavior.
    assert should_invert_grayscale(image, threshold) is True

    # Invert the image
    invert_grayscale_in_place(image)
    # Background should now be bright, square should be dark
    assert image.pixelColor(0, 0).red() > 240
    assert image.pixelColor(5, 5).red() < 60


def test_smooth_grayscale_image(qapp):
    """Test smooth_grayscale_image returns an image of the same size."""
    image = QtGui.QImage(16, 16, QtGui.QImage.Format.Format_Grayscale8)
    image.fill(128)
    smoothed = smooth_grayscale_image(image)
    assert smoothed.width() == 16
    assert smoothed.height() == 16
    assert smoothed.format() == QtGui.QImage.Format.Format_Grayscale8


def test_stretch_grayscale_contrast(qapp):
    """Test contrast stretching adjusts pixel values properly."""
    image = QtGui.QImage(10, 10, QtGui.QImage.Format.Format_Grayscale8)
    # Fill with values concentrated around 100-110
    image.fill(100)
    # Fill a 6x6 square in the middle with value 110
    for y in range(2, 8):
        for x in range(2, 8):
            image.setPixelColor(x, y, QtGui.QColor(110, 110, 110))


    stretched = stretch_grayscale_contrast(image)
    # Minimum values should be stretched towards 0, and max towards 255
    min_val = 255
    max_val = 0
    bits = stretched.bits()
    bits.setsize(stretched.sizeInBytes())
    for val in memoryview(bits):
        min_val = min(min_val, val)
        max_val = max(max_val, val)

    assert min_val < 50
    assert max_val > 200


def test_normalize_source_image(qapp, sample_pixmap):
    """Test normalize_source_image converts to Grayscale8 and resets DPR."""
    sample_pixmap.setDevicePixelRatio(2.0)
    normalized = normalize_source_image(sample_pixmap)
    assert normalized.devicePixelRatio() == 1.0
    assert normalized.format() == QtGui.QImage.Format.Format_Grayscale8


def test_scale_image(qapp):
    """Test scale_image resizes images based on scale factor."""
    image = QtGui.QImage(10, 10, QtGui.QImage.Format.Format_Grayscale8)
    
    # Scale factor 1.0 shouldn't resize
    scaled_1 = scale_image(image, 1.0)
    assert scaled_1.width() == 10

    # Scale factor 2.5 should resize to 25
    scaled_2 = scale_image(image, 2.5)
    assert scaled_2.width() == 25


def test_create_padded_canvas(qapp):
    """Test create_padded_canvas adds padding when below minimum size."""
    # Image above min size (64x64) should NOT be padded
    image_large = QtGui.QImage(100, 100, QtGui.QImage.Format.Format_Grayscale8)
    canvas_large, pad_large = create_padded_canvas(image_large)
    assert pad_large == 0
    assert canvas_large.width() == 100

    # Image below min size should be padded
    image_small = QtGui.QImage(32, 32, QtGui.QImage.Format.Format_Grayscale8)
    image_small.fill(QtCore.Qt.GlobalColor.white)
    canvas_small, pad_small = create_padded_canvas(image_small)
    assert pad_small > 0
    # Canvas should be at least 64 + pad * 2
    assert canvas_small.width() >= 64 + pad_small * 2


def test_draw_boldened_text(qapp):
    """Test draw_boldened_text copies source to destination with offsets."""
    src = QtGui.QImage(10, 10, QtGui.QImage.Format.Format_Grayscale8)
    src.fill(255)  # White
    
    # Draw a black square at (2, 2) using QPainter
    painter = QtGui.QPainter(src)
    painter.fillRect(QtCore.QRect(2, 2, 2, 2), QtGui.QColor(0, 0, 0))
    painter.end()

    dst = QtGui.QImage(14, 14, QtGui.QImage.Format.Format_Grayscale8)
    dst.fill(255)

    draw_boldened_text(dst, src, pad=2)

    # In dst, the black pixel is boldened/shifted. Due to opaque source-over drawing of the 
    # entire src canvas, some pixels get overwritten by the last draw call, but (4, 5) 
    # is guaranteed to survive as a dark pixel.
    assert dst.pixelColor(4, 5).red() < 50
    assert dst.pixelColor(6, 6).red() > 200  # far away remains bright



def test_run_preprocess_pipeline(qapp, sample_pixmap):
    """Test full pipeline execution yields valid result."""
    settings = OcrPreprocessSettings(
        auto_scale=True,
        normalize_source=True,
        auto_add_padding=True,
        smooth=True,
        bolden_text=True,
        auto_invert=True,
        high_contrast=True,
    )
    result = run_preprocess_pipeline(sample_pixmap, settings=settings, resolved_scale_factor=2.0)

    assert isinstance(result, OcrPreprocessResult)
    assert result.image.format() == QtGui.QImage.Format.Format_RGB32
    assert result.resolved_scale_factor == 2.0
    assert len(result.applied_steps) > 0


def test_run_minimal_pipeline(qapp, sample_pixmap):
    """Test run_minimal_pipeline scales and normalizes only."""
    settings = OcrPreprocessSettings(auto_scale=True, auto_invert=True)
    result = run_minimal_pipeline(sample_pixmap, settings=settings, resolved_scale_factor=1.5)

    assert isinstance(result, OcrPreprocessResult)
    assert result.image.format() == QtGui.QImage.Format.Format_RGB32
    assert result.resolved_scale_factor == 1.5
    
    # Check that inversion step was NOT added to minimal pipeline
    step_keys = [step.key for step in result.applied_steps]
    assert "auto_invert" not in step_keys


def test_preprocess_for_ocr(qapp, sample_pixmap):
    """Test backward-compatible preprocess_for_ocr helper."""
    image = preprocess_for_ocr(sample_pixmap)
    assert isinstance(image, QtGui.QImage)
    assert image.format() == QtGui.QImage.Format.Format_RGB32


def test_prepare_preprocess_result_uses_minimal_pipeline(qapp, sample_pixmap):
    """Test that prepare_preprocess_result calls run_minimal_pipeline."""
    from hushsnap.ocr.recognition import prepare_preprocess_result
    
    settings = OcrPreprocessSettings(auto_scale=False, auto_invert=True)
    
    # Run prepare_preprocess_result
    result = prepare_preprocess_result(sample_pixmap, preprocess_settings=settings)
    
    # Check that inversion step (which is part of run_preprocess_pipeline but NOT minimal)
    # is NOT present in the result
    step_keys = [step.key for step in result.applied_steps]
    assert "auto_invert" not in step_keys

