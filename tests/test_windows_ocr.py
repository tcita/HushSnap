import json
from types import SimpleNamespace

from hushsnap.system import windows_ocr


def test_run_windows_ocr_json_warns_when_requested_language_is_not_supported(monkeypatch, tmp_path, caplog):
    payload = {
        "Text": "hello",
        "RequestedLanguageTag": "en-US",
        "RequestedLanguageSupported": False,
        "UsedUserProfileFallback": True,
        "EngineLanguageTag": "zh-CN",
        "Lines": [],
    }

    monkeypatch.setattr(
        windows_ocr.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    result = windows_ocr.run_windows_ocr_json(tmp_path / "sample.bmp", "en-US")

    assert result["Text"] == "hello"
    assert "Requested OCR language 'en-US' is not installed or supported by Windows OCR" in caplog.text
    assert "falling back to 'zh-CN'" in caplog.text


def test_run_windows_ocr_json_logs_process_failure(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        windows_ocr.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="boom",
        ),
    )

    result = windows_ocr.run_windows_ocr_json(tmp_path / "sample.bmp", "en-US")

    assert result == {"Error": "boom"}
    assert "Windows OCR failed: boom" in caplog.text


def test_build_windows_ocr_script_checks_simplified_chinese_candidates(tmp_path):
    script = windows_ocr.build_windows_ocr_script(tmp_path / "sample.bmp", "zh-CN")

    assert "$normalizedRequestedTag -in @('zh-cn', 'zh-sg', 'zh-hans', 'zh')" in script
    assert "'zh-CN'" in script
    assert "'zh-SG'" in script
    assert "'zh-Hans'" in script


def test_build_windows_ocr_script_checks_traditional_chinese_candidates(tmp_path):
    script = windows_ocr.build_windows_ocr_script(tmp_path / "sample.bmp", "zh-TW")

    assert "$normalizedRequestedTag -in @('zh-tw', 'zh-hk', 'zh-mo', 'zh-hant')" in script
    assert "'zh-TW'" in script
    assert "'zh-HK'" in script
    assert "'zh-MO'" in script
    assert "'zh-Hant'" in script
