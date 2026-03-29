"""
Windows native OCR helpers.
Includes image preprocessing and OCR invocation via WinRT (Windows.Media.Ocr).
"""

import subprocess
import tempfile
from pathlib import Path

from PyQt6 import QtCore, QtGui


def _clamp_uint8(value):
    return max(0, min(255, int(value)))


def preprocess_pixmap_for_ocr(pixmap):
    """
    Prepare screenshot image for OCR accuracy.
    Pipeline: grayscale -> contrast boost -> binarization -> upscale.
    """
    image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_Grayscale8)

    bits = image.bits()
    bits.setsize(image.sizeInBytes())
    data = memoryview(bits)

    # Build LUT once: contrast + threshold in one pass.
    contrast_factor = 1.45
    threshold = 158
    lut = [0] * 256
    for src in range(256):
        enhanced = _clamp_uint8((src - 128) * contrast_factor + 128)
        lut[src] = 255 if enhanced >= threshold else 0

    for idx in range(len(data)):
        data[idx] = lut[data[idx]]

    # Upscale after denoising to improve native OCR on small text.
    scale_factor = 2.5
    upscaled = image.scaled(
        int(image.width() * scale_factor),
        int(image.height() * scale_factor),
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    )
    return upscaled


def _run_windows_ocr(image_path):
    escaped_path = str(image_path).replace("'", "''")
    script = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Foundation.IAsyncOperation`1, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]

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
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {{
    throw 'Windows OCR engine is unavailable for current language profile.'
}}
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
[Console]::Write($result.Text)
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
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(stderr or "Windows OCR command failed.")
    return (completed.stdout or "").strip()


def recognize_text_from_pixmap(pixmap):
    """
    Run Windows native OCR on a screenshot pixmap with preprocessing.
    """
    processed = preprocess_pixmap_for_ocr(pixmap)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        if not processed.save(str(tmp_path), "PNG"):
            raise RuntimeError("Failed to save temporary OCR image.")
        return _run_windows_ocr(tmp_path)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
