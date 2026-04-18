import logging
import re
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PyQt6 import QtCore, QtGui

from .system.windows_ocr import run_windows_ocr_json

logger = logging.getLogger(__name__)

INITIAL_SCALE_FACTOR = 1.0
IDEAL_LINE_HEIGHT_PX = 40.0
MAX_OCR_IMAGE_DIMENSION = 2600
MIN_RESCALE_DELTA = 0.15
NO_SPACE_SCRIPT_CHAR_CLASS = r"\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"

NUMBERS_TO_LETTERS = {
    # Heuristic correction table for OCR errors (digit -> letter).
    # Intentionally empty: keeping interface but removing heuristics for now.
}
LETTERS_TO_NUMBERS = {
    # Heuristic correction table for OCR errors (letter -> digit).
    # Intentionally empty: keeping interface but removing heuristics for now.
}


@dataclass
class OcrBox:
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class OcrWord:
    text: str = ""
    bounding_box: OcrBox = field(default_factory=OcrBox)


@dataclass
class OcrLine:
    text: str = ""
    words: list[OcrWord] = field(default_factory=list)
    bounding_box: OcrBox = field(default_factory=OcrBox)


@dataclass
class OcrRecognition:
    text: str = ""
    lines: list[OcrLine] = field(default_factory=list)
    angle: float = 0.0


@dataclass
class OcrRequest:
    pixmap: QtGui.QPixmap
    language_tag: str = ""
    debug_dir: str | Path | None = None


@dataclass
class OcrResponse:
    text: str = ""
    error: str = ""
    pixmap: QtGui.QPixmap | None = None
    recognition: OcrRecognition | None = None


@dataclass(frozen=True)
class OcrTextAdapter:
    """Language-specific text composition rules kept separate from OCR IO."""
    name: str
    matches_language: Callable[[str], bool]
    compose_line: Callable[[OcrLine], str]
    finalize_text: Callable[[str], str]


class WindowsOcrEngine:
    def recognize(self, image_path: Path, language_tag: str) -> OcrRecognition:
        payload = run_windows_ocr_json(image_path, language_tag)
        return _parse_ocr_payload(payload)


def _image_bytes(image: QtGui.QImage) -> memoryview:
    """Return a writable byte view for direct pixel manipulation."""
    bits = image.bits()
    bits.setsize(image.sizeInBytes())
    return memoryview(bits)


def _otsu_threshold(grayscale_image: QtGui.QImage) -> int:
    """Compute Otsu threshold for an 8-bit grayscale image."""
    data = _image_bytes(grayscale_image)
    hist = [0] * 256
    for value in data:
        hist[value] += 1

    total = len(data)
    if total == 0:
        return 160

    sum_total = 0
    for i in range(256):
        sum_total += i * hist[i]

    sum_bg = 0.0
    weight_bg = 0
    max_var = -1.0
    threshold = 160

    for i in range(256):
        weight_bg += hist[i]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += i * hist[i]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if between > max_var:
            max_var = between
            threshold = i
    return threshold


