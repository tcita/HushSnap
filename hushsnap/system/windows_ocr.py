import json
import logging
import subprocess
from pathlib import Path
import asyncio
from typing import Any

logger = logging.getLogger(__name__)

# WinRT imports
try:
    import winrt.windows.foundation as foundation
    import winrt.windows.graphics.imaging as imaging
    import winrt.windows.media.ocr as ocr
    import winrt.windows.storage as storage
    import winrt.windows.globalization as globalization
    HAS_WINRT = True
except ImportError:
    HAS_WINRT = False


def _build_powershell_script(image_path: Path, language_tag: str) -> str:
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
$requestedLanguageCandidates = @()
if ('{escaped_language_tag}') {{
    $requestedLanguageCandidates += '{escaped_language_tag}'
    $normalizedRequestedTag = '{escaped_language_tag}'.ToLowerInvariant()
    if ($normalizedRequestedTag -in @('zh-cn', 'zh-sg', 'zh-hans', 'zh')) {{
        $requestedLanguageCandidates += @('zh-CN', 'zh-SG', 'zh-Hans')
    }} elseif ($normalizedRequestedTag -in @('zh-tw', 'zh-hk', 'zh-mo', 'zh-hant')) {{
        $requestedLanguageCandidates += @('zh-TW', 'zh-HK', 'zh-MO', 'zh-Hant')
    }}
    $requestedLanguageCandidates = $requestedLanguageCandidates | Select-Object -Unique

    foreach ($candidateTag in $requestedLanguageCandidates) {{
        try {{
            $lang = [Windows.Globalization.Language]::new($candidateTag)
            if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($lang)) {{
                $requestedLanguageSupported = $true
                $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
                if ($null -ne $engine) {{ break }}
            }}
        }} catch {{}}
    }}
}}
if ($null -eq $engine) {{
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    $usedUserProfileFallback = $true
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

# Alias for backward compatibility with tests
build_windows_ocr_script = _build_powershell_script


def _run_windows_ocr_powershell(image_path: Path, language_tag: str = "") -> dict[str, Any]:
    """Fallback: Invoke Windows OCR through PowerShell subprocess."""
    script = _build_powershell_script(image_path, language_tag)
    
    startupinfo = None
    creationflags = 0
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags |= subprocess.CREATE_NO_WINDOW

    try:
        completed = subprocess.run(
            ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            startupinfo=startupinfo, creationflags=creationflags
        )
        if completed.returncode != 0:
            return {"Error": (completed.stderr or "PowerShell failed").strip()}
        
        stdout = (completed.stdout or "").strip()
        if not stdout:
            return {}
        return json.loads(stdout)
    except Exception as exc:
        return {"Error": str(exc)}

async def _run_windows_ocr_direct_async(image_path: Path, language_tag: str = "") -> dict[str, Any]:
    """Directly call Windows OCR WinRT APIs."""
    try:
        # 1. Load image
        file = await storage.StorageFile.get_file_from_path_async(str(image_path.absolute()))
        stream = await file.open_read_async()
        decoder = await imaging.BitmapDecoder.create_async(stream)
        
        # Standard WinRT way to get a specific format: use the 'converted' async method.
        try:
            bitmap = await decoder.get_software_bitmap_converted_async(
                imaging.BitmapPixelFormat.BGRA8, 
                imaging.BitmapAlphaMode.PREMULTIPLIED
            )
        except Exception as e:
            logger.debug(f"Direct conversion failed, using default format: {e}")
            bitmap = await decoder.get_software_bitmap_async()

        # 2. Select engine
        engine = None
        requested_supported = False
        used_fallback = False
        
        if language_tag:
            tags_to_try = [language_tag]
            lowered = language_tag.lower()
            if lowered in ('zh-cn', 'zh-sg', 'zh-hans', 'zh'):
                tags_to_try.extend(['zh-CN', 'zh-SG', 'zh-Hans'])
            elif lowered in ('zh-tw', 'zh-hk', 'zh-mo', 'zh-hant'):
                tags_to_try.extend(['zh-TW', 'zh-HK', 'zh-MO', 'zh-Hant'])
            
            for tag in tags_to_try:
                try:
                    lang = globalization.Language(tag)
                    if ocr.OcrEngine.is_language_supported(lang):
                        requested_supported = True
                        engine = ocr.OcrEngine.try_create_from_language(lang)
                        if engine: break
                except Exception: continue
        
        if not engine:
            engine = ocr.OcrEngine.try_create_from_user_profile_languages()
            used_fallback = True
            
        if not engine:
            return {"Error": "Windows OCR engine unavailable."}

        # 3. Recognize
        result = await engine.recognize_async(bitmap)

        # 4. Build payload
        payload = {
            "Text": result.text,
            "Angle": result.text_angle if result.text_angle is not None else 0.0,
            "RequestedLanguageTag": language_tag,
            "RequestedLanguageSupported": requested_supported,
            "UsedUserProfileFallback": used_fallback,
            "EngineLanguageTag": engine.recognizer_language.language_tag if engine.recognizer_language else "",
            "Lines": []
        }

        for line in result.lines:
            line_payload = {"Text": line.text, "Words": []}
            for word in line.words:
                line_payload["Words"].append({
                    "Text": word.text,
                    "BoundingBox": {
                        "X": float(word.bounding_rect.x),
                        "Y": float(word.bounding_rect.y),
                        "Width": float(word.bounding_rect.width),
                        "Height": float(word.bounding_rect.height)
                    }
                })
            payload["Lines"].append(line_payload)
        return payload
    except Exception as exc:
        logger.exception(f"Direct Windows OCR failed: {exc}")
        return {"Error": str(exc)}

def run_windows_ocr_json(image_path: Path, language_tag: str = "") -> dict[str, Any]:
    """
    Hybrid Windows OCR entry point.
    Uses direct WinRT API if available, otherwise falls back to PowerShell subprocess.
    """
    if HAS_WINRT:
        logger.debug("Using direct WinRT API for Windows OCR (Fast mode)")
        try:
            return asyncio.run(_run_windows_ocr_direct_async(image_path, language_tag))
        except Exception as exc:
            logger.warning(f"WinRT OCR failed, falling back to PowerShell: {exc}")
    
    logger.info("Using PowerShell subprocess for Windows OCR (OOTB/Compatibility mode)")
    return _run_windows_ocr_powershell(image_path, language_tag)
