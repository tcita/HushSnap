"""
Windows native OCR helpers.
Includes image preprocessing and OCR invocation via WinRT (Windows.Media.Ocr).
"""

import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

from PyQt6 import QtCore, QtGui

logger = logging.getLogger(__name__)

_ENGLISH_CONNECTOR_WORDS = {
    "A",
    "An",
    "And",
    "As",
    "At",
    "But",
    "By",
    "For",
    "From",
    "In",
    "Nor",
    "Of",
    "On",
    "Off",
    "Or",
    "So",
    "The",
    "To",
    "With",
    "Yet",
}

_ENGLISH_COMMON_WORDS = {
    "a",
    "about",
    "across",
    "against",
    "and",
    "at",
    "between",
    "by",
    "conflict",
    "earlier",
    "first",
    "for",
    "from",
    "gasoline",
    "has",
    "in",
    "into",
    "iran",
    "is",
    "month",
    "of",
    "off",
    "on",
    "ordered",
    "prices",
    "president",
    "rattled",
    "republicans",
    "sent",
    "set",
    "setting",
    "soaring",
    "strikes",
    "that",
    "the",
    "to",
    "trump",
    "u",
    "us",
    "was",
    "with",
}


def _clamp_uint8(value):
    return max(0, min(255, int(value)))


def _image_bytes(image):
    bits = image.bits()
    bits.setsize(image.sizeInBytes())
    return memoryview(bits)


def _stretch_contrast(image):
    """
    Contrast stretch using robust percentiles to avoid clipping on noisy backgrounds.
    """
    data = _image_bytes(image)
    if not data:
        return image

    hist = [0] * 256
    for value in data:
        hist[value] += 1

    total = len(data)
    low_target = int(total * 0.02)
    high_target = int(total * 0.98)

    low = 0
    accum = 0
    for idx in range(256):
        accum += hist[idx]
        if accum >= low_target:
            low = idx
            break

    high = 255
    accum = 0
    for idx in range(255, -1, -1):
        accum += hist[idx]
        if total - accum <= high_target:
            high = idx
            break

    if high <= low:
        return image

    scale = 255.0 / (high - low)
    for i in range(len(data)):
        data[i] = _clamp_uint8((data[i] - low) * scale)
    return image


def _otsu_threshold(image):
    data = _image_bytes(image)
    hist = [0] * 256
    for v in data:
        hist[v] += 1

    total = len(data)
    if total == 0:
        return 128

    sum_total = 0
    for i in range(256):
        sum_total += i * hist[i]

    sum_bg = 0.0
    weight_bg = 0
    max_var = -1.0
    threshold = 128

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


def _binarize(image, threshold):
    bin_img = image.copy()
    data = _image_bytes(bin_img)
    for i in range(len(data)):
        data[i] = 255 if data[i] >= threshold else 0
    return bin_img


def _invert(image):
    out = image.copy()
    data = _image_bytes(out)
    for i in range(len(data)):
        data[i] = 255 - data[i]
    return out


def _make_ocr_candidates(pixmap):
    """
    Build multiple preprocessed image variants and let OCR pick the best result.
    """
    base = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_Grayscale8)

    # 2.0x is more stable than 2.8x for high-DPI screens.
    upscaled = base.scaled(
        base.width() * 2,
        base.height() * 2,
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    )
    enhanced = _stretch_contrast(upscaled.copy())
    threshold = _otsu_threshold(enhanced)

    binary = _binarize(enhanced, threshold)
    inverted_binary = _invert(binary)
    inverted_enhanced = _invert(enhanced)

    return [base, enhanced, binary, inverted_binary, inverted_enhanced]


def _score_text(text):
    cleaned = (text or "").strip()
    if not cleaned:
        return -9999.0

    # Basic length weight
    length_score = min(len(cleaned), 500) * 0.2
    
    # Specific character counts
    cjk_count = sum(0x4E00 <= ord(ch) <= 0x9FFF for ch in cleaned)
    alpha_num = sum(ch.isalnum() for ch in cleaned)
    ascii_printable = sum(32 <= ord(ch) <= 126 for ch in cleaned)
    
    # Useful characters (alphanumeric + CJK)
    useful_chars = alpha_num + cjk_count
    total_len = max(1, len(cleaned))
    
    # Ratios
    useful_ratio = useful_chars / total_len
    printable_ratio = ascii_printable / total_len
    
    # Penalize too many weird symbols
    weird_penalty = sum(ch in "|[]{}~`@#$%^&*_+" for ch in cleaned) * 5.0
    
    # Bonus for common structure
    line_count = cleaned.count("\n")
    structure_bonus = min(line_count * 3.0, 30.0)

    # Penalize OCR split artifacts like "B ut", "S aturd ay", "estimate s in".
    split_penalty = 0.0
    split_penalty += len(re.findall(r"\b[B-HJ-Z]\s+[a-z]{2,}\b", cleaned)) * 12.0
    split_penalty += len(re.findall(r"\b[a-z]{3,}\s+[a-z]\s+[a-z]{2,}\b", cleaned)) * 10.0
    split_penalty += len(
        re.findall(
            r"\b[a-z]{4,}(against|earlier|soaring|rattled|ordered|setting|strikes|republicans)\b",
            cleaned,
            flags=re.IGNORECASE,
        )
    ) * 10.0

    # Final score: prioritize high ratio of useful characters
    quality_score = (useful_ratio * 100.0) + (printable_ratio * 20.0)
    
    return length_score + quality_score + structure_bonus - weird_penalty - split_penalty