def _to_high_contrast(image: QtGui.QImage) -> QtGui.QImage:
    """Convert image to OCR-friendly high-contrast RGB output."""
    gray = image.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
    w = max(1, gray.width())
    h = max(1, gray.height())

    # Super-sampling + downsampling smooths stair-step edges without hard binarization.
    smooth = gray.scaled(
        w * 2,
        h * 2,
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    ).scaled(
        w,
        h,
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QtGui.QImage.Format.Format_Grayscale8)

    data = _image_bytes(smooth)
    threshold = _otsu_threshold(smooth)

    # Normalize polarity: always white background + black text.
    dark = 0
    for px in data:
        if px <= threshold:
            dark += 1
    if dark > (len(data) // 2):
        for i in range(len(data)):
            data[i] = 255 - data[i]

    # Contrast stretch in grayscale domain to keep edge information.
    hist = [0] * 256
    for px in data:
        hist[px] += 1
    total = len(data)
    low_target = int(total * 0.02)
    high_target = int(total * 0.98)

    low = 0
    accum = 0
    for i in range(256):
        accum += hist[i]
        if accum >= low_target:
            low = i
            break

    high = 255
    accum = 0
    for i in range(255, -1, -1):
        accum += hist[i]
        if (total - accum) <= high_target:
            high = i
            break

    if high <= low:
        low, high = 0, 255

    scale = 255.0 / max(1, (high - low))
    for i in range(len(data)):
        v = int((data[i] - low) * scale)
        if v < 0:
            v = 0
        elif v > 255:
            v = 255
        # Keep grayscale edges but bias toward OCR-friendly white bg / dark text.
        if v > 178:
            v = min(255, int(178 + (v - 178) * 1.9))
        elif v < 120:
            v = max(0, int(v * 0.88))
        data[i] = v

    return smooth.convertToFormat(QtGui.QImage.Format.Format_RGB32)


def _normalize_source_image(pixmap: QtGui.QPixmap) -> QtGui.QImage:
    """Normalize DPR and pixel format to avoid HiDPI offset artifacts."""
    image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    if image.devicePixelRatio() != 1.0:
        image.setDevicePixelRatio(1.0)
    return image


def _scale_image(image: QtGui.QImage, scale_factor: float) -> QtGui.QImage:
    """Resize with smooth interpolation when scale is meaningfully different."""
    if abs(scale_factor - 1.0) <= 0.01:
        return image
    return image.scaled(
        max(1, int(round(image.width() * scale_factor))),
        max(1, int(round(image.height() * scale_factor))),
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    )


def _create_padded_canvas(image: QtGui.QImage, pad: int = 32) -> tuple[QtGui.QImage, int]:
    """
    Create centered quiet-zone padding using sampled background color.
    The returned image is opaque to avoid alpha-blended gray text.
    """
    bg_color = image.pixelColor(0, 0)
    if bg_color.alpha() < 255:
        bg_color.setAlpha(255)

    canvas = QtGui.QImage(
        image.width() + (pad * 2),
        image.height() + (pad * 2),
        QtGui.QImage.Format.Format_RGB32,
    )
    canvas.fill(bg_color)
    return canvas, pad


def _draw_boldened_text(dst: QtGui.QImage, src: QtGui.QImage, pad: int) -> None:
    """
    Draw source image with small offsets to thicken OCR strokes.
    Triple drawing is a pragmatic heuristic that improves OCR confidence.
    """
    painter = QtGui.QPainter(dst)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(1.0)
        painter.drawImage(pad, pad, src)
        painter.drawImage(pad + 1, pad, src)
        painter.drawImage(pad, pad + 1, src)
    finally:
        painter.end()


def _preprocess_for_ocr(pixmap: QtGui.QPixmap, scale_factor: float) -> QtGui.QImage:
    """
    Prepare an OCR-oriented image:
    1) normalize source pixels, 2) scale, 3) add quiet-zone padding,
    4) bolden strokes, 5) convert to normalized high-contrast output.
    """
    source = _normalize_source_image(pixmap)
    scaled = _scale_image(source, scale_factor)
    padded, pad = _create_padded_canvas(scaled)
    _draw_boldened_text(padded, scaled, pad)
    return _to_high_contrast(padded)


def _parse_box(obj: Any) -> OcrBox:
    """Parse a JSON object into OcrBox, accepting both PascalCase and camelCase keys."""
    if not isinstance(obj, dict):
        return OcrBox()
    return OcrBox(
        x=float(obj.get("X", obj.get("x", 0.0)) or 0.0),
        y=float(obj.get("Y", obj.get("y", 0.0)) or 0.0),
        width=float(obj.get("Width", obj.get("width", 0.0)) or 0.0),
        height=float(obj.get("Height", obj.get("height", 0.0)) or 0.0),
    )


def _compute_line_box(words: list[OcrWord]) -> OcrBox:
    """Compute a line box from word-level boxes when line box is missing."""
    if not words:
        return OcrBox()
    left = min(w.bounding_box.x for w in words)
    top = min(w.bounding_box.y for w in words)
    right = max(w.bounding_box.x + w.bounding_box.width for w in words)
    bottom = max(w.bounding_box.y + w.bounding_box.height for w in words)
    return OcrBox(x=left, y=top, width=max(0.0, right - left), height=max(0.0, bottom - top))


def _parse_word(word_obj: Any) -> OcrWord | None:
    """Parse one OCR word node."""
    if not isinstance(word_obj, dict):
        return None
    return OcrWord(
        text=str(word_obj.get("Text", "") or ""),
        bounding_box=_parse_box(word_obj.get("BoundingBox")),
    )


def _parse_line(line_obj: Any) -> OcrLine | None:
    """Parse one OCR line node and backfill line box from words if needed."""
    if not isinstance(line_obj, dict):
        return None

    words: list[OcrWord] = []
    for word_obj in line_obj.get("Words", []) or []:
        parsed = _parse_word(word_obj)
        if parsed is not None:
            words.append(parsed)

    line_box = _parse_box(line_obj.get("BoundingBox"))
    if line_box.width <= 0.0 or line_box.height <= 0.0:
        line_box = _compute_line_box(words)

    return OcrLine(
        text=str(line_obj.get("Text", "") or ""),
        words=words,
        bounding_box=line_box,
    )


def _parse_ocr_payload(payload: Any) -> OcrRecognition:
    """Parse Windows OCR JSON payload into internal dataclasses."""
    if not isinstance(payload, dict):
        return OcrRecognition()

    lines: list[OcrLine] = []
    for line_obj in payload.get("Lines", []) or []:
        parsed = _parse_line(line_obj)
        if parsed is not None:
            lines.append(parsed)

    return OcrRecognition(
        text=str(payload.get("Text", "") or ""),
        lines=lines,
        angle=float(payload.get("Angle", 0.0) or 0.0),
    )


def _recognize_qimage(
    image: QtGui.QImage,
    language_tag: str = "",
    engine: WindowsOcrEngine | None = None,
) -> OcrRecognition:
    """Run OCR on a temporary file generated from QImage."""
    if engine is None:
        engine = WindowsOcrEngine()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        if not image.save(str(tmp_path), "BMP"):
            return OcrRecognition()
        return engine.recognize(tmp_path, language_tag=language_tag)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _recommend_scale_factor(result: OcrRecognition, width: int, height: int) -> float:
    """Estimate a second-pass OCR scale based on detected word heights."""
    heights = [
        word.bounding_box.height
        for line in result.lines
        for word in line.words
        if word.bounding_box.height > 0
    ]

    line_height = (sum(heights) / len(heights)) if heights else 10.0
    if line_height <= 0:
        return INITIAL_SCALE_FACTOR

    scale_factor = IDEAL_LINE_HEIGHT_PX / line_height

    larger_dimension = max(width, height, 1)
    max_allowed_scale = MAX_OCR_IMAGE_DIMENSION / larger_dimension
    scale_factor = min(scale_factor, max_allowed_scale)

    return max(0.25, min(scale_factor, 4.0))


def _is_space_joining_word(token: str) -> bool:
    r"""
    Text-Grab style SpaceJoiningWordRegex: (^[\p{L}-[\p{Lo}]]|\p{Nd}$)|.{2,}
    Matches words that should trigger a space-joining behavior.
    """
    if not token:
        return False
    
    # Length 2 or more always joins with space (Latin words, grouped CJK)
    if len(token) >= 2:
        return True

    ch = token[0]
    cat = unicodedata.category(ch)
    
    # \p{Nd}$ : Single digit
    if cat == "Nd":
        return True

    # ^[\p{L}-[\p{Lo}]] : Single letter that is NOT an 'Other_Letter' (CJK)
    if cat.startswith("L") and cat != "Lo":
        return True

    return False


def _cleanup_ocr_text_line(text: str) -> str:
    """Normalize spacing around punctuation and CJK scripts."""
    text = re.sub(rf"(?<=[{NO_SPACE_SCRIPT_CHAR_CLASS}])\s+(?=[{NO_SPACE_SCRIPT_CHAR_CLASS}])", "", text)
    text = re.sub(r"\s+([,;:.!?])", r"\1", text)
    text = re.sub(r"([,;:.!?])(?=[A-Za-z0-9])", r"\1 ", text)
    return text


def _normalize_token_text(token: str) -> str:
    return unicodedata.normalize("NFKC", (token or "").strip())


def _compose_default_line_text(line: OcrLine) -> str:
    return _cleanup_ocr_text_line((line.text or "").strip())


def _compose_spaced_line_text(line: OcrLine) -> str:
    return _compose_default_line_text(line)


def _compose_cjk_line_text(line: OcrLine) -> str:
    if not line.words:
        return _compose_default_line_text(line)

    parts = []
    is_first_word = True
    is_prev_word_space_joining = False

    for word in line.words:
        token = _normalize_token_text(word.text)
        if not token:
            continue

        is_this_word_space_joining = _is_space_joining_word(token)

        if is_first_word or (not is_this_word_space_joining and not is_prev_word_space_joining):
            parts.append(token)
        else:
            parts.append(f" {token}")
        
        is_first_word = False
        is_prev_word_space_joining = is_this_word_space_joining

    joined = "".join(parts).strip()
    if not joined:
        joined = (line.text or "").strip()

    return _cleanup_ocr_text_line(joined)


def _normalize_ocr_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        lines.append(_cleanup_ocr_text_line(line))
    return "\n".join(lines).strip()


def _replace_with_map(s: str, mapping: dict[str, str]) -> str:
    return "".join(mapping.get(ch, ch) for ch in s)


def _try_fix_number_letter_errors(token: str) -> str:
    if len(token) < 5:
        return token
    total_numbers = sum(1 for ch in token if ch.isdigit())
    total_letters = sum(1 for ch in token if ch.isalpha())
    if total_numbers / max(1, len(token)) >= 0.6:
        return _replace_with_map(token, LETTERS_TO_NUMBERS)
    if total_letters / max(1, len(token)) >= 0.6:
        return _replace_with_map(token, NUMBERS_TO_LETTERS)
    return token


def _try_fix_every_word_letter_number_errors(text: str) -> str:
    words = text.split(" ")
    fixed = [_try_fix_number_letter_errors(word) for word in words]
    joined = " ".join(fixed)
    joined = joined.replace("\t ", "\t").replace("\r ", "\r").replace("\n ", "\n")
    return joined.strip()


def _matches_chinese(language_tag: str) -> bool:
    return (language_tag or "").lower().startswith("zh")


def _matches_english(language_tag: str) -> bool:
    return (language_tag or "").lower().startswith("en")


def _finalize_default_text(text: str) -> str:
    return _normalize_ocr_text(text)


def _finalize_english_text(text: str) -> str:
    return _try_fix_every_word_letter_number_errors(_normalize_ocr_text(text))


LANGUAGE_TEXT_ADAPTERS = (
    OcrTextAdapter(
        name="chinese",
        matches_language=_matches_chinese,
        compose_line=_compose_cjk_line_text,
        finalize_text=_finalize_default_text,
    ),
    OcrTextAdapter(
        name="english",
        matches_language=_matches_english,
        compose_line=_compose_spaced_line_text,
        finalize_text=_finalize_english_text,
    ),
    OcrTextAdapter(
        name="default",
        matches_language=lambda _language_tag: True,
        compose_line=_compose_default_line_text,
        finalize_text=_finalize_default_text,
    ),
)


def _select_text_adapter(language_tag: str) -> OcrTextAdapter:
    for adapter in LANGUAGE_TEXT_ADAPTERS:
        if adapter.matches_language(language_tag):
            return adapter
    return LANGUAGE_TEXT_ADAPTERS[-1]


def _compose_text_from_result(result: OcrRecognition, language_tag: str = "") -> str:
    adapter = _select_text_adapter(language_tag)

    if not result.lines:
        return adapter.finalize_text(result.text)

    built_lines = []
    for line in result.lines:
        joined = adapter.compose_line(line)
        if joined:
            built_lines.append(joined)

    if not built_lines:
        return adapter.finalize_text(result.text)

    output = "\n".join(built_lines).strip()
    return adapter.finalize_text(output)


def _save_debug_preprocessed_image(image: QtGui.QImage, debug_dir: str | Path | None) -> None:
    """Best-effort debug image dump; failures are logged but non-fatal."""
    if not debug_dir:
        return
    try:
        debug_path = Path(debug_dir) / "ocr_debug_preprocessed.png"
        image.save(str(debug_path), "PNG")
        logger.debug(f"Saved OCR debug image to: {debug_path}")
    except Exception as exc:
        logger.warning(f"Failed to save OCR debug image: {exc}")


def recognize_result_from_pixmap(
    pixmap: QtGui.QPixmap,
    language_tag: str = "",
    debug_dir: str | Path | None = None,
) -> OcrRecognition:
    """
    Run OCR with optional adaptive second pass.
    First pass is fast baseline; second pass is used only when scale estimate differs enough.
    """
    total_start = time.perf_counter()

    if pixmap.isNull():
        return OcrRecognition()

    engine = WindowsOcrEngine()

    initial_image = _preprocess_for_ocr(pixmap, INITIAL_SCALE_FACTOR)
    _save_debug_preprocessed_image(initial_image, debug_dir)

    initial_result = _recognize_qimage(initial_image, language_tag=language_tag, engine=engine)

    if not initial_result.text and not initial_result.lines:
        logger.info(f"OCR Completed in {time.perf_counter() - total_start:.2f}s (empty result)")
        return OcrRecognition()

    recommended_scale = _recommend_scale_factor(initial_result, initial_image.width(), initial_image.height())
    final_result = initial_result

    if abs(recommended_scale - INITIAL_SCALE_FACTOR) >= MIN_RESCALE_DELTA:
        rescaled_image = _preprocess_for_ocr(pixmap, recommended_scale)
        rescaled_result = _recognize_qimage(rescaled_image, language_tag=language_tag, engine=engine)
        if rescaled_result.text or rescaled_result.lines:
            final_result = rescaled_result

    logger.info(
        f"OCR Completed in {time.perf_counter() - total_start:.2f}s "
        f"(engine=windows, scale={recommended_scale:.2f}, lines={len(final_result.lines)})"
    )
    return final_result


def recognize_text_from_pixmap(
    pixmap: QtGui.QPixmap,
    language_tag: str = "",
    debug_dir: str | Path | None = None,
) -> str:
    result = recognize_result_from_pixmap(
        pixmap,
        language_tag=language_tag,
        debug_dir=debug_dir,
    )
    return _compose_text_from_result(result, language_tag=language_tag)


class OcrService:
    """
    Async/sync OCR service abstraction.
    Keeps threading and error handling outside UI modules.
    """

    def recognize(self, request: OcrRequest) -> OcrResponse:
        try:
            recognition = recognize_result_from_pixmap(
                request.pixmap,
                language_tag=request.language_tag,
                debug_dir=request.debug_dir,
            )
            text = _compose_text_from_result(recognition, language_tag=request.language_tag)
            return OcrResponse(
                text=text,
                error="",
                pixmap=request.pixmap,
                recognition=recognition,
            )
        except Exception as exc:
            logger.exception(f"OCR service failed: {exc}")
            return OcrResponse(
                text="",
                error=str(exc),
                pixmap=request.pixmap,
                recognition=None,
            )

    def recognize_async(self, request: OcrRequest, done_callback):
        def worker():
            done_callback(self.recognize(request))

        threading.Thread(target=worker, daemon=True).start()
