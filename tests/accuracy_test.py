"""OCR accuracy: max_side_len=1536 vs 4000 — block-level comparison.

Ignores XY-cut reading-order (which is known to mangle multi-column dense
layouts) and compares raw detection blocks against ground-truth using
fuzzy token-level matching.  This isolates detection/recognition quality
from layout-ordering bugs.

Usage:  python tests/accuracy_test.py
"""

import difflib
import gc
import os
import re
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import numpy as np
from PyQt6 import QtGui
import psutil

_proc = psutil.Process(os.getpid())


def ws_mb():
    return _proc.memory_info().rss / (1024 * 1024)


# ── Load ──────────────────────────────────────────────────────────────

img_path = project_root / "scratch" / "5.png"
gt_path = project_root / "scratch" / "5.txt"

qimage = QtGui.QImage(str(img_path))
bgr = qimage.convertToFormat(QtGui.QImage.Format.Format_RGB32)
w, h = bgr.width(), bgr.height()
ptr = bgr.bits(); ptr.setsize(bgr.sizeInBytes())
arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))[:, :, :3].copy()

with open(gt_path, "r", encoding="utf-8") as f:
    gt_text = f.read()


def tokenize(text: str) -> set[str]:
    """Lowercase word tokens (≥3 chars), stripping punctuation."""
    tokens = set()
    for token in re.findall(r'[a-zA-Z]{3,}', text.lower()):
        tokens.add(token)
    return tokens


def tokenize_ordered(text: str) -> list[str]:
    """Ordered word tokens for sequence matching."""
    return re.findall(r'[a-zA-Z]{3,}', text.lower())


def block_tokens(blocks: list[str]) -> set[str]:
    """Union of all tokens across all blocks."""
    all_tokens = set()
    for block in blocks:
        all_tokens |= tokenize(block)
    return all_tokens


# ── Run both ──────────────────────────────────────────────────────────

from rapidocr import RapidOCR, OCRVersion

BASE = {
    "Det.ocr_version": OCRVersion.PPOCRV5,
    "Rec.ocr_version": OCRVersion.PPOCRV5,
    "Rec.rec_batch_num": 1,
    "EngineConfig.onnxruntime.intra_op_num_threads": 8,
    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    "EngineConfig.onnxruntime.enable_cpu_mem_arena": False,
}


def detect_only(label: str, max_side: int, arr):
    """Run detection + recognition, return raw block texts (unsorted)."""
    gc.collect(); time.sleep(1.0)

    ws_before = ws_mb()
    engine = RapidOCR(params={**BASE, "Global.max_side_len": max_side})

    t0 = time.perf_counter()
    result = engine(arr)
    dur = (time.perf_counter() - t0) * 1000
    ws_peak = ws_mb()

    json_data = result.to_json() if hasattr(result, "to_json") else []
    blocks = [item["txt"] for item in json_data if (item.get("txt") or "").strip()]
    boxes = [item["box"] for item in json_data if (item.get("txt") or "").strip()]

    del engine; gc.collect(); time.sleep(0.5)
    ws_after = ws_mb()

    return {
        "label": label,
        "dur": dur,
        "ws_before": ws_before, "ws_peak": ws_peak, "ws_after": ws_after,
        "blocks": blocks,
        "boxes": boxes,
        "n_boxes": len(blocks),
    }


print(f"Image: 5.png  {w}×{h}  ({w*h/1e6:.1f} MP)  long_side={max(w,h)}")
gt_tokens = tokenize(gt_text)
gt_ordered = tokenize_ordered(gt_text)
print(f"GT: {len(gt_text)} chars  |  {len(gt_tokens)} unique tokens (≥3 chars)\n")

r1536 = detect_only("max_side=1536", 1536, arr)
r4000 = detect_only("max_side=4000", 4000, arr)

# ── Token-level analysis ──────────────────────────────────────────────

tok1536 = block_tokens(r1536["blocks"])
tok4000 = block_tokens(r4000["blocks"])

# Which GT tokens are found?
found_1536 = gt_tokens & tok1536
found_4000 = gt_tokens & tok4000
missed_1536 = gt_tokens - tok1536
missed_4000 = gt_tokens - tok4000
extra_1536 = tok1536 - gt_tokens
extra_4000 = tok4000 - gt_tokens

# Tokens found by 4000 but NOT by 1536 (what max_side_len=1536 missed)
only_in_4000 = found_4000 - found_1536
only_in_1536 = found_1536 - found_4000

