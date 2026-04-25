"""
HushSnap OCR Evaluator
--------------------------------
A diagnostic tool to compare raw-image OCR against the current HushSnap
single-path preprocessing pipeline.

Usage:
    0. Edit `tools/ocr_eval_config.toml` to turn preprocessing steps on/off.
    1. Create an `ocr_eval_data` folder in the project root.
    2. Add images to `ocr_eval_data/` (or subfolders like `sc/`, `en/`).
    3. Run: python tools/ocr_evaluator.py

The report will show:
    - Original image and the generated pipeline image.
    - Active preprocessing chain.
    - Baseline OCR text vs pipeline OCR text.
"""

import argparse
import html
import logging
import os
import re
import shutil
import sys
import tomllib
from difflib import SequenceMatcher
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.append(str(PROJECT_ROOT))

from PyQt6 import QtGui, QtWidgets

from hushsnap.ocr.preprocess import OcrPreprocessSettings, run_preprocess_pipeline
from hushsnap.ocr.recognition import recognize_qimage
from hushsnap.ocr.text import compose_text_from_result

# Initialize QApplication
app = QtWidgets.QApplication(sys.argv)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ocr_evaluator")

LANG_MAP = {
    "sc": "zh-Hans",
    "chs": "zh-Hans",
    "cn": "zh-Hans",
    "tc": "zh-Hant",
    "cht": "zh-Hant",
    "tw": "zh-Hant",
    "hk": "zh-Hant",
    "en": "en-US",
    "jp": "ja-JP",
    "kr": "ko-KR",
}

TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]+|\s+", re.UNICODE)
CONFIG_PATH = Path(__file__).with_name("ocr_eval_config.toml")


def get_language_tag(file_path: Path, override_lang: str = None) -> str:
    if override_lang:
        return override_lang
    parent_name = file_path.parent.name.lower()
    return LANG_MAP.get(parent_name, parent_name if "-" in parent_name and len(parent_name) <= 7 else "")


def run_baseline_ocr(pixmap: QtGui.QPixmap, lang: str) -> str:
    """Run OCR directly on raw image."""
    if pixmap.isNull():
        return ""
    image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    result = recognize_qimage(image, language_tag=lang)
    return compose_text_from_result(result, language_tag=lang)


def run_pipeline_ocr(
    pixmap: QtGui.QPixmap,
    lang: str,
    debug_img_path: Path,
    settings: OcrPreprocessSettings,
):
    preprocess_result = run_preprocess_pipeline(pixmap, settings=settings)
    preprocess_result.image.save(str(debug_img_path), "PNG")
    recognition = recognize_qimage(preprocess_result.image, language_tag=lang)
    pipeline_text = compose_text_from_result(recognition, language_tag=lang)

    return {
        "processed_text": pipeline_text,
        "processed_scale": preprocess_result.settings.scale_factor,
        "pipeline_summary": preprocess_result.summary(),
        "pipeline_steps": preprocess_result.applied_steps,
        "settings": preprocess_result.settings,
    }


