import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap import ocr
from tools.ocr_evaluator import load_eval_preprocess_settings


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def sample_pixmap(qapp):
    pixmap = QtGui.QPixmap(20, 10)
    pixmap.fill(QtCore.Qt.GlobalColor.white)
    return pixmap


def test_run_preprocess_pipeline_uses_single_scale_setting(sample_pixmap):
    result = ocr.run_preprocess_pipeline(
        sample_pixmap,
        settings=ocr.OcrPreprocessSettings(scale_factor=2.0),
    )

    assert result.settings.scale_factor == 2.0
    assert any(step.key == "scale" and step.enabled for step in result.steps)
    assert result.image.width() > sample_pixmap.width()
    assert result.image.height() > sample_pixmap.height()


def test_run_preprocess_pipeline_can_disable_optional_steps(sample_pixmap):
    result = ocr.run_preprocess_pipeline(
        sample_pixmap,
        settings=ocr.OcrPreprocessSettings(
            scale_factor=1.0,
            add_padding=False,
            bolden_text=False,
            auto_invert=False,
            high_contrast=False,
        ),
    )

    steps_by_key = {step.key: step for step in result.steps}

    assert steps_by_key["padding"].enabled is False
    assert steps_by_key["bolden"].enabled is False
    assert steps_by_key["high_contrast"].enabled is False
    assert result.image.width() == sample_pixmap.width()
    assert result.image.height() == sample_pixmap.height()


def test_ocr_eval_profile_uses_preprocess_settings():
    assert isinstance(load_eval_preprocess_settings(), ocr.OcrPreprocessSettings)
