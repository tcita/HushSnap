import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap import ocr
from hushsnap.ocr.preprocess import OcrPreprocessStep
from hushsnap.ocr import recognition as recognition_module
from tools.ocr_evaluator import (
    format_pipeline_summary,
    format_pipeline_steps,
    load_eval_preprocess_settings,
)


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


def test_run_preprocess_pipeline_uses_auto_scale_factor(sample_pixmap):
    result = ocr.run_preprocess_pipeline(
        sample_pixmap,
        settings=ocr.OcrPreprocessSettings(auto_scale=True),
        resolved_scale_factor=2.0,
    )

    assert result.resolved_scale_factor == 2.0
    assert any(step.key == "scale" and step.enabled for step in result.steps)
    assert result.image.width() > sample_pixmap.width()
    assert result.image.height() > sample_pixmap.height()


def test_run_preprocess_pipeline_can_disable_optional_steps(sample_pixmap):
    result = ocr.run_preprocess_pipeline(
        sample_pixmap,
        settings=ocr.OcrPreprocessSettings(
            auto_scale=False,
            auto_add_padding=False,
            bolden_text=False,
            auto_invert=False,
            high_contrast=False,
        ),
    )

    steps_by_key = {step.key: step for step in result.steps}

    assert steps_by_key["padding"].enabled is False
    assert steps_by_key["bolden"].enabled is False
    assert steps_by_key["scale"].details == "1.00x"
    assert steps_by_key["high_contrast"].enabled is False
    assert result.image.width() == sample_pixmap.width()
    assert result.image.height() == sample_pixmap.height()


def test_run_preprocess_pipeline_keeps_auto_invert_without_high_contrast(qapp):
    pixmap = QtGui.QPixmap(10, 10)
    pixmap.fill(QtGui.QColor(0, 0, 0))

    result = ocr.run_preprocess_pipeline(
        pixmap,
        settings=ocr.OcrPreprocessSettings(
            auto_add_padding=False,
            bolden_text=False,
            auto_invert=True,
            high_contrast=False,
        ),
    )

    steps_by_key = {step.key: step for step in result.steps}

    assert steps_by_key["auto_invert"].enabled is True
    assert steps_by_key["high_contrast"].enabled is False
    assert result.image.pixelColor(0, 0).value() > 200


def test_ocr_eval_profile_uses_preprocess_settings():
    settings = load_eval_preprocess_settings()
    assert isinstance(settings, ocr.OcrPreprocessSettings)
    assert settings.auto_scale is True
    assert settings.smooth is False


def test_ocr_eval_profile_defaults_missing_keys(tmp_path):
    config_path = tmp_path / "ocr_eval_config.toml"
    config_path.write_text("[preprocess]\nnormalize_source = false\n", encoding="utf-8")

    settings = load_eval_preprocess_settings(config_path)

    assert settings.normalize_source is False
    assert settings.auto_scale is True
    assert settings.auto_add_padding is True
    assert settings.smooth is True


def test_ocr_eval_profile_rejects_unknown_keys(tmp_path):
    config_path = tmp_path / "ocr_eval_config.toml"
    config_path.write_text("[preprocess]\nnormalize_source = true\nunexpected = true\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown \\[preprocess\\] keys"):
        load_eval_preprocess_settings(config_path)


def test_prepare_preprocess_result_uses_auto_scale(monkeypatch, sample_pixmap):
    monkeypatch.setattr(recognition_module, "estimate_auto_scale_factor", lambda *args, **kwargs: 2.5)

    result = ocr.prepare_preprocess_result(
        sample_pixmap,
        language_tag="en-US",
        preprocess_settings=ocr.OcrPreprocessSettings(auto_scale=True),
    )

    steps_by_key = {step.key: step for step in result.steps}
    assert result.resolved_scale_factor == 2.5
    assert steps_by_key["scale"].details == "2.50x"
    assert result.image.width() > sample_pixmap.width()


def test_run_preprocess_pipeline_auto_padding_only_applies_to_small_images(qapp):
    small_pixmap = QtGui.QPixmap(20, 10)
    small_pixmap.fill(QtCore.Qt.GlobalColor.white)

    small_result = ocr.run_preprocess_pipeline(
        small_pixmap,
        settings=ocr.OcrPreprocessSettings(auto_scale=False, auto_add_padding=True, bolden_text=False),
    )
    large_pixmap = QtGui.QPixmap(80, 80)
    large_pixmap.fill(QtCore.Qt.GlobalColor.white)
    large_result = ocr.run_preprocess_pipeline(
        large_pixmap,
        settings=ocr.OcrPreprocessSettings(auto_scale=False, auto_add_padding=True, bolden_text=False),
    )

    small_steps = {step.key: step for step in small_result.steps}
    large_steps = {step.key: step for step in large_result.steps}
    assert small_result.image.width() > small_pixmap.width()
    assert small_steps["padding"].details == "+8px"
    assert large_result.image.width() == large_pixmap.width()
    assert large_steps["padding"].details == "none"


def test_run_preprocess_pipeline_can_disable_smooth(sample_pixmap):
    result = ocr.run_preprocess_pipeline(
        sample_pixmap,
        settings=ocr.OcrPreprocessSettings(
            auto_scale=False,
            auto_add_padding=False,
            smooth=False,
            auto_invert=False,
            high_contrast=False,
            bolden_text=False,
        ),
    )

    steps_by_key = {step.key: step for step in result.steps}
    assert steps_by_key["smooth"].enabled is False
    assert steps_by_key["smooth"].details == "off"


def test_ocr_evaluator_pipeline_summary_deduplicates_grayscale_wording():
    steps = [
        OcrPreprocessStep("normalize_source", "Grayscale", True, "8-bit"),
        OcrPreprocessStep("scale", "Scale", True, "1.54x"),
        OcrPreprocessStep("padding", "Padding", True, "none"),
        OcrPreprocessStep("smooth", "Grayscale + Smooth", True, "bilinear-resampling"),
    ]

    summary = format_pipeline_summary(steps)

    assert summary == "Normalize & Grayscale -> Auto Scale (auto->1.54x) -> Auto Add Padding (no-op) -> Smooth"


def test_ocr_evaluator_pipeline_step_chips_hide_redundant_grayscale_detail():
    steps = [
        OcrPreprocessStep("normalize_source", "Grayscale", True, "8-bit"),
        OcrPreprocessStep("smooth", "Grayscale + Smooth", True, "bilinear-resampling"),
    ]

    chips_html = format_pipeline_steps(steps)

    assert "Normalize &amp; Grayscale" in chips_html
    assert "Grayscale + Smooth" not in chips_html
    assert ">Smooth<" in chips_html
