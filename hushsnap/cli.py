"""HushSnap command-line client (OCR).

Talks to the OCR server hosted by the *running* HushSnap app (see
``hushsnap/ocr/ocr_server.py``) over loopback HTTP.  The CLI never loads the
OCR engine itself and never starts HushSnap: if the app is not running it
fails fast and asks the user to launch HushSnap first.

Usage:
    python -m hushsnap.cli ocr <image> [--lang LANG] [--json] [--timeout SEC]

This module deliberately imports only stdlib plus the lightweight
``hushsnap.config`` / ``hushsnap.constants`` modules (no PyQt6 / rapidocr),
so the CLI starts quickly without pulling in the OCR stack.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import get_user_data_dir
from .constants import OCR_SERVER_FILENAME, OCR_SUPERSEDED_ERROR

NOT_RUNNING_HINT = (
    "HushSnap is not running. Start HushSnap first, then retry this command."
)


def _server_info():
    """Read the discovery file; return dict or None when the app isn't serving."""
    info_path = get_user_data_dir() / OCR_SERVER_FILENAME
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    port = data.get("port")
    token = data.get("token", "")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    return {"port": port, "token": token if isinstance(token, str) else ""}


def ocr(image, lang="", json_output=False, timeout_s=60.0):
    """OCR one image file via the running app and print the result.

    Returns a process exit code (0 = success).
    """
    info = _server_info()
    if info is None:
        return _fail(NOT_RUNNING_HINT, json_output)

    try:
        image_bytes = Path(image).read_bytes()
    except OSError as exc:
        return _fail(f"Cannot read image file: {image} ({exc})", json_output)

    url = f"http://127.0.0.1:{info['port']}/v1/ocr"
    if lang:
        url += "?" + urllib.parse.urlencode({"lang": lang})
    request = urllib.request.Request(url, data=image_bytes, method="POST")
    request.add_header("Content-Type", "application/octet-stream")
    if info["token"]:
        request.add_header("X-HushSnap-Token", info["token"])

    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            return _fail(f"OCR server error: HTTP {exc.code}", json_output)
    except Exception as exc:
        return _fail(
            f"Could not reach the HushSnap OCR server ({exc}). {NOT_RUNNING_HINT}",
            json_output,
        )

    if payload.get("superseded") or payload.get("error") == OCR_SUPERSEDED_ERROR:
        return _fail(
            "OCR request was superseded by a newer request (a screenshot/hotkey "
            "OCR or another CLI call started after yours). Retry when the app is idle.",
            json_output,
        )
    if not payload.get("ok"):
        return _fail(payload.get("error") or "OCR failed", json_output)

    if json_output:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(payload.get("text") or "")
    return 0


def _fail(message, json_output):
    if json_output:
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    else:
        print(message, file=sys.stderr)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="hushsnap cli",
        description="Talk to the running HushSnap app's OCR engine over loopback.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ocr_p = sub.add_parser("ocr", help="Recognize text in an image file")
    ocr_p.add_argument("image", help="Path to the image to OCR")
    ocr_p.add_argument("--lang", default="", help="Optional BCP-47 language tag")
    ocr_p.add_argument("--json", action="store_true", help="Emit the full JSON response")
    ocr_p.add_argument("--timeout", type=float, default=60.0, help="Client timeout in seconds")

    args = parser.parse_args(argv)

    if args.command == "ocr":
        return ocr(
            args.image,
            lang=args.lang,
            json_output=args.json,
            timeout_s=args.timeout,
        )
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