def load_eval_preprocess_settings(config_path: Path = CONFIG_PATH) -> OcrPreprocessSettings:
    with open(config_path, "rb") as file_obj:
        data = tomllib.load(file_obj)

    preprocess_data = data.get("preprocess")
    if not isinstance(preprocess_data, dict):
        raise ValueError("Missing [preprocess] section in OCR evaluator config.")

    try:
        return OcrPreprocessSettings(
            scale_factor=float(preprocess_data.get("scale_factor", 1.0)),
            normalize_source=bool(preprocess_data.get("normalize_source", True)),
            add_padding=bool(preprocess_data.get("add_padding", True)),
            padding_px=int(preprocess_data.get("padding_px", 32)),
            bolden_text=bool(preprocess_data.get("bolden_text", True)),
            auto_invert=bool(preprocess_data.get("auto_invert", True)),
            high_contrast=bool(preprocess_data.get("high_contrast", True)),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid OCR evaluator config in {config_path}: {exc}") from exc


def tokenize_for_diff(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text or "")


def build_pairwise_diff_masks(left_text: str, right_text: str) -> tuple[list[bool], list[bool]]:
    left_tokens = tokenize_for_diff(left_text)
    right_tokens = tokenize_for_diff(right_text)
    left_mask = [False] * len(left_tokens)
    right_mask = [False] * len(right_tokens)

    matcher = SequenceMatcher(a=left_tokens, b=right_tokens, autojunk=False)
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        for index in range(left_start, left_end):
            if not left_tokens[index].isspace():
                left_mask[index] = True
        for index in range(right_start, right_end):
            if not right_tokens[index].isspace():
                right_mask[index] = True

    return left_mask, right_mask


def build_char_diff_html(token: str, other_token: str) -> str:
    matcher = SequenceMatcher(a=token, b=other_token, autojunk=False)
    rendered: list[str] = []
    for tag, start, end, _, _ in matcher.get_opcodes():
        piece = html.escape(token[start:end])
        if not piece:
            continue
        if tag == "equal":
            rendered.append(piece)
        else:
            rendered.append(f'<span class="diff-char">{piece}</span>')
    return "".join(rendered)


def build_pairwise_token_html(text: str, other_text: str) -> list[str]:
    tokens = tokenize_for_diff(text)
    other_tokens = tokenize_for_diff(other_text)
    rendered = [html.escape(token) for token in tokens]

    matcher = SequenceMatcher(a=tokens, b=other_tokens, autojunk=False)
    for tag, start, end, other_start, other_end in matcher.get_opcodes():
        if tag == "equal":
            continue

        this_span = tokens[start:end]
        other_span = other_tokens[other_start:other_end]

        if (
            tag == "replace"
            and len(this_span) == 1
            and len(other_span) == 1
            and not this_span[0].isspace()
            and not other_span[0].isspace()
        ):
            rendered[start] = build_char_diff_html(this_span[0], other_span[0])
            continue

        for index in range(start, end):
            if tokens[index].isspace():
                continue
            rendered[index] = f'<span class="diff-token">{html.escape(tokens[index])}</span>'

    return rendered


def combine_token_html(text: str, html_groups: list[list[str]], diff_mask: list[bool]) -> str:
    tokens = tokenize_for_diff(text)
    if not tokens:
        return html.escape(text or "[No text]")

    combined = [html.escape(token) for token in tokens]
    for index, token in enumerate(tokens):
        if token.isspace():
            continue

        chosen_html = combined[index]
        for group in html_groups:
            candidate = group[index]
            if candidate != html.escape(token):
                chosen_html = candidate
                break

        if chosen_html == html.escape(token) and diff_mask[index]:
            chosen_html = f'<span class="diff-token">{html.escape(token)}</span>'

        combined[index] = chosen_html

    return "".join(combined)


def format_pipeline_steps(steps) -> str:
    if not steps:
        return "<span class='muted-note'>No preprocessing steps enabled.</span>"
    badges = []
    for step in steps:
        details = f" <span class='step-detail'>{html.escape(step.details)}</span>" if step.details else ""
        badges.append(f"<span class='step-chip'>{html.escape(step.label)}{details}</span>")
    return "".join(badges)


def generate_html_report(results, output_path):
    html_content = [
        "<html><head><title>HushSnap OCR Pipeline Report</title>",
        "<style>",
        "body { font-family: 'Segoe UI', system-ui, sans-serif; margin: 30px; background: #f8f9fa; color: #212529; }",
        ".case { background: white; border-radius: 12px; margin-bottom: 50px; padding: 30px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border: 1px solid #e9ecef; }",
        ".filename { font-size: 1.5em; font-weight: 700; margin-bottom: 20px; color: #1a73e8; border-bottom: 2px solid #e8f0fe; padding-bottom: 10px; display: flex; align-items: center; justify-content: space-between; gap: 20px; }",
        ".meta { font-size: 0.85em; background: #f1f3f4; padding: 5px 12px; border-radius: 20px; color: #5f6368; font-weight: normal; }",
        ".image-layout { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 30px; margin-bottom: 24px; align-items: start; }",
        ".img-panel, .pipeline-panel { background: #fff; border: 1px solid #dee2e6; border-radius: 12px; padding: 20px; }",
        ".img-container { text-align: center; }",
        ".img-label { font-weight: 600; margin-bottom: 10px; color: #444; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.5px; }",
        ".img-wrapper { background: #111; padding: 10px; border-radius: 8px; display: inline-block; max-width: 100%; box-shadow: inset 0 2px 4px rgba(0,0,0,0.2); }",
        ".img-wrapper img { max-height: 520px; max-width: 100%; display: block; }",
        ".pipeline-summary { font-size: 0.95em; line-height: 1.6; color: #495057; }",
        ".pipeline-summary code { font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 0.92em; }",
        ".step-chip { display: inline-block; margin: 6px 8px 0 0; padding: 6px 10px; border-radius: 999px; background: #eef2ff; color: #364fc7; font-size: 0.85em; }",
        ".step-detail { color: #5c7cfa; margin-left: 4px; }",
        ".text-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 30px; }",
        ".text-box { background: #fff; border: 1px solid #dee2e6; border-radius: 8px; overflow: hidden; }",
        ".text-header { background: #f8f9fa; border-bottom: 1px solid #dee2e6; padding: 10px 15px; font-weight: 600; font-size: 0.9em; display: flex; justify-content: space-between; }",
        ".text-content { padding: 15px; font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 13px; white-space: pre-wrap; line-height: 1.6; min-height: 120px; color: #3c4043; }",
        ".text-content .diff-token { background: #fff3bf; color: #7a4f01; border-radius: 4px; box-shadow: 0 0 0 1px rgba(245, 158, 11, 0.25); }",
        ".text-content .diff-char { background: #ffd8a8; color: #8f3f00; border-radius: 3px; box-shadow: 0 0 0 1px rgba(217, 119, 6, 0.28); }",
        ".muted-note { color: #868e96; font-style: italic; }",
        ".diff-legend { margin: 18px 0 22px; font-size: 0.9em; color: #5f6368; }",
        ".diff-legend .diff-token, .diff-legend .diff-char { padding: 1px 4px; }",
        "h1 { color: #202124; margin-bottom: 10px; }",
        ".summary { margin-bottom: 40px; color: #70757a; border-left: 4px solid #1a73e8; padding-left: 15px; }",
        "</style></head><body>",
        "<h1>HushSnap OCR Pipeline Evaluation</h1>",
        f"<div class='summary'>Analyzing <b>{len(results)}</b> cases with a single OCR pass driven by the current preprocessing pipeline.</div>",
        "<div class='diff-legend'>Light highlight marks changed tokens. Darker highlight marks changed characters inside a token.</div>",
    ]

    for item in results:
        baseline_mask, pipeline_mask = build_pairwise_diff_masks(item["baseline"], item["processed_text"])
        baseline_html = combine_token_html(
            item["baseline"] or "[No text]",
            [build_pairwise_token_html(item["baseline"] or "[No text]", item["processed_text"] or "[No text]")],
            baseline_mask,
        )
        pipeline_html = combine_token_html(
            item["processed_text"] or "[No text]",
            [build_pairwise_token_html(item["processed_text"] or "[No text]", item["baseline"] or "[No text]")],
            pipeline_mask,
        )

        html_content.append("<div class='case'>")
        html_content.append("  <div class='filename'>")
        html_content.append(f"    <span>{html.escape(item['name'])}</span>")
        html_content.append(
            f"    <span class='meta'>Engine: {item['lang'] or 'Auto'} | Pipeline scale: {item['processed_scale']:.2f}</span>"
        )
        html_content.append("  </div>")

        html_content.append("  <div class='image-layout'>")
        html_content.append("    <div class='img-panel img-container'>")
        html_content.append("      <div class='img-label'>Source Capture</div>")
        html_content.append(
            f"      <a class='img-wrapper' href='{item['src_rel']}' target='_blank' rel='noopener noreferrer'><img src='{item['src_rel']}'></a>"
        )
        html_content.append("    </div>")
        html_content.append("    <div class='img-panel img-container'>")
        html_content.append("      <div class='img-label'>Pipeline Output</div>")
        html_content.append(
            f"      <a class='img-wrapper' href='{item['processed_rel']}' target='_blank' rel='noopener noreferrer'><img src='{item['processed_rel']}'></a>"
        )
        html_content.append("    </div>")
        html_content.append("  </div>")

        html_content.append("  <div class='pipeline-panel'>")
        html_content.append("    <div class='img-label'>Pipeline Steps</div>")
        html_content.append(
            f"    <div class='pipeline-summary'><code>{html.escape(item['pipeline_summary'] or 'No preprocessing')}</code></div>"
        )
        html_content.append(f"    <div>{format_pipeline_steps(item['pipeline_steps'])}</div>")
        html_content.append("  </div>")

        html_content.append("  <div class='text-grid'>")
        html_content.append("    <div class='text-box'>")
        html_content.append("      <div class='text-header'>Baseline (Raw Image)</div>")
        html_content.append(f"      <div class='text-content'>{baseline_html}</div>")
        html_content.append("    </div>")
        html_content.append("    <div class='text-box'>")
        html_content.append("      <div class='text-header'>Pipeline OCR</div>")
        html_content.append(f"      <div class='text-content'>{pipeline_html}</div>")
        html_content.append("    </div>")
        html_content.append("  </div>")
        html_content.append("</div>")

    html_content.append("</body></html>")
    with open(output_path, "w", encoding="utf-8") as file_obj:
        file_obj.write("\n".join(html_content))


def main():
    parser = argparse.ArgumentParser(description="Pipeline evaluation for HushSnap OCR")
    parser.add_argument("--input", default="ocr_eval_data", help="Input image directory")
    parser.add_argument("--output", default="tools/ocr_report.html", help="HTML report output path")
    args = parser.parse_args()

    input_path = PROJECT_ROOT / args.input
    output_report = Path(args.output).absolute()
    debug_dir = output_report.parent / "ocr_debug"

    if debug_dir.exists():
        shutil.rmtree(debug_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)

    image_extensions = {".png", ".jpg", ".jpeg", ".bmp"}
    files = [file_path for file_path in input_path.rglob("*") if file_path.suffix.lower() in image_extensions]

    if not files:
        print(f"No images found in {input_path}")
        return

    settings = load_eval_preprocess_settings()
    print(f"\n>>> Starting Pipeline Evaluation ({len(files)} images)...")
    results = []
    for index, img_file in enumerate(files, 1):
        lang = get_language_tag(img_file)
        print(f"[{index}/{len(files)}] Processing: {img_file.name}...", end=" ", flush=True)

        pixmap = QtGui.QPixmap(str(img_file))
        if pixmap.isNull():
            continue

        baseline_text = run_baseline_ocr(pixmap, lang)

        processed_img_name = f"processed_{img_file.stem}_{index}.png"
        processed_img_path = debug_dir / processed_img_name
        pipeline_data = run_pipeline_ocr(pixmap, lang, processed_img_path, settings=settings)

        report_dir = output_report.parent
        src_rel = os.path.relpath(img_file, report_dir)
        processed_rel = os.path.relpath(processed_img_path, report_dir)

        results.append(
            {
                "name": img_file.name,
                "src_rel": src_rel,
                "processed_rel": processed_rel,
                "baseline": baseline_text.strip(),
                "processed_text": pipeline_data["processed_text"].strip(),
                "processed_scale": pipeline_data["processed_scale"],
                "pipeline_summary": pipeline_data["pipeline_summary"],
                "pipeline_steps": pipeline_data["pipeline_steps"],
                "lang": lang,
            }
        )
        print("Done")

    generate_html_report(results, args.output)

    report_path = Path(args.output).absolute()
    report_uri = report_path.as_uri()

    print("\n[Success] Pipeline report generated!")
    print(f"Report: {report_uri}")


if __name__ == "__main__":
    main()
