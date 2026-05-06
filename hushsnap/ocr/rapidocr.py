import atexit
import json
import logging
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from PyQt6 import QtGui

from .models import OcrRecognition
from ..config import get_resource_dir

logger = logging.getLogger(__name__)

RAPIDOCR_EXE_NAME = "RapidOCR-json.exe"
RAPIDOCR_RUNTIME_DIR_NAME = "rapidocr"
RAPIDOCR_DEFAULT_MAX_SIDE_LEN = 2048


@dataclass(frozen=True)
class RapidOcrModelConfig:
    language_name: str
    det: str
    cls: str
    rec: str
    keys: str


RAPIDOCR_UNIVERSAL_CONFIG = RapidOcrModelConfig(
    language_name="Universal (v4)",
    det="ch_PP-OCRv3_det_infer.onnx",
    cls="ch_ppocr_mobile_v2.0_cls_infer.onnx",
    rec="rec_ch_PP-OCRv4_infer.onnx",
    keys="dict_chinese.txt",
)


def find_rapidocr_runtime_dir() -> Path:
    runtime_dir = get_resource_dir() / RAPIDOCR_RUNTIME_DIR_NAME
    if runtime_dir.exists():
        return runtime_dir
    return Path(__file__).resolve().parents[2] / RAPIDOCR_RUNTIME_DIR_NAME


def rapidocr_box_to_bbox(box) -> tuple[float, float, float, float]:
    if not isinstance(box, list) or not box:
        return 0.0, 0.0, 0.0, 0.0

    points = []
    for point in box:
        if isinstance(point, list | tuple) and len(point) >= 2:
            points.append((float(point[0]), float(point[1])))
    if not points:
        return 0.0, 0.0, 0.0, 0.0

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def is_cjk_or_fullwidth(character: str) -> bool:
    if not character:
        return False
    codepoint = ord(character)
    return (
        0x3000 <= codepoint <= 0x303F
        or 0x3040 <= codepoint <= 0x30FF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xFF00 <= codepoint <= 0xFFEF
    )


def word_separator(left: str, right: str) -> str:
    if not left or not right:
        return ""
    if is_cjk_or_fullwidth(left[-1]) and is_cjk_or_fullwidth(right[0]):
        return ""
    if left[-1] == "-":
        return ""
    if right[0] in ",.;:!?)]}，。；：！？）】》":
        return ""
    return " "


def compose_rapidocr_text(blocks: list[dict]) -> str:
    normalized_blocks = []
    for block in blocks or []:
        text = str(block.get("text", "") or "").strip()
        if not text:
            continue
        left, top, right, bottom = rapidocr_box_to_bbox(block.get("box"))
        height = max(1.0, bottom - top)
        normalized_blocks.append(
            {
                "text": text,
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "center_y": (top + bottom) / 2,
                "height": height,
            }
        )

    if not normalized_blocks:
        return ""

    normalized_blocks.sort(key=lambda item: (item["center_y"], item["left"]))
    lines: list[list[dict]] = []
    for block in normalized_blocks:
        if not lines:
            lines.append([block])
            continue

        current_line = lines[-1]
        avg_height = sum(item["height"] for item in current_line) / len(current_line)
        avg_center_y = sum(item["center_y"] for item in current_line) / len(current_line)
        if abs(block["center_y"] - avg_center_y) <= max(avg_height, block["height"]) * 0.55:
            current_line.append(block)
        else:
            lines.append([block])

    rendered_lines: list[str] = []
    for line in lines:
        line.sort(key=lambda item: item["left"])
        pieces: list[str] = []
        previous = None
        for block in line:
            text = block["text"]
            if previous is None:
                pieces.append(text)
            else:
                gap = block["left"] - previous["right"]
                avg_height = (block["height"] + previous["height"]) / 2
                if gap > avg_height * 1.2:
                    pieces.append(" " + text)
                else:
                    pieces.append(word_separator(previous["text"], text) + text)
            previous = block

        rendered = "".join(pieces).rstrip()
        if rendered:
            rendered_lines.append(rendered)

    return "\n".join(rendered_lines).strip()


