import json
import urllib.error
import urllib.request

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from hushsnap import cli
from hushsnap.constants import OCR_ENGINE_PPOCR
from hushsnap.ocr import OcrRecognition, OcrService
from hushsnap.ocr.engine import register_engine
from hushsnap.ocr.ocr_server import OcrHttpServer


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])
    return app


def _png_bytes(width=32, height=32) -> bytes:
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor(255, 255, 255))
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def _request(port, token, method, path, data=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
    )
    if token:
        req.add_header("X-HushSnap-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def _start_server(tmp_path, recognize=None):
    register_engine(
        OCR_ENGINE_PPOCR,
        recognize=recognize or (lambda *a, **k: OcrRecognition(text="server works")),
    )
    service = OcrService()
    server = OcrHttpServer(service=service, info_path=tmp_path / "ocr_server.json")
    server.start()
    return server, service


def test_server_roundtrip(tmp_path, qapp):
    server, _service = _start_server(tmp_path)
    try:
        info = json.loads((tmp_path / "ocr_server.json").read_text(encoding="utf-8"))
        port, token = info["port"], info["token"]

        ping = _request(port, token, "GET", "/v1/ping")
        assert ping["ok"] is True
        assert ping["version"] == 1

        resp = _request(port, token, "POST", "/v1/ocr", data=_png_bytes())
        assert resp["ok"] is True
        assert resp["text"] == "server works"
        assert resp["error"] == ""
        assert resp["superseded"] is False
    finally:
        server.shutdown()


def test_server_rejects_bad_token(tmp_path, qapp):
    server, _service = _start_server(tmp_path)
    try:
        info = json.loads((tmp_path / "ocr_server.json").read_text(encoding="utf-8"))
        req = urllib.request.Request(
            f"http://127.0.0.1:{info['port']}/v1/ping", method="GET",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req, timeout=5)
        assert excinfo.value.code == 401
    finally:
        server.shutdown()


def test_server_rejects_garbage_image(tmp_path, qapp):
    server, _service = _start_server(tmp_path)
    try:
        info = json.loads((tmp_path / "ocr_server.json").read_text(encoding="utf-8"))
        resp = _request(info["port"], info["token"], "POST", "/v1/ocr", data=b"not an image")
        assert resp["ok"] is False
        assert resp["error"] == "unreadable_image"
    finally:
        server.shutdown()


def test_server_removes_info_file_on_shutdown(tmp_path, qapp):
    server, _service = _start_server(tmp_path)
    info_file = tmp_path / "ocr_server.json"
    assert info_file.exists()
    server.shutdown()
    assert not info_file.exists()


def test_cli_errors_when_app_not_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_user_data_dir", lambda: tmp_path)
    code = cli.main(["ocr", str(tmp_path / "nope.png")])
    assert code == 1
    captured = capsys.readouterr()
    assert "HushSnap is not running" in captured.err


def test_cli_roundtrip(tmp_path, monkeypatch, capsys, qapp):
    server, _service = _start_server(tmp_path)
    try:
        monkeypatch.setattr(cli, "get_user_data_dir", lambda: tmp_path)
        image_path = tmp_path / "photo.png"
        image_path.write_bytes(_png_bytes())

        code = cli.main(["ocr", str(image_path), "--json"])
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["text"] == "server works"
    finally:
        server.shutdown()


def test_cli_superseded_reports_clear_error(tmp_path, monkeypatch, capsys, qapp):
    """A superseded request surfaces as an explicit error, not a hang.

    Deterministic: a stub service that always reports OCR_SUPERSEDED_ERROR
    exercises the server's 409 mapping and the CLI's error formatting.
    """
    from hushsnap.constants import OCR_SUPERSEDED_ERROR
    from hushsnap.ocr.models import OcrResponse

    class _AlwaysSuperseded:
        def recognize_async(self, request, done_callback, notify_if_dropped=False):
            done_callback(OcrResponse(error=OCR_SUPERSEDED_ERROR))

    server = OcrHttpServer(
        service=_AlwaysSuperseded(),
        info_path=tmp_path / "ocr_server.json",
    )
    server.start()
    try:
        monkeypatch.setattr(cli, "get_user_data_dir", lambda: tmp_path)
        image_path = tmp_path / "photo.png"
        image_path.write_bytes(_png_bytes())

        code = cli.main(["ocr", str(image_path)])
        assert code == 1
        captured = capsys.readouterr()
        assert "superseded" in captured.err
    finally:
        server.shutdown()
