"""
HushSnap OCR Evaluator
--------------------------------
A diagnostic tool to evaluate the quality of OCR preprocessing logic. 
It compares "Raw Image" vs "HushSnap Preprocessed Image" and their OCR results.

Usage:
    1. Create an `ocr_eval_data` folder in the project root.
    2. Add images to `ocr_eval_data/` (or subfolders like `sc/`, `en/`).
    3. Run: python tools/ocr_evaluator.py

The report will show:
    - Original Image vs Preprocessed Image.
    - Adaptive Scale Factor used.
    - Baseline OCR Text vs HushSnap OCR Text.
"""

import os
import sys
import argparse
from pathlib import Path
import html
import logging
import shutil

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.append(str(PROJECT_ROOT))

from PyQt6 import QtGui, QtWidgets
from hushsnap.ocr.recognition import (
    recognize_qimage, 
    recommend_scale_factor, 
    INITIAL_SCALE_FACTOR, 
    MIN_RESCALE_DELTA
)
from hushsnap.ocr.preprocess import preprocess_for_ocr
from hushsnap.ocr.text import compose_text_from_result
from hushsnap.ocr.parsing import parse_ocr_payload

# Initialize QApplication
app = QtWidgets.QApplication(sys.argv)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("ocr_evaluator")

LANG_MAP = {
    "sc": "zh-Hans", "chs": "zh-Hans", "cn": "zh-Hans",
    "tc": "zh-Hant", "cht": "zh-Hant", "tw": "zh-Hant", "hk": "zh-Hant",
    "en": "en-US", "jp": "ja-JP", "kr": "ko-KR",
}

def get_language_tag(file_path: Path, override_lang: str = None) -> str:
    if override_lang: return override_lang
    parent_name = file_path.parent.name.lower()
    return LANG_MAP.get(parent_name, parent_name if "-" in parent_name and len(parent_name) <= 7 else "")

def run_baseline_ocr(pixmap: QtGui.QPixmap, lang: str):
    """Run OCR directly on raw image."""
    if pixmap.isNull(): return ""
    image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
    result = recognize_qimage(image, language_tag=lang)
    return compose_text_from_result(result, language_tag=lang)

def evaluate_hushsnap(
    pixmap: QtGui.QPixmap,
    lang: str,
    initial_debug_img_path: Path,
    rescaled_debug_img_path: Path,
):
    """
    Run full HushSnap logic and save the first/second pass preprocessed images.
    Returns diagnostic data without changing the current selection logic.
    """
    # 1. Initial Pass
    initial_img = preprocess_for_ocr(pixmap, INITIAL_SCALE_FACTOR)
    initial_result = recognize_qimage(initial_img, language_tag=lang)
    initial_img.save(str(initial_debug_img_path), "PNG")
    initial_text = compose_text_from_result(initial_result, language_tag=lang)
    
    final_result = initial_result
    scale_used = INITIAL_SCALE_FACTOR
    was_rescaled = False
    rescaled_text = ""
    rescaled_scale = 0.0
    rescaled_debug_available = False
    
    # 2. Adaptive Logic
    recommended_scale = recommend_scale_factor(initial_result, initial_img.width(), initial_img.height())
    if abs(recommended_scale - INITIAL_SCALE_FACTOR) >= MIN_RESCALE_DELTA:
        rescaled_img = preprocess_for_ocr(pixmap, recommended_scale)
        rescaled_result = recognize_qimage(rescaled_img, language_tag=lang)
        rescaled_img.save(str(rescaled_debug_img_path), "PNG")
        rescaled_text = compose_text_from_result(rescaled_result, language_tag=lang)
        rescaled_scale = recommended_scale
        rescaled_debug_available = True
        if rescaled_result.text or rescaled_result.lines:
            final_result = rescaled_result
            scale_used = recommended_scale
            was_rescaled = True
    
    return {
        "pass1_text": initial_text,
        "pass1_scale": INITIAL_SCALE_FACTOR,
        "pass2_text": rescaled_text,
        "pass2_scale": rescaled_scale,
        "pass2_ran": rescaled_debug_available,
        "chosen_pass": "pass2" if was_rescaled else "pass1",
        "chosen_scale": scale_used,
        "was_rescaled": was_rescaled,
    }