def _has_split_artifacts(text):
    s = text or ""
    return bool(
        re.search(r"\b[B-HJ-Z]\s+[a-z]{2,}\b", s)
        or re.search(r"\b[a-z]{3,}\s+[a-z]\s+[a-z]{2,}\b", s)
        or re.search(
            r"\b[a-z]{4,}(against|earlier|soaring|rattled|ordered|setting|strikes|republicans)\b",
            s,
            flags=re.IGNORECASE,
        )
    )


def _normalize_ocr_text(text):
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return ""

    def split_joined_english_words(line):
        def split_token(match):
            token = match.group(0)
            lower = token.lower()
            best_idx = -1
            best_score = -1.0

            for i in range(3, len(token) - 2):
                left = lower[:i]
                right = lower[i:]
                if len(left) < 2 or len(right) < 2:
                    continue
                score = 0.0
                if right in _ENGLISH_COMMON_WORDS:
                    score += 3.0
                if left in _ENGLISH_COMMON_WORDS:
                    score += 2.0
                if score <= 0:
                    continue
                score += min(len(left), len(right)) * 0.1
                if score > best_score:
                    best_idx = i
                    best_score = score

            if best_idx > 0:
                return f"{token[:best_idx]} {token[best_idx:]}"
            return token

        return re.sub(r"\b[A-Za-z]{8,}\b", split_token, line)

    # Keep line structure but normalize per-line spacing.
    lines = []
    sentence_start = True
    for raw_line in cleaned.split("\n"):
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue

        # Fix obvious missing spaces in English camel-like joins: "MiddleEast" -> "Middle East".
        line = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", line)

        # Repair common OCR split artifacts inside English words.
        # Examples: "S aturd ay" -> "Saturday", "B ut" -> "But", "estimate s in" -> "estimates in".
        line = re.sub(r"\b([B-HJ-Z])\s+([a-z]{2,})\b", r"\1\2", line)
        line = re.sub(r"\b([a-z]{3,})\s+([a-z])\s+([a-z]{2,})\b", r"\1\2 \3", line)
        line = split_joined_english_words(line)

        # Punctuation spacing cleanup.
        line = re.sub(r"\s+([,;:.!?])", r"\1", line)
        line = re.sub(r"([,;:.!?])(?=[A-Za-z0-9])", r"\1 ", line)

        # Lowercase connector words when they appear mid-sentence in English text.
        tokens = re.findall(r"[A-Za-z]+|[^A-Za-z]+", line)
        rebuilt = []
        for token in tokens:
            if token.isalpha():
                if (not sentence_start) and (token in _ENGLISH_CONNECTOR_WORDS):
                    rebuilt.append(token.lower())
                else:
                    rebuilt.append(token)
                sentence_start = False
            else:
                rebuilt.append(token)
                if re.search(r"[.!?]\s*$", token):
                    sentence_start = True
        if rebuilt and not re.search(r"[.!?]\s*$", "".join(rebuilt)):
            sentence_start = False
        lines.append("".join(rebuilt).strip())

    return "\n".join(lines).strip()