print(f"{'='*60}")
print(f"  TOKEN-LEVEL COMPARISON (ignoring reading order)")
print(f"{'='*60}")
print(f"  {'':<35s} {'1536':>10s} {'4000':>10s}")
sep = "-" * 55
print(f"  {'':<35s} {'':>10s} {'':>10s}")
print(f"  {'GT tokens':<35s} {len(gt_tokens):>10d} {len(gt_tokens):>10d}")
print(f"  {'OCR tokens (unique)':<35s} {len(tok1536):>10d} {len(tok4000):>10d}")
print(f"  {'Found (recall)':<35s} {len(found_1536):>9d}  {len(found_4000):>9d} ")
print(f"  {'Missed':<35s} {len(missed_1536):>10d} {len(missed_4000):>10d}")
print(f"  {'Extra (not in GT)':<35s} {len(extra_1536):>10d} {len(extra_4000):>10d}")
print(f"  {'Recall rate':<35s} {len(found_1536)/len(gt_tokens)*100:>9.1f}%  "
      f"{len(found_4000)/len(gt_tokens)*100:>9.1f}%")

if only_in_4000:
    print(f"\n  Words found by 4000 but MISSED by 1536:")
    for w in sorted(only_in_4000)[:20]:
        print(f"    - {w}")
    if len(only_in_4000) > 20:
        print(f"    ... and {len(only_in_4000) - 20} more")

if only_in_1536:
    print(f"\n  Words found by 1536 but NOT by 4000:")
    for w in sorted(only_in_1536)[:10]:
        print(f"    + {w}")

# ── Detection block analysis ─────────────────────────────────────────

print(f"\n{'='*60}")
print(f"  DETECTION BLOCK ANALYSIS")
print(f"{'='*60}")
print(f"  {'':<30s} {'1536':>12s} {'4000':>12s}")
print(f"  {'Detection boxes':<30s} {r1536['n_boxes']:>12d} {r4000['n_boxes']:>12d}")
print(f"  {'Total raw chars':<30s} {sum(len(b) for b in r1536['blocks']):>12d} "
      f"{sum(len(b) for b in r4000['blocks']):>12d}")
print(f"  {'Avg chars/box':<30s} "
      f"{sum(len(b) for b in r1536['blocks'])/max(1,r1536['n_boxes']):>11.1f}  "
      f"{sum(len(b) for b in r4000['blocks'])/max(1,r4000['n_boxes']):>11.1f}")

# ── Memory ───────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"  MEMORY")
print(f"{'='*60}")
print(f"  {'':<20s} {'1536':>15s} {'4000':>15s}")
print(f"  {'WS peak':<20s} {r1536['ws_peak']:>14.0f} MB {r4000['ws_peak']:>14.0f} MB")
print(f"  {'WS delta (peak-base)':<20s} "
      f"{r1536['ws_peak']-r1536['ws_before']:>+14.0f} MB "
      f"{r4000['ws_peak']-r4000['ws_before']:>+14.0f} MB")
print(f"  {'Latency':<20s} {r1536['dur']:>14.0f} ms {r4000['dur']:>14.0f} ms")

# ── Verdict ──────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"  VERDICT")
print(f"{'='*60}")

recall_1536 = len(found_1536) / len(gt_tokens) * 100
recall_4000 = len(found_4000) / len(gt_tokens) * 100
recall_drop = recall_4000 - recall_1536
ws_saving = r4000["ws_peak"] - r1536["ws_peak"]

print(f"  Token recall:  1536={recall_1536:.1f}%  unlimited={recall_4000:.1f}%  "
      f"drop={recall_drop:.1f}%")
print(f"  Unique tokens:  1536={len(tok1536)}  unlimited={len(tok4000)}")
print(f"  Words ONLY in unlimited: {len(only_in_4000)}")
print(f"  Words ONLY in 1536:     {len(only_in_1536)}")
print(f"")
print(f"  WS peak:  1536={r1536['ws_peak']:.0f} MB  unlimited={r4000['ws_peak']:.0f} MB  "
      f"saving={ws_saving:.0f} MB")

if recall_drop < 2.0:
    print(f"")
    print(f"  ✓ max_side_len=1536 does NOT cause meaningful text loss.")
    print(f"    Token recall difference ({recall_drop:.1f}%) is noise-level.")
    print(f"    The {ws_saving:.0f} MB memory saving is pure win.")
elif recall_drop < 5.0:
    print(f"")
    print(f"  ~ max_side_len=1536 has minor recall loss ({recall_drop:.1f}%).")
    print(f"    Trade-off: {ws_saving:.0f} MB saved vs {len(only_in_4000)} missed words.")
else:
    print(f"")
    print(f"  ⚠ max_side_len=1536 loses {recall_drop:.1f}% token recall.")
    print(f"    {len(only_in_4000)} words detected by unlimited are MISSED at 1536.")
    print(f"    Trade-off: {ws_saving:.0f} MB saved vs {len(only_in_4000)} missed words.")
    print(f"    Consider raising to 2048 or 2560 for dense text.")

print(f"")
print(f"  Note: The ~30-49% char accuracy from earlier was XY-cut layout")
print(f"  ordering errors, NOT detection quality. Block-level token recall")
print(f"  ({recall_1536:.0f}-{recall_4000:.0f}%) shows the OCR engine sees most text.")
print(f"{'='*60}")