def generate_html_report(results, output_path):
    html_content = [
        "<html><head><title>HushSnap OCR Depth Report</title>",
        "<style>",
        "body { font-family: 'Segoe UI', system-ui, sans-serif; margin: 30px; background: #f8f9fa; color: #212529; }",
        ".case { background: white; border-radius: 12px; margin-bottom: 50px; padding: 30px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border: 1px solid #e9ecef; }",
        ".filename { font-size: 1.5em; font-weight: 700; margin-bottom: 20px; color: #1a73e8; border-bottom: 2px solid #e8f0fe; padding-bottom: 10px; display: flex; align-items: center; justify-content: space-between; }",
        ".meta { font-size: 0.85em; background: #f1f3f4; padding: 5px 12px; border-radius: 20px; color: #5f6368; font-weight: normal; }",
        ".image-layout { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr); gap: 30px; margin-bottom: 25px; align-items: start; }",
        ".img-container { text-align: center; }",
        ".img-panel { background: #fff; border: 1px solid #dee2e6; border-radius: 12px; padding: 20px; }",
        ".img-stack { display: grid; gap: 20px; }",
        ".img-label { font-weight: 600; margin-bottom: 10px; color: #444; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.5px; }",
        ".img-wrapper { background: #111; padding: 10px; border-radius: 8px; display: inline-block; max-width: 100%; box-shadow: inset 0 2px 4px rgba(0,0,0,0.2); }",
        ".img-wrapper img { max-height: 520px; max-width: 100%; display: block; }",
        ".text-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 30px; }",
        ".text-box { background: #fff; border: 1px solid #dee2e6; border-radius: 8px; overflow: hidden; }",
        ".text-header { background: #f8f9fa; border-bottom: 1px solid #dee2e6; padding: 10px 15px; font-weight: 600; font-size: 0.9em; display: flex; justify-content: space-between; }",
        ".text-content { padding: 15px; font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 13px; white-space: pre-wrap; line-height: 1.6; min-height: 120px; color: #3c4043; }",
        ".rescaled-badge { background: #fff7e6; color: #d46b08; border: 1px solid #ffd591; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; }",
        ".muted-note { color: #868e96; font-style: italic; }",
        "h1 { color: #202124; margin-bottom: 10px; }",
        ".summary { margin-bottom: 40px; color: #70757a; border-left: 4px solid #1a73e8; padding-left: 15px; }",
        "</style></head><body>",
        "<h1>HushSnap OCR Depth Evaluation</h1>",
        f"<div class='summary'>Analyzing <b>{len(results)}</b> cases. The layout keeps source images large while grouping both preprocessing passes together for direct visual inspection.</div>"
    ]

    for item in results:
        rescaled_html = '<span class="rescaled-badge">Adaptive Rescale Active</span>' if item["was_rescaled"] else ""
        html_content.append(f'<div class="case">')
        html_content.append(f'  <div class="filename">')
        html_content.append(f'    <span>{html.escape(item["name"])}</span>')
        html_content.append(
            f'    <span class="meta">Engine: {item["lang"] or "Auto"} | '
            f'Chosen: {item["chosen_pass"]} @ {item["chosen_scale"]:.2f} {rescaled_html}</span>'
        )
        html_content.append(f'  </div>')
        
        # Images Comparison
        html_content.append(f'  <div class="image-layout">')
        html_content.append(f'    <div class="img-panel img-container">')
        html_content.append(f'      <div class="img-label">Source Capture</div>')
        html_content.append(
            f'      <a class="img-wrapper" href="{item["src_rel"]}" target="_blank" rel="noopener noreferrer">'
            f'<img src="{item["src_rel"]}"></a>'
        )
        html_content.append(f'    </div>')
        html_content.append(f'    <div class="img-panel">')
        html_content.append(f'      <div class="img-label">Preprocessing Review</div>')
        html_content.append(f'      <div class="img-stack">')
        html_content.append(f'        <div class="img-container">')
        html_content.append(f'          <div class="img-label">Pass 1 Preprocessed</div>')
        html_content.append(
            f'          <a class="img-wrapper" href="{item["pass1_rel"]}" target="_blank" rel="noopener noreferrer">'
            f'<img src="{item["pass1_rel"]}"></a>'
        )
        html_content.append(f'        </div>')
        html_content.append(f'        <div class="img-container">')
        html_content.append(f'          <div class="img-label">Pass 2 Preprocessed</div>')
        if item["pass2_rel"]:
            html_content.append(
                f'          <a class="img-wrapper" href="{item["pass2_rel"]}" target="_blank" rel="noopener noreferrer">'
                f'<img src="{item["pass2_rel"]}"></a>'
            )
        else:
            html_content.append(f'          <div class="text-content muted-note">No second pass was run.</div>')
        html_content.append(f'        </div>')
        html_content.append(f'      </div>')
        html_content.append(f'    </div>')
        html_content.append(f'  </div>')

        # Text Comparison
        html_content.append(f'  <div class="text-grid">')
        html_content.append(f'    <div class="text-box">')
        html_content.append(f'      <div class="text-header">Baseline (Raw Image)</div>')
        html_content.append(f'      <div class="text-content">{html.escape(item["baseline"] or "[No text]")}</div>')
        html_content.append(f'    </div>')
        html_content.append(f'    <div class="text-box">')
        html_content.append(f'      <div class="text-header">Pass 1 OCR <span>Scale: {item["pass1_scale"]:.2f}</span></div>')
        html_content.append(f'      <div class="text-content">{html.escape(item["pass1_text"] or "[No text]")}</div>')
        html_content.append(f'    </div>')
        html_content.append(f'    <div class="text-box">')
        if item["pass2_ran"]:
            html_content.append(f'      <div class="text-header">Pass 2 OCR <span>Scale: {item["pass2_scale"]:.2f}</span></div>')
            html_content.append(f'      <div class="text-content">{html.escape(item["pass2_text"] or "[No text]")}</div>')
        else:
            html_content.append(f'      <div class="text-header">Pass 2 OCR</div>')
            html_content.append(f'      <div class="text-content muted-note">No second pass was run.</div>')
        html_content.append(f'    </div>')
        html_content.append(f'  </div>')
        
        html_content.append(f'</div>')

    html_content.append("</body></html>")
    with open(output_path, "w", encoding="utf-8") as f: f.write("\n".join(html_content))