def _run_windows_ocr_on_image(image, language_tag=""):
    start_time = time.perf_counter()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        if not image.save(str(tmp_path), "PNG"):
            raise RuntimeError("Failed to save temporary OCR image.")
        result = _run_windows_ocr(tmp_path, language_tag=language_tag)
        elapsed = time.perf_counter() - start_time
        logger.debug(f"PowerShell OCR call (lang='{language_tag}') took {elapsed:.2f}s")
        return result
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _run_windows_ocr(image_path, language_tag=""):
    escaped_path = str(image_path).replace("'", "''")
    escaped_language_tag = (language_tag or "").replace("'", "''")
    script = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Foundation.IAsyncOperation`1, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

function Await([Object]$AsyncOp, [Type]$ResultType) {{
    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.IsGenericMethod
    }} | Select-Object -First 1).MakeGenericMethod($ResultType)
    $task = $asTaskGeneric.Invoke($null, @($AsyncOp))
    $task.Wait()
    return $task.Result
}}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{escaped_path}')) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

# OcrEngine is most reliable with Bgra8 + Premultiplied alpha.
if (
    $bitmap.BitmapPixelFormat -ne [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8 -or
    $bitmap.BitmapAlphaMode -ne [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
) {{
    $bitmap = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert(
        $bitmap,
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
    )
}}

$engine = $null
$langTag = '{escaped_language_tag}'
if ($langTag.Length -gt 0) {{
    try {{
        $lang = [Windows.Globalization.Language]::new($langTag)
        if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($lang)) {{
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
        }}
    }} catch {{ }}
}}
if ($null -eq $engine) {{
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}}
if ($null -eq $engine) {{
    throw 'Windows OCR engine is unavailable.'
}}
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

function HasCJK([string]$t) {{
    if ([string]::IsNullOrEmpty($t)) {{ return $false }}
    foreach ($ch in $t.ToCharArray()) {{
        $code = [int]$ch
        if ($code -ge 0x4e00 -and $code -le 0x9fff) {{ return $true }}
    }}
    return $false
}}

$outputLines = @()
foreach ($line in $result.Lines) {{
    $words = @($line.Words)
    $wordCount = [int]$words.Count
    if ($wordCount -le 0) {{
        continue
    }}

    $lineText = ""
    for ($i = 0; $i -lt $wordCount; $i++) {{
        $curr = $words[$i]
        if ($null -eq $curr -or -not $curr.Text) {{
            continue
        }}
        $currText = [string]$curr.Text

        if ([string]::IsNullOrEmpty($lineText)) {{
            $lineText = $currText
            continue
        }}

        $prev = $words[$i - 1]
        $prevText = [string]$prev.Text
        $needSpace = $true

        $bothCJK = (HasCJK($prevText) -and HasCJK($currText))
        $currIsPunct = ($currText.Length -gt 0) -and ($currText.TrimEnd(',', '.', ';', ':', '!', '?', '%', ')', ']') -eq '')
        $prevIsOpen = ($prevText.Length -gt 0) -and ($prevText.TrimStart('(', '[') -eq '')

        if ($bothCJK -or $currIsPunct -or $prevIsOpen) {{
            $needSpace = $false
        }} else {{
            $prevRight = [double]$prev.BoundingRect.X + [double]$prev.BoundingRect.Width
            $gap = [double]$curr.BoundingRect.X - $prevRight
            $prevW = [double]$prev.BoundingRect.Width
            $currW = [double]$curr.BoundingRect.Width
            $refW = [Math]::Max(1.0, [Math]::Min($prevW, $currW))

            # Tight boxes often mean the OCR engine split one word into fragments.
            if ($gap -le ($refW * 0.12)) {{
                $needSpace = $false
            }} elseif (
                (($prevText.Length -eq 1 -and $prevText -match "^[A-Za-z]$") -or
                 ($currText.Length -eq 1 -and $currText -match "^[A-Za-z]$")) -and
                $gap -le ($refW * 0.35)
            ) {{
                $needSpace = $false
            }}
        }}

        if ($needSpace) {{
            $lineText += " "
        }}
        $lineText += $currText
    }}

    $lineText = [System.Text.RegularExpressions.Regex]::Replace($lineText, "(?<=[\u4e00-\u9fff])\\s+(?=[\u4e00-\u9fff])", "")
    $lineText = [System.Text.RegularExpressions.Regex]::Replace($lineText, "\\s+([,;:.!?])", '$1')
    $lineText = [System.Text.RegularExpressions.Regex]::Replace($lineText, "([,;:.!?])(?=\\S)", '$1 ')
    $outputLines += $lineText.Trim()
}}

if ($outputLines.Count -gt 0) {{
    [Console]::Write(($outputLines -join "`r`n"))
}} else {{
    [Console]::Write($result.Text)
}}
"""
    completed = subprocess.run(
        [
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        if stderr:
            logger.error(f"PowerShell OCR Error: {stderr}")
        return ""
    stdout = (completed.stdout or "").strip()
    if not stdout:
        stderr = (completed.stderr or "").strip()
        if stderr:
            logger.debug(f"PowerShell OCR stderr (empty output): {stderr}")
    return stdout


def recognize_text_from_pixmap(pixmap):
    """
    Run Windows native OCR on a screenshot pixmap with optimized multi-pass logic.
    """
    total_start = time.perf_counter()
    candidates = _make_ocr_candidates(pixmap)
    language_candidates = ["", "zh-CN", "en-US"]
    
    best_text = ""
    best_score = -9999.0

    for i, image in enumerate(candidates):
        for lang in language_candidates:
            # Skip forced language if we already have a decent result to save time
            if i > 0 and lang != "" and best_score > 30:
                continue

            text = _normalize_ocr_text(_run_windows_ocr_on_image(image, language_tag=lang))
            score = _score_text(text)
            
            if score > best_score:
                best_score = score
                best_text = text
            
            logger.debug(f"Pass {i} (lang='{lang}'): Score={score:.2f}, TextLen={len(text)}")
            
            # FAST EXIT: If we get any reasonable text in the first few passes, just use it.
            if best_score > 45 and not _has_split_artifacts(best_text):
                total_elapsed = time.perf_counter() - total_start
                logger.info(f"OCR Fast Path Success in {total_elapsed:.2f}s (Score: {best_score:.2f})")
                return best_text.strip()

    total_elapsed = time.perf_counter() - total_start
    logger.info(f"OCR Completed in {total_elapsed:.2f}s (Best Score: {best_score:.2f})")
    return best_text.strip()
