"""Loopback HTTP service exposing the app's OCR engine to local clients.

HushSnap runs a tiny HTTP server bound to 127.0.0.1 so external tools — a
command-line client, an AI agent — can reuse the already-tuned, already-warm
OCR engine living inside the tray process, without installing the heavy
Python stack (PyQt6, rapidocr, onnxruntime) themselves.

Design contract
───────────────
- STRICT SERIALIZATION: every request funnels through the single OcrService
  worker slot.  There is deliberately NO second worker and no per-client
  queue, so a rapidocr call can never run concurrently with another one.
- LATEST-WINS PRESERVED: IPC requests share the app's single-slot "newest
  request wins" semantics (see OcrService.recognize_async).  When an IPC
  request is superseded — by a UI hotkey OCR, auto-OCR, or another CLI call —
  it receives an explicit ``OCR_SUPERSEDED_ERROR`` response (HTTP 409) instead
  of hanging or being silently dropped.
- NO AUTO-LAUNCH: the server exists only while the app is running.  The CLI
  never starts HushSnap and never loads a second engine; if the app is not
  running the CLI fails fast with a clear message.

Discovery: the app writes a JSON file (``OCR_SERVER_FILENAME``) into the user
data dir carrying the bound port and a per-launch auth token.  Clients read
that file to find and authenticate against the server; it is removed on clean
app exit.
"""

import json
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PyQt6 import QtCore, QtGui

from ..constants import OCR_SUPERSEDED_ERROR
from .models import OcrRequest

logger = logging.getLogger(__name__)

# Version of the server.json discovery protocol. Bump on breaking changes.
OCR_SERVER_VERSION = 1
# Upper bound on the size of an uploaded image (defence in depth; desktop
# screenshots are typically < 10 MB).
MAX_IMAGE_BYTES = 32 * 1024 * 1024
# How long an OCR request may take before the client is told it timed out.
# A single inference is usually well under 2 s; this is a safety net for cold
# engine loads and pathological images, not a latency target.
REQUEST_TIMEOUT_S = 30.0


class _ThreadingHTTPServer(ThreadingHTTPServer):
    # Never let a lingering request thread block process exit: an in-flight
    # request during app teardown must not delay the shutdown handshake.
    daemon_threads = True


class _OcrHandler(BaseHTTPRequestHandler):
    """Minimal JSON endpoint set for the OCR service."""

    protocol_version = "HTTP/1.0"
    server_version = "HushSnapOCR"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Silence the default per-request stderr line; route to our logger.
        logger.debug("[ocr-server] " + fmt, *args)

    # ── helpers ──────────────────────────────────────────────────────────

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass  # client went away mid-write

    def _authorized(self) -> bool:
        expected = getattr(self.server, "token", "")
        if not expected:
            return True
        return secrets.compare_digest(
            self.headers.get("X-HushSnap-Token", ""), expected,
        )

    # ── endpoints ────────────────────────────────────────────────────────

    def do_GET(self):
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        if self.path.split("?", 1)[0].rstrip("/") == "/v1/ping":
            self._send_json(200, {
                "ok": True,
                "version": OCR_SERVER_VERSION,
                "pid": getattr(self.server, "pid", None),
            })
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        if self.path.split("?", 1)[0].rstrip("/") != "/v1/ocr":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return

        length = self.headers.get("Content-Length")
        if length is None:
            self._send_json(400, {"ok": False, "error": "missing_content_length"})
            return
        try:
            length = int(length)
        except ValueError:
            self._send_json(400, {"ok": False, "error": "bad_content_length"})
            return
        if length <= 0:
            self._send_json(400, {"ok": False, "error": "empty_body"})
            return
        if length > MAX_IMAGE_BYTES:
            self._send_json(413, {"ok": False, "error": "image_too_large"})
            return
        try:
            data = self.rfile.read(length)
        except OSError:
            self._send_json(400, {"ok": False, "error": "read_failed"})
            return

        query = parse_qs(urlparse(self.path).query)
        lang = (query.get("lang") or [""])[0]

        image = QtGui.QImage.fromData(QtCore.QByteArray(data))
        if image.isNull():
            self._send_json(400, {"ok": False, "error": "unreadable_image"})
            return

        holder = {}
        done = threading.Event()

        def _on_response(response):
            holder["response"] = response
            done.set()

        # Funnel through the SAME single worker slot as every GUI request.
        # notify_if_dropped=True turns the single-slot overwrite into a
        # guaranteed single callback, so a superseded client never hangs.
        self.server.service.recognize_async(
            OcrRequest(pixmap=image, language_tag=lang),
            _on_response,
            notify_if_dropped=True,
        )

        if not done.wait(timeout=REQUEST_TIMEOUT_S):
            self._send_json(504, {"ok": False, "error": "ocr_timeout"})
            return

        response = holder["response"]
        if response.error == OCR_SUPERSEDED_ERROR:
            self._send_json(409, {
                "ok": False,
                "superseded": True,
                "error": OCR_SUPERSEDED_ERROR,
                "text": "",
            })
            return

        lines = [
            _line_to_dict(line)
            for line in (response.recognition.lines if response.recognition else [])
        ]
        status = 200 if not response.error else 500
        self._send_json(status, {
            "ok": not response.error,
            "text": response.text or "",
            "error": response.error or "",
            "superseded": False,
            "engine": response.recognition.engine_type if response.recognition else "",
            "lines": lines,
        })


def _line_to_dict(line) -> dict:
    box = line.bounding_box
    return {
        "text": line.text,
        "x": box.x,
        "y": box.y,
        "width": box.width,
        "height": box.height,
        "indent_level": line.indent_level,
        "is_blank": line.is_blank,
    }


class OcrHttpServer:
    """Loopback HTTP server fronting the app's OcrService.

    Started by the app on launch; the server exists only while the app is
    running.  Discovery: writes a JSON file (``OCR_SERVER_FILENAME``) into the
    user data dir carrying the bound port and an auth token.  Clients (the
    CLI, agents) read that file to find and authenticate against the server.
    """

    def __init__(self, service, info_path, token=True):
        self._service = service
        self._info_path = Path(info_path)
        self._token = secrets.token_hex(16) if token else ""
        self._httpd = None
        self._thread = None

    def start(self):
        """Bind the loopback port, serve in a daemon thread, publish the info file."""
        if self._httpd is not None:
            return
        httpd = _ThreadingHTTPServer(("127.0.0.1", 0), _OcrHandler)
        httpd.service = self._service
        httpd.token = self._token
        httpd.pid = os.getpid()
        self._httpd = httpd
        self._write_info_file()
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            name="hushsnap-ocr-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "OCR server listening on 127.0.0.1:%d (token=%s)",
            httpd.server_address[1], "on" if self._token else "off",
        )

    def _write_info_file(self):
        info = {
            "version": OCR_SERVER_VERSION,
            "pid": os.getpid(),
            "port": self._httpd.server_address[1],
            "token": self._token,
        }
        try:
            self._info_path.parent.mkdir(parents=True, exist_ok=True)
            self._info_path.write_text(json.dumps(info), encoding="utf-8")
        except Exception:
            logger.warning("Failed to write OCR server info file", exc_info=True)

    def shutdown(self):
        """Stop serving, close the socket, and remove the discovery file."""
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                logger.debug("[ocr-server] httpd.shutdown()", exc_info=True)
            httpd.server_close()
        try:
            self._info_path.unlink(missing_ok=True)
        except Exception:
            logger.debug("[ocr-server] failed to remove info file", exc_info=True)
