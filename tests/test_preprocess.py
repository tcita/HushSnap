"""
Unit tests for the OCR image preprocessing module.
"""

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap.ocr.preprocess import (
    OcrPreprocessSettings,
    OcrPreprocessStep,
    OcrPreprocessResult,
    prepare_ocr_image,
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


def test_prepare_ocr_image(qapp, sample_pixmap):
    """Test prepare_ocr_image unifies format to ARGB32 and resets DPR."""
    sample_pixmap.setDevicePixelRatio(2.0)
    prepared = prepare_ocr_image(sample_pixmap)
    assert prepared.devicePixelRatio() == 1.0
    assert prepared.format() == QtGui.QImage.Format.Format_ARGB32


def test_run_minimal_pipeline(qapp, sample_pixmap):
    """Test run_minimal_pipeline prepares the source image for OCR."""
    settings = OcrPreprocessSettings()
    result = run_minimal_pipeline(sample_pixmap, settings=settings)

    assert isinstance(result, OcrPreprocessResult)
    assert result.image.format() == QtGui.QImage.Format.Format_ARGB32
    assert result.resolved_scale_factor == 1.0

    step_keys = [step.key for step in result.applied_steps]
    assert "prepare_ocr" in step_keys
    # 32x32 < 960 min side, so safe_pad should fire
    assert "safe_pad" in step_keys
    assert result.image.width() >= 960
    assert result.image.height() >= 960


def test_safe_pad_skips_when_already_large_enough(qapp):
    """Safe pad is skipped when both sides >= 960 px."""
    image = QtGui.QImage(1000, 960, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.black)
    result = run_minimal_pipeline(image)
    step_keys = [step.key for step in result.applied_steps]
    assert "safe_pad" not in step_keys
    assert result.image.width() == 1000
    assert result.image.height() == 960