def main():
    parser = argparse.ArgumentParser(description="Depth evaluation for HushSnap OCR")
    parser.add_argument("--input", default="ocr_eval_data", help="Input image directory")
    parser.add_argument("--output", default="ocr_report.html", help="HTML report output path")
    args = parser.parse_args()

    input_path = PROJECT_ROOT / args.input
    output_report = Path(args.output).absolute()
    debug_dir = output_report.parent / "ocr_debug"
    
    if debug_dir.exists(): shutil.rmtree(debug_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)

    image_extensions = {".png", ".jpg", ".jpeg", ".bmp"}
    files = [f for f in input_path.rglob("*") if f.suffix.lower() in image_extensions]
    
    if not files:
        print(f"No images found in {input_path}")
        return

    print(f"\n>>> Starting Depth Evaluation ({len(files)} images)...")
    results = []
    for i, img_file in enumerate(files, 1):
        lang = get_language_tag(img_file)
        print(f"[{i}/{len(files)}] Processing: {img_file.name}...", end=" ", flush=True)
        
        pixmap = QtGui.QPixmap(str(img_file))
        if pixmap.isNull(): continue
            
        baseline_text = run_baseline_ocr(pixmap, lang)
        
        pass1_img_name = f"pass1_{img_file.stem}_{i}.png"
        pass2_img_name = f"pass2_{img_file.stem}_{i}.png"
        pass1_img_path = debug_dir / pass1_img_name
        pass2_img_path = debug_dir / pass2_img_name
        
        eval_data = evaluate_hushsnap(pixmap, lang, pass1_img_path, pass2_img_path)
        
        # Relative paths for HTML
        report_dir = output_report.parent
        src_rel = os.path.relpath(img_file, report_dir)
        pass1_rel = os.path.relpath(pass1_img_path, report_dir)
        pass2_rel = os.path.relpath(pass2_img_path, report_dir) if eval_data["pass2_ran"] else ""

        results.append({
            "name": img_file.name,
            "src_rel": src_rel,
            "pass1_rel": pass1_rel,
            "pass2_rel": pass2_rel,
            "baseline": baseline_text.strip(),
            "pass1_text": eval_data["pass1_text"].strip(),
            "pass1_scale": eval_data["pass1_scale"],
            "pass2_text": eval_data["pass2_text"].strip(),
            "pass2_scale": eval_data["pass2_scale"],
            "pass2_ran": eval_data["pass2_ran"],
            "chosen_pass": eval_data["chosen_pass"],
            "chosen_scale": eval_data["chosen_scale"],
            "lang": lang,
            "was_rescaled": eval_data["was_rescaled"],
        })
        print(f"Done (Chosen scale: {eval_data['chosen_scale']:.2f})")

    generate_html_report(results, args.output)
    
    report_path = Path(args.output).absolute()
    # Create a clickable file URI for most modern terminals
    report_uri = report_path.as_uri()
    
    print(f"\n[Success] Deep report generated!")
    print(f"Report: {report_uri}")
    print(f"Debug images folder: {debug_dir.absolute().as_uri()}")

if __name__ == "__main__":
    main()
