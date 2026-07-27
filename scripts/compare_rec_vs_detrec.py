"""Compare det+rec pipeline vs rec-only fallback on a multi-line screenshot.

Runs test.png through both paths and reports text output + timing so the
speed / quality tradeoff is visible.  rec-only (the _recognize_without_detection
fallback) squashes the whole image to a 48px-tall feature map, so on a
multi-line capture it is fast but illegible - this script makes that concrete.

Usage:
    python scripts/compare_rec_vs_detrec.py [path/to/image.png]
    (default: <repo>/test.png)
"""

import gc
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from PIL import Image
from PyQt6 import QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

import numpy as np

from hushsnap.ocr.preprocess import run_minimal_pipeline
from hushsnap.ocr.ppocr import (
    _get_engine,
    _recognize_without_detection,
    recognize_ppocr_qimage,
)

IMG = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _project_root / "test.png"
N_WARM = 2
N_RUNS = 7


def stats(xs: list[float]) -> str:
    return (f"min={min(xs):6.1f}  median={statistics.median(xs):6.1f}  "
            f"mean={statistics.mean(xs):6.1f}  max={max(xs):6.1f}")


def time_fn(fn, n=N_RUNS) -> list[float]:
    xs = []
    for _ in range(n):
        gc.collect()  # start each iteration from a clean native-buffer state
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return xs


# ── Load + preprocess (production-identical: RGB32, DPR 1.0, no resize) ──────
pil = Image.open(IMG)
prep = run_minimal_pipeline(pil)
qimg = prep.image
print(f"image: {IMG.name}  {qimg.width()}x{qimg.height()}  "
      f"(original {prep.original_size.width()}x{prep.original_size.height()})")

# Build the BGR numpy array once - same buffer math as recognize_ppocr_qimage.
w, h = qimg.width(), qimg.height()
ptr = qimg.bits()
ptr.setsize(qimg.sizeInBytes())
arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))[:, :, :3].copy()

engine = _get_engine()
print("engine loaded.\n")

# ── COLD first det+rec call: commits ORT mid-tensors for this input size ────
# (see memory first-inference-not-weight-pages / inference-buffer-never-reclaimed)
t0 = time.perf_counter()
cold = recognize_ppocr_qimage(prep)
cold_ms = (time.perf_counter() - t0) * 1000
print(f"COLD det+rec (1st inference, commits mid-tensors): {cold_ms:.1f} ms\n")

# ── Warm up both paths so timing reflects steady state ──────────────────────
for _ in range(N_WARM):
    recognize_ppocr_qimage(prep)
for _ in range(N_WARM):
    _recognize_without_detection(engine, arr)

# ── Text outputs (one representative run each) ───────────────────────────────
detrec = recognize_ppocr_qimage(prep)
reconly = _recognize_without_detection(engine, arr)

print("=" * 64)
print("TEXT OUTPUT")
print("=" * 64)
print("\n--- det+rec pipeline ---")
print(detrec.text)
print(f"[chars={len(detrec.text)}  lines={len(detrec.lines)}]")
print("\n--- rec-only fallback (use_det=False) ---")
print(reconly.text if reconly.text else "(empty)")
print(f"[chars={len(reconly.text)}]")

# ── Warm timing ─────────────────────────────────────────────────────────────
detrec_engine = time_fn(lambda: engine(arr))                                  # inference only
detrec_full   = time_fn(lambda: recognize_ppocr_qimage(prep))                 # full production call
reconly_engine = time_fn(lambda: engine(arr, use_det=False, use_cls=False))   # inference only

print("\n" + "=" * 64)
print("TIMING (ms, warm)")
print("=" * 64)
print(f"det+rec   engine(arr)            : {stats(detrec_engine)}")
print(f"det+rec   full recognize_ppocr   : {stats(detrec_full)}   <- production per-call")
print(f"rec-only  engine(use_det=False)  : {stats(reconly_engine)}")

med_full = statistics.median(detrec_full)
med_reconly = statistics.median(reconly_engine)
print(f"\nspeedup (full det+rec / rec-only): {med_full / med_reconly:.1f}x")
print(f"det+rec overhead (full - engine) : {statistics.median(detrec_full) - statistics.median(detrec_engine):.1f} ms  (compose + arr build + gc)")

# ── rapidocr's own per-stage elapse breakdown ───────────────────────────────
r1 = engine(arr)
r2 = engine(arr, use_det=False, use_cls=False)
if hasattr(r1, "elapse_list"):
    print(f"\nrapidocr elapse_list  det+rec : {r1.elapse_list}")
if hasattr(r2, "elapse_list"):
    print(f"rapidocr elapse_list  rec-only: {r2.elapse_list}")
