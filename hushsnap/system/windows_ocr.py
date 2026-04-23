import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

def build_windows_ocr_script(image_path: Path, language_tag: str) -> str:
    """Build the PowerShell script that runs Windows OCR and prints JSON."""
    escaped_path = str(image_path).replace("'", "''")
    escaped_language_tag = (language_tag or "").replace("'", "''")

    return f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]        
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]       
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
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

if ($bitmap.BitmapPixelFormat -ne [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8 -or
    $bitmap.BitmapAlphaMode -ne [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied) {{
    $bitmap = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert(
        $bitmap,
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
    )
}}

$engine = $null
$requestedLanguageSupported = $false
$usedUserProfileFallback = $false
if ('{escaped_language_tag}') {{
    try {{
        $lang = [Windows.Globalization.Language]::new('{escaped_language_tag}')
        if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($lang)) {{
            $requestedLanguageSupported = $true
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
        }}
    }} catch {{
    }}
}}
if ($null -eq $engine) {{
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    $usedUserProfileFallback = $true
}}
if ($null -eq $engine) {{
    throw 'Windows OCR engine unavailable.'
}}

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$payload = [ordered]@{{
    Text = $result.Text
    Angle = $(if ($null -ne $result.TextAngle) {{ [double]$result.TextAngle }} else {{ 0.0 }})
    RequestedLanguageTag = '{escaped_language_tag}'
    RequestedLanguageSupported = $requestedLanguageSupported
    UsedUserProfileFallback = $usedUserProfileFallback
    EngineLanguageTag = $(if ($null -ne $engine.RecognizerLanguage) {{ $engine.RecognizerLanguage.LanguageTag }} else {{ '' }})
    Lines = @()
}}

foreach ($line in $result.Lines) {{
    $linePayload = [ordered]@{{
        Text = $line.Text
        BoundingBox = [ordered]@{{
            X = 0.0
            Y = 0.0
            Width = 0.0
            Height = 0.0
        }}
        Words = @()
    }}

    foreach ($word in $line.Words) {{
        $linePayload.Words += [ordered]@{{
            Text = $word.Text
            BoundingBox = [ordered]@{{
                X = [double]$word.BoundingRect.X
                Y = [double]$word.BoundingRect.Y
                Width = [double]$word.BoundingRect.Width
                Height = [double]$word.BoundingRect.Height
            }}
        }}
    }}

    $payload.Lines += $linePayload
}}

[Console]::Write(($payload | ConvertTo-Json -Depth 8 -Compress))
"""

def run_windows_ocr_json(image_path: Path, language_tag: str = "") -> dict:
    """Invoke Windows OCR through PowerShell and return the raw JSON dict."""
    script = build_windows_ocr_script(image_path, language_tag)

    startupinfo = None
    creationflags = 0
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags |= subprocess.CREATE_NO_WINDOW

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
        startupinfo=startupinfo,
        creationflags=creationflags,
    )

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        if stderr:
            logger.error(f"Windows OCR failed: {stderr}")
            return {"Error": stderr}
        return {"Error": "Windows OCR process failed."}

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {}

    try:
        payload = json.loads(stdout)
    except Exception as exc:
        logger.warning(f"Failed to parse OCR JSON payload: {exc}")
        return {"Text": stdout}

    if language_tag and isinstance(payload, dict):
        requested_supported = payload.get("RequestedLanguageSupported")
        used_fallback = payload.get("UsedUserProfileFallback")
        engine_language_tag = str(payload.get("EngineLanguageTag", "") or "").strip()

        if requested_supported is False and used_fallback:
            fallback_target = engine_language_tag or "user profile language"
            logger.warning(
                "Requested OCR language '%s' is not installed or supported by Windows OCR; "
                "falling back to '%s'. Install the matching Windows language pack to use it.",
                language_tag,
                fallback_target,
            )

    return payload
