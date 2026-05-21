"""
Unit tests for the OCR image preprocessing module.
"""

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap.ocr.preprocess import (
    OcrPreprocessSettings,
    OcrPreprocessStep,
    OcrPreprocessResult,
    resolve_scale_factor,
    normalize_source_image,
    scale_image,
    run_minimal_pipeline,
)


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def sample_pixmap(qapp):
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


def test_normalize_source_image(qapp, sample_pixmap):
    """Test normalize_source_image converts to Grayscale8 and resets DPR."""
    sample_pixmap.setDevicePixelRatio(2.0)
    normalized = normalize_source_image(sample_pixmap)
    assert normalized.devicePixelRatio() == 1.0
    assert normalized.format() == QtGui.QImage.Format.Format_Grayscale8


def test_scale_image(qapp):
    """Test scale_image resizes images based on scale factor."""
    image = QtGui.QImage(10, 10, QtGui.QImage.Format.Format_Grayscale8)

    scaled_1 = scale_image(image, 1.0)
    assert scaled_1.width() == 10

    scaled_2 = scale_image(image, 2.5)
    assert scaled_2.width() == 25


def test_run_minimal_pipeline(qapp, sample_pixmap):
    """Test run_minimal_pipeline scales and normalizes only."""
    settings = OcrPreprocessSettings(auto_scale=True)
    result = run_minimal_pipeline(sample_pixmap, settings=settings, resolved_scale_factor=1.5)

    assert isinstance(result, OcrPreprocessResult)
    assert result.image.format() == QtGui.QImage.Format.Format_RGB32
    assert result.resolved_scale_factor == 1.5

    step_keys = [step.key for step in result.applied_steps]
    assert "scale" in step_keys
    assert "normalize_source" in step_keys


def test_prepare_preprocess_result_uses_minimal_pipeline(qapp, sample_pixmap):
    """Test that prepare_preprocess_result calls run_minimal_pipeline."""
    from hushsnap.ocr.recognition import prepare_preprocess_result

    settings = OcrPreprocessSettings(auto_scale=False)
    result = prepare_preprocess_result(sample_pixmap, preprocess_settings=settings)

    step_keys = [step.key for step in result.applied_steps]
    assert "scale" in step_keys or "normalize_source" in step_keys