class RapidOcrProcess:
    def __init__(self):
        self._process = None
        self._config: RapidOcrModelConfig | None = None
        self._lock = threading.Lock()
        atexit.register(self.stop)

    def recognize(self, image_path: Path, language_tag: str = "") -> dict:
        # Use the universal v4 model for all requests
        with self._lock:
            self._ensure_started(RAPIDOCR_UNIVERSAL_CONFIG)
            return self._run_path(image_path)

    def _ensure_started(self, config: RapidOcrModelConfig) -> None:
        if self._process is not None and self._process.poll() is None and self._config == config:
            return
        logger.info(f"Starting RapidOCR process with config: {config.language_name}")
        self.stop()

        runtime_dir = find_rapidocr_runtime_dir()
        exe_path = runtime_dir / RAPIDOCR_EXE_NAME
        logger.debug(f"RapidOCR exe path: {exe_path}")
        if not exe_path.exists():
            raise RuntimeError(f"RapidOCR engine unavailable. Expected {exe_path}")

        args = [
            str(exe_path),
            "--models=models",
            "--ensureAscii=1",
            f"--det={config.det}",
            f"--cls={config.cls}",
            f"--rec={config.rec}",
            f"--keys={config.keys}",
            "--doAngle=0",
            "--mostAngle=0",
            f"--maxSideLen={RAPIDOCR_DEFAULT_MAX_SIDE_LEN}",
            "--numThread=4",
        ]
        logger.debug(f"RapidOCR args: {args}")

        startupinfo = None
        creationflags = 0
        if hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            startupinfo.wShowWindow = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW

        try:
            self._process = subprocess.Popen(
                args,
                cwd=str(runtime_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            self._config = config
            self._wait_until_ready()
        except Exception as e:
            logger.error(f"Failed to launch RapidOCR process: {e}")
            raise

    def _wait_until_ready(self) -> None:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("RapidOCR process failed to start.")

        logger.info("Waiting for RapidOCR init...")
        for _ in range(100):
            if self._process.poll() is not None:
                stderr = ""
                if self._process.stderr is not None:
                    stderr = self._process.stderr.read().strip()
                raise RuntimeError(f"RapidOCR init failed. {stderr}".strip())

            line = self._process.stdout.readline()
            if "OCR init completed." in line:
                logger.info("RapidOCR init completed successfully.")
                return

        raise RuntimeError("RapidOCR init timed out.")

    def _run_path(self, image_path: Path) -> dict:
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("RapidOCR process is not running.")
        if self._process.poll() is not None:
            raise RuntimeError("RapidOCR process exited unexpectedly.")

        logger.debug(f"Sending image to RapidOCR: {image_path}")
        payload = json.dumps({"image_path": str(image_path)}, ensure_ascii=True) + "\n"
        self._process.stdin.write(payload)
        self._process.stdin.flush()
        output = self._process.stdout.readline()
        if not output:
            raise RuntimeError("RapidOCR returned no output.")
        logger.debug(f"RapidOCR output received: {output[:200]}...")
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"RapidOCR returned invalid JSON: {output[:200]}") from exc

    def stop(self):
        process = self._process
        self._process = None
        self._config = None
        if process is None:
            return
        try:
            process.kill()
        except Exception:
            logger.debug("Failed to stop RapidOCR process.", exc_info=True)


_rapidocr_process = RapidOcrProcess()


def recognize_rapidocr_qimage(image: QtGui.QImage, language_tag: str = "") -> OcrRecognition:
    from ..constants import OCR_ENGINE_RAPID
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        if not image.save(str(temp_path), "PNG"):
            return OcrRecognition(engine_type=OCR_ENGINE_RAPID)

        payload = _rapidocr_process.recognize(temp_path, language_tag=language_tag)
        code = payload.get("code")
        if code == 101:
            return OcrRecognition(
                requested_language_supported=True,
                engine_language_tag="zh-CN",
                engine_type=OCR_ENGINE_RAPID,
            )
        if code != 100:
            raise RuntimeError(str(payload.get("data") or f"RapidOCR failed with code {code}"))

        blocks = payload.get("data")
        if not isinstance(blocks, list):
            return OcrRecognition(engine_type=OCR_ENGINE_RAPID)
        text = compose_rapidocr_text(blocks)
        return OcrRecognition(
            text=text,
            requested_language_supported=True,
            engine_language_tag="zh-CN",
            engine_type=OCR_ENGINE_RAPID,
        )
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def recognize_rapidocr_result_from_pixmap(
    pixmap: QtGui.QPixmap,
    language_tag: str = "",
) -> OcrRecognition:
    if pixmap.isNull():
        return OcrRecognition()
    image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    return recognize_rapidocr_qimage(image, language_tag=language_tag)
