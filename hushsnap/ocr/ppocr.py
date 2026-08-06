"""
PP-OCR Engine Implementation — parameter choices vs RapidOCR defaults.

Models
  Detection:  PP-OCRv6 SMALL (~10 MB) — language-agnostic, only locates text
              regions; does not recognise characters.
  Recognition: PP-OCRv6 SMALL (21 MB) — 50-language dictionary including
              Japanese, Traditional Chinese, and extended Unicode symbols.
  Classifier:  PP-OCRv4 (disabled — saves ~10 % latency, only needed for
              180° rotated images).

  Requires RapidOCR ≥ 3.9.1 (PP-OCRv6 model support).

Why SMALL det + SMALL rec
  ───────────────────────
  Det and rec are independent ONNX models — any det size can pair with any
  rec size.  The three PP-OCRv6 sizes (tiny/small/medium) differ only in
  channel width, not architecture; they share the same PPLCNetV4 backbone.

  Det choice — SMALL over TINY:
    TINY det underperforms on scripts beyond Simplified Chinese and English —
    most visibly it fragments vertical Japanese into many small square boxes
    that defeat column reconstruction.  SMALL det returns clean text regions
    across all supported scripts (incl. Traditional Chinese and Japanese), so
    it is used despite a small latency cost.  The detection-only Hmean
    benchmark (PaddleOCR) also favours SMALL det.

  Rec choice — SMALL over TINY:
    TINY rec (1.1M parameters) lacks Japanese entirely and shows significant
    errors on Traditional Chinese characters outside the simplified set.  It
    also confuses visually similar symbols (e.g. oxygen atom O vs digit 0)
    and degrades on superscripts/subscripts and uncommon Unicode.  On modern
    Simplified Chinese and English, TINY rec and SMALL rec are comparable.
    SMALL rec is chosen for full script coverage, not for speed.

Pipeline: det+rec → fallback rec-only.
  Detection provides reading-order layout (overlap-based clustering) for
  horizontal left-to-right and vertical right-to-left text.  When the
  detector finds no boxes, the engine falls back to recognition-only:
  recognize_ppocr_qimage crops arr back to original_size if preprocessing
  upscaled it, then _recognize_without_detection direct-feeds that image to
  the recognizer (use_det=False, no crop / contrast-normalize).  A failure
  surfaces empty so the caller prompts recapture (ocr_empty_popup_hint).

  Vertical CJK needs no rotation: PP-OCRv6 SMALL recognises upright CJK
  characters in vertical columns natively, so a 90° rotation (which loses
  5–20 % of CJK characters) is avoided.  Vertical text is detected via an
  area-weighted tall-box heuristic and rebuilt through a vertical-aware line
  builder.  Rotated/angled non-CJK text remains a model-level limitation.

  Pad-to-960 pre-processing was removed in Jul 2026.  The padding was originally
  introduced to work around RapidOCR v5's detector behaviour: when an image's
  short side is < limit_side_len, the detector upscales it to that length via
  cv2.resize.  rapidocr defaulted that to 736, so a tiny image (e.g. 32 px short
  side) got a ~23× upscale causing catastrophic interpolation blur that destroys
  character features; padding to 960 px forced a 1∶1 scale.  The root cause is
  that screenshots are pixel-exact vector-rasterized text - already sharp, so any
  forced upscale only blurs them.  The fix is to not upscale in the first place:
  limit_side_len is now pinned to 32 (see the derivation below - the only value
  that both keeps aspect ratio and minimizes upscale).  UX: screenshots taken
  for OCR almost never have a short side below ~20px, so at 32 the upscale is
  ≤1.6× and harmless - unlike rapidocr's 736 which force-scales a 46px crop
  ~16× and destroys it (and, since 736 is a large short side for desktop OCR,
  taxes the latency of nearly every screenshot - small ones 8-17× slower).  The rec-only fallback handles the remaining wide-flat
  tiny cases (short side < 48 px, aspect ratio ≥ 3∶1) down to ~15 px, so the
  simpler pipeline without padding is more reliable overall.

Parameter choices vs RapidOCR defaults
  ────────────────────────────────────
  Global.use_preprocess_img = False (rapidocr default True)
  Global.use_vertical_padding = False (rapidocr default True)
      Both rapidocr-ONLY features with no PP-OCR equivalent.  Pinned False to
      match PP-OCR: screenshots don't need global-scaling guard nor vertical
      padding.  A/B on real screenshots: vertical_padding is a wash;
      preprocess never fires on screenshot crops.  Global.max_side_len is
      absent (dead when use_preprocess_img=False — resize_image_within_bounds
      is never reached).  Previously pinned to 4000 (not rapidocr 2000) to
      leave 4K shots unscaled; if use_preprocess_img is ever re-enabled,
      reinstate that pin.

  Rec.rec_batch_num = 1 (default 6)
      Recognition runs sequentially on CPU — batching only adds threading
      overhead without parallelism.

  intra_op_num_threads = -1 (default -1)
      Left at the ONNX Runtime default.  Manual tuning has been attempted
      but the optimal value drifts across ORT versions (1.20 → 8, 1.28 →
      12, 1.21 → 14) and is only measurable on large/dense screenshots.
      On typical captures (≤200 chars) the difference is in the noise.
      Let ORT's own heuristics choose — they are maintained alongside the
      thread pool and adapt across versions.

  inter_op_num_threads = 1 (default -1)
      Det → Rec pipeline is strictly sequential; inter-op parallelism has
      nothing to schedule.

  enable_cpu_mem_arena = False (ONNX default: True)
      ONNX's arena allocator pools large blocks without releasing them,
      keeping the working set at peak after OCR completes.  Disabling it
      lets the OS reclaim pages immediately — important for a long-running
      tray app.
"""

import logging
import re
import threading
import time

import numpy as np

# Import ppocr library at startup to ensure thread-safe loading of C/C++ extensions.
# Importing at module level (not inside functions / background threads) prevents
# Python 3.13 JIT + C-extension loader race conditions on the first OCR call.
from rapidocr import RapidOCR, OCRVersion, ModelType

# Alias kept for backward compatibility (tests monkeypatch ppocr_module.PPOCR).
PPOCR = RapidOCR

from PyQt6 import QtCore, QtGui

from .models import OcrBox, OcrLine, OcrRecognition, OcrWord
from .preprocess import OcrPreprocessResult
from ..system.memory_utils import get_working_set_mb, fmt_memory, trim_working_set

logger = logging.getLogger(__name__)

# ── Fallback / tuning constants ──────────────────────────────────────────────
# Centre-distance ratio for greedy line/column clustering.  A box joins
# the current line when its centre is within 0.4 × median_height of the
# line's median centre (or 0.4 × median_width for vertical columns).
# This single font-agnostic condition replaces the previous two-condition
# (overlap + centre) approach — at 0.4 the overlap check is implied.
_CENTER_RATIO = 0.4

# Box-height to font-size ratio.  PP-OCR detection boxes are systematically
# taller than the rendered font-size — the DB shrink→unclip pipeline produces
# boxes whose height median is ~1.31× the CSS font-size at unclip_ratio=1.6.
# This was measured on 168 single-line samples covering 4 languages
# (en, zh-CN, zh-TW, ja), 14 system fonts, and 12 font sizes (16–80 px) at
# devicePixelRatio=1.5, rendered via Playwright Chromium (scripts/box_fit_test.py
# and scratch/box_fit_test_16_80/detail.json).
#
# Per-language breakdown at unclip_ratio=1.6:
#   en     median 1.24   p5 1.13    (Latin descenders make boxes ~20 % tighter)
#   zh-CN  median 1.33   p5 1.19
#   zh-TW  median 1.35   p5 1.27
#   ja     median 1.33   p5 1.19
#
# Using the conservative Latin edge (1.2) as a global divisor avoids language
# detection while keeping the worst under-estimate at −10 % (Consolas 48 px,
# a monospace outlier where estimated_fs = 43 px instead of 48).  For all CJK
# fonts and the majority of Latin samples the error is ≤ 5 %.  The threshold
# constants (_CENTER_RATIO, _INDENT_RATIO, _PARAGRAPH_GAP_RATIO, _INLINE_GAP_RATIO)
# were all tuned against box-height-as-proxy and remain unchanged — only the
# height input is calibrated.
_BOX_H_TO_FS_RATIO = 1.2

# ── Default PP-OCR engine parameters (documented in the module header) ───────
# Det.mean / Det.std: pinned to ImageNet [0.485,0.456,0.406] / [0.229,0.224,0.225]
# (= the PP-OCRv6 training normalization, from PaddleOCR's
# configs/det/PP-OCRv6/PP-OCRv6_small_det.yml NormalizeImage), NOT rapidocr's
# default [0.5,0.5,0.5] (== img/127.5 - 1).
#
# Reason: the claim that rapidocr's 0.5 is better is NOT verified.  The prior
# A/B that supported 0.5 ran on the old dpr=1 undersized dataset with
# limit_side_len=736 (both since fixed) - tainted, retracted.  On the corrected
# dataset (dpr=1.5, limit_side_len=32, 480 images) ImageNet is in fact
# measurably better:
#   A/B  scripts/ab_det_normalize.py  over  scripts/gen_normalize_dataset.py
#   A=[0.5,0.5,0.5]  vs  B=ImageNet  - only mean/std vary (limit_side_len=32,
#   use_dilation=false, use_cls=false identical in both = current production det
#   path).  480 images (6 cats x 80, dpr=1.5, real font sizes, design-matrix
#   balanced across tier/scheme/lang/size_bin), seed 20260724:
#     mean CER   A=0.0711  B=0.0647   (B-A = -0.0065)
#     pooled CER A=0.0568  B=0.0507
#     paired    A better 47, B better 109, tie 324; A==B text 296/480 (62%)
#   B-A = -0.0065 reproduces across config changes (was -0.0065 under the old
#   limit_side_len=64/use_dilation=true too) - the conclusion is stable.
#   Root cause: 0.5 over-segments det (total boxes A=2835 vs B=2565; e.g.
#   code_008 A=22 boxes vs B=7) -> fragmented rec.  B's lead is in
#   code/terminal (B-A -0.022 / -0.020); on word/web/ui/chat within noise.
#
# Real-screenshot sanity (the decisive check): on two actual HushSnap
# screenshots - a Chinese paragraph and a black-bg PowerShell terminal - A and
# B produce IDENTICAL box counts and identical text (box positions within a few
# px).  So on real screenshots 0.5 vs ImageNet is a wash; neither is "better"
# in practice.  Given that, pick the one with a PP-OCR source (ImageNet, the
# training normalization - matches what the detector weights expect) over
# rapidocr's sourceless 0.5.  See memory rapidocr-det-normalize-not-imagenet
# (updated: verdict reversed).  Det path only - Rec has its own preprocessing
# (rec_img_shape [3,48,320]).
#
# -- det config: rapidocr default vs PaddleOCR training/inference ------------
# (verified from PaddleOCR-main.zip: configs/det/PP-OCRv6/PP-OCRv6_small_det.yml,
#  deploy/cpp_infer/src/configs/OCR.yaml, deploy/hubserving/ocr_det/params.py)
#
#   Param            rapidocr cfg default   PaddleOCR                 HushSnap now
#   ---------------  ---------------------   ------------------------   ------------
#   Det.ocr_version  "PP-OCRv6"              PP-OCRv6                   PPOCRV6 (set in _get_engine)
#   Det.mean         [0.5,0.5,0.5]           [0.485,0.456,0.406] ImNet  [0.485,0.456,0.406] ImNet  (= PP-OCRv6 training)
#   Det.std          [0.5,0.5,0.5]           [0.229,0.224,0.225] ImNet  [0.229,0.224,0.225] ImNet  (= PP-OCRv6 training)
#   Det.limit_side_len 736                   null(->code 736)           32   (HushSnap guard; see note)
#   Det.limit_type   "min"                  "min"                      "min" (rapidocr default)
#   Det.use_dilation True                   False(implicit)             False (= PaddleOCR implicit)
#
# mean/std: pinned to ImageNet = PP-OCRv6 small det inference.yml NormalizeImage
#   (mean [0.485,0.456,0.406] / std [0.229,0.224,0.225], direct source - the
#   model's own shipped config).  rapidocr defaults to [0.5,0.5,0.5] (== img/127.5
#   -1), a sourceless choice.  No evidence rapidocr's 0.5 is better on desktop
#   screenshots: the rapidocr author's det-eval-set A/B found 0.5 better on
#   detection H-mean (natural-scene det set, not screenshots), but HushSnap's
#   dpr=1.5 screenshot end-to-end A/B found ImageNet marginally better (0.0647
#   vs 0.0711, B-A=-0.0065, stable across limit_side_len/dilation config) and
#   real screenshots show a wash - so 0.5 is NOT proven better for this domain.
#   Per "keep PP-OCR defaults where verifiable", use ImageNet.
#   See memory rapidocr-det-normalize-not-imagenet.
#
# Det.limit_side_len: pinned to 32 (a guard), NOT rapidocr's 736.  No PP-OCR
#   source for 32: PP-OCRv6 small det's inference.yml sets DetResizeForTest=null,
#   which falls to the PaddleOCR code default 736 (same as rapidocr) - so the
#   "64 = PaddleOCR v6" once cited here was the MEDIUM model's OCR.yaml, not
#   small.  32 is HushSnap's own choice, derived as follows.  det's resize
#   (ch_ppocr_det/utils.py DetPreProcess.resize, limit_type=min) is two steps:
#     (a) if short side s < N: scale BOTH dims by r = N/s  (uniform, aspect-
#         preserving); else r = 1.
#     (b) round each dim to a multiple of 32:  D -> R(D) = round(D/32)·32,
#         Python banker's rounding (0.5 -> even).  This is unconditional (a
#         hard net requirement), applied to EVERY image.
#   After (a) the short side is exactly min(s, N); after (b) it is R(min(s,N)).
#   R is round-to-NEAREST, not a floor: R(16) = round(0.5)·32 = 0 (even), so a
#   16px dim snaps to 0 and trips the `<=0` guard -> det returns EMPTY.
#
#   Why N must be a multiple of 32:  after step (a) the short side lands on N,
#   then step (b) snaps it to R(N).  If N is a multiple of 32, R(N) = N (zero
#   extra snap) and the long side, scaled by the same r, also lands near a 32-
#   multiple -> aspect ratio preserved.  If N is NOT a multiple of 32 (e.g. 17),
#   R(N) != N and the short side takes an extra asymmetric snap while the long
#   side does not -> aspect ratio distorted (measured 8x200 -> 32x416 under
#   N=17, ratio 1:25 crushed to 1:13; under N=32 it stays 1:25).  So N is
#   constrained to {32, 64, 96, ...}.
#
#   Within that set pick the smallest:  larger N (64/736) adds pointless upscale
#   blur to screenshots (already pixel-exact sharp); rapidocr's 736 force-scales
#   a 46px crop ~16x and collapses it (CER=1.0).  Smaller-than-32 multiples: only
#   0, which makes s<=16 round to 0 -> det returns EMPTY (losing the det vote
#   entirely to the rec-only fallback).  Hence 32 - the unique value that is a
#   multiple of 32, non-empty, and minimal.  UX corroboration: screenshots taken
#   for OCR almost never have a short side below ~20px, so at N=32 the upscale is
#   <=1.6x (harmless), in stark contrast to 736's ~16x destruction.  No PP-OCR
#   source for 32 (small det inference.yml is null -> code 736); 32 is a guard.
#   See memory limit-side-len-736-wrong.
#
# Det.use_dilation: pinned False (= PaddleOCR default), NOT rapidocr's True.
#   use_dilation is a DB post-step (ch_ppocr_det/utils.py DBPostProcess): a
#   2x2 cv2.dilate on the thresholded score map before findContours, which
#   connects text regions <=1px apart in the downsampled score map.  It was
#   made for low-res photos where blur/anti-aliasing fragments strokes.
#   Screenshots are pixel-exact vector-rasterized text with regular spacing,
#   so the connect benefit is marginal and the over-merge risk is real (it
#   fuses adjacent list items "- A - B" into one box, can join near lines).
#   Measured on scratch/desktop_dataset (480 screenshots, dpr=1.5, design-matrix
#   balanced across tier/scheme/lang/size_bin): under CURRENT ImageNet mean/std
#   + limit_side_len=32 (= production det path) True vs False is a WASH -
#   meanCER True=0.0639 vs False=0.0647, Δ=+0.0007 (noise), paired True wins 80
#   / False wins 47 / tie 353; box count identical on 455/480 (total 2552 vs
#   2565).  Stable across config: was Δ=+0.0016 under the old 64/true isolation.
#   Failure shape: True over-merges web list items; False over-splits tight
#   code/ui (drops more spaces).  Given the wash + PaddleOCR source + screenshots
#   being a cleaner subset of PaddleOCR's document domain (which uses False),
#   pick False.
#   See scripts/ab_det_use_dilation.py + scratch/ab_use_dilation_report.txt
#   and memory det-use-dilation-true (updated: reversed to False).
#
# Summary of HushSnap Det config vs sources (honest - no false "aligns with
# PP-OCRv6" claims):
#   mean/std=ImageNet   = PP-OCRv6 small det inference.yml (direct source) ✓
#   use_dilation=False  = PP-OCRv6 small det inference.yml (implicit: no field
#                          -> code default False) ✓  [rapidocr defaults True]
#   limit_side_len=32   = HushSnap guard, NO PP-OCR source (small inference.yml
#                          is null -> code 736; we override because 736 both
#                          destroys small-image quality AND taxes the latency of
#                          ~every desktop screenshot - 736 is a large short side
#                          for desktop OCR).  32 = the unique multiple of 32 that
#                          is non-empty and minimal (see derivation above);
#                          round(dim/32)*32 is round-to-nearest, not a floor.
#   use_preprocess_img=False  = rapidocr-only photo/scan guard (min/max side);
#                          premises (uncontrolled sizes, OOM) don't hold for
#                          screenshots - see dict comment below.
#   use_vertical_padding=False = rapidocr-only pad (aspect>8 or h<=30);
#                          premise DOES occur (single-line crops), but padding=False
#                          still handles them (probed: det finds the line, no
#                          fallback) - see dict comment below.
#   Global.max_side_len  = REMOVED (dead with use_preprocess_img=False — the
#                          resize_image_within_bounds path is never reached).
# Principle: keep PP-OCR defaults where they have a verifiable small-model
# source (mean/std, use_dilation); override only where a rapidocr default is
# demonstrably bad for screenshots (limit_side_len 736 collapses small crops);
# switch off rapidocr-only features that have no PP-OCR equivalent.
_DEFAULT_ENGINE_PARAMS: dict = {
    # Global.max_side_len was pinned to 4000 (rapidocr-ONLY pre-det long-side
    # cap; no PP-OCR equivalent).  REMOVED: with use_preprocess_img=False the
    # code path that reads it (resize_image_within_bounds) is never reached, so
    # the pin was dead.  If use_preprocess_img is ever re-enabled, reinstate the
    # pin (4000 to cover 4K full-screen shots); without it rapidocr defaults 2000.
    # use_preprocess_img / use_vertical_padding: both rapidocr-only (no PP-OCR
    # equivalent).  Pinned False.  These are PHOTO/SCAN preprocesses whose design
    # premises don't hold for desktop screenshots:
    #
    # use_preprocess_img (rapidocr's global pre-det resize, params min_side_len
    #   =30 / max_side_len=2000): meant to guard "image too large -> OOM" and
    #   "too small -> illegible" - i.e. UNCONTROLLED camera/scan input sizes.
    #   Desktop screenshots are user-cropped and size-controlled: a 4K full-screen
    #   shot is ~3840px max side (no OOM risk on a desktop OCR run), and the small
    #   end is already guarded by Det.limit_side_len=32 below.  So neither guard
    #   fires meaningfully; the magic numbers 30/2000 are calibrated for natural
    #   images, not vector-rasterized text.  (rapidocr 3.9.2 made these togglable
    #   with default True; we opt out.)
    #
    # use_vertical_padding (rapidocr's top/bottom pad, params min_height=30 /
    #   width_height_ratio=8): pads an image whose aspect ratio >8 (or h<=30)
    #   to "restore the training aspect ratio" so det sees more typical
    #   proportions.  Unlike the size guards above, this premise DOES occur in
    #   desktop OCR - a single line (terminal cmd, title, one-row toolbar) is
    #   routinely 1:10+ and triggers the pad.  But padding=False still handles
    #   these fine: probed on 7 single-line crops (ratio 2 to 24, h 22-30px,
    #   all triggering the pad condition), det+rec under padding=False returned
    #   exactly 1 box each and recognized the text correctly - det did NOT empty,
    #   so the rec-only fallback was never triggered.  padding=True gave at most a
    #   tiny edge (1/7: `created_at` vs `created at`, an underscore) - not worth
    #   the magic numbers.  (limit_side_len=32 upscales the short side uniformly
    #   to 32, which is enough for det to find the line without padding.)  A/B on
    #   the full dataset: vertical_padding is a wash.  The magic numbers 30/8
    #   remain photo-domain heuristics; closing the transform removes both the
    #   magic numbers and an unneeded pass.
    #
    # Net: closing these is "remove an unneeded transform + its magic numbers",
    # not "give up a benefit".  See memory preprocess-vertical-padding-false.
    "Global.use_preprocess_img": False,
    "Global.use_vertical_padding": False,
    "Rec.rec_batch_num": 1,
    # Det.mean / Det.std: pinned to ImageNet [0.485,0.456,0.406] /
    # [0.229,0.224,0.225] (= PP-OCRv6 training normalization,
    # PP-OCRv6_small_det.yml NormalizeImage), NOT rapidocr's default 0.5.
    # rapidocr's 0.5 being better was not verified (old A/B was tainted; the
    # corrected A/B shows ImageNet better, real screenshots show a wash).  Pin
    # to the value with a PP-OCR source.  See det-config block above + memory
    # rapidocr-det-normalize-not-imagenet (verdict reversed).
    "Det.mean": [0.485, 0.456, 0.406],
    "Det.std": [0.229, 0.224, 0.225],
    # Det.limit_side_len = 32, NOT rapidocr's 736.  Two independent reasons:
    #
    # (1) QUALITY: 736 (limit_type=min) force-upscales any short side <736 to
    #     736 (aspect-preserving).  On small screenshots this destroys the
    #     image - measured objectively on 6 small crops (Sobel edge sharpness
    #     drops 78-95%, unique colors explode 22 -> ~60000 from interpolation
    #     artifacts), DB finds no box, CER=1.0.  Smaller short side = worse
    #     (a 140x29 crop -> 3552x736, a 25x linear upscale).
    # (2) PERFORMANCE: 736 forces almost EVERY desktop screenshot through a
    #     736-short-side CNN pass, because 736px is a LARGE short side for
    #     desktop OCR - most screenshots (single lines, UI cards, chat,
    #     multi-para docs) have short sides well under 736.  Measured end-to-end
    #     det+rec latency (median of 3): small 140x29 -> 33ms(32) vs 554ms(736),
    #     16.8x slower; mid 300x100 -> ~2-6x slower; only near-736 images break
    #     even.  So 736 taxes the latency of nearly every capture, not just
    #     tiny ones - and the smaller the screenshot, the worse both effects.
    #
    # Why exactly 32 (the guard value).  det's resize is two steps: (a) scale
    # both dims by ratio = N/s when short side s < N (uniform); (b) snap each dim
    # via R(D) = round(D/32)·32 (banker's rounding, unconditional - runs on every
    # image, upscaled or not; the CNN backbone has stride 32 so inputs must be
    # multiples of 32).  The final cv2.resize passes no interpolation flag, so it
    # defaults to INTER_LINEAR (bilinear) - pure fabrication of in-between pixels
    # that only blurs pixel-exact screenshot text, recovering no real detail.
    #
    # Three constraints pin N to 32:
    #  (1) Want N as SMALL as possible.  Screenshots are already sharp vector-
    #      rasterized text; any upscale only blurs (bilinear) AND slows det (CNN
    #      cost grows with output area).  Smaller N = less upscale = better on
    #      both.  So the search is downward from small N, not upward.
    #  (2) N <= 16 is broken: R(N) = round(N/32)·32 = 0 (at N=16, 0.5 rounds to
    #      even 0; below 16 is <0.5), so the short side snaps to 0 -> det returns
    #      EMPTY (the short side is effectively dropped).
    #  (3) 17 <= N < 32 distorts the ratio.  Short side = R(N) = round(N/32)·32
    #      = 32 for ALL N in this range (N/32 in (0.5,1) -> rounds to 1) --
    #      INSENSITIVE to N.  Long side = round(ratio·N/32)·32 = round(ratio·x)·32
    #      with x=N/32 in (0.5,1) -- SENSITIVE to N (varies continuously).
    #      Preserving the ratio needs long side = ratio·32, i.e. round(ratio·x)=ratio,
    #      which holds only at x=1 (N=32).  For x<1 the short side is pinned at 32
    #      while the long side shrinks with N -- asymmetric N-sensitivity breaks the
    #      ratio (8x200 -> 32x416 = 13:1 instead of 25:1 at N=17).  This defeats
    #      the intent of pulling the short side near 32 WITHOUT distorting the ratio.
    # So 32 is the smallest N that is non-empty (rules out <=16) and ratio-honest
    # (rules out 17-31; for N>32 only multiples of 32 are ratio-honest, and they
    # upscale more).  UX: OCR screenshots almost never have short side <~20px, so
    # at 32 the upscale is <=1.6x (harmless).  No PP-OCR source (small det
    # inference.yml is null -> code 736); 32 is a guard, not an accuracy lever.
    # See memory limit-side-len-736-wrong.
    "Det.limit_side_len": 32,
    # Det.use_dilation: pinned False (= PaddleOCR default), NOT rapidocr's True.
    # DB post-process: a 2x2 cv2.dilate on the thresholded score map before
    # findContours (ch_ppocr_det/utils.py DBPostProcess).  dilation connects
    # text regions separated by <=1px in the (downsampled) score map - useful
    # for low-res photos where anti-aliasing/blur fragments strokes, but
    # screenshots are pixel-exact vector-rasterized text with regular spacing,
    # so the connect benefit is marginal and the OVER-MERGE risk is real: it
    # fuses adjacent items (e.g. list rows "- A - B" into one box) and can
    # join neighbouring lines.  Measured on scratch/desktop_dataset (480 imgs,
    # dpr=1.5, design-matrix balanced): under the CURRENT production det path
    # (ImageNet mean/std, limit_side_len=32) True vs False is a WASH -
    # meanCER True=0.0639 vs False=0.0647, Δ=+0.0007 (noise), paired 80/47/353;
    # box count identical 455/480.  Stable: was Δ=+0.0016 under old 64/true.
    # Failure shape: True over-merges web list items; False over-splits tight
    # code/ui (drops more spaces).  Given the wash + PaddleOCR's source +
    # screenshots being a cleaner subset of PaddleOCR's document domain (which
    # doesn't need dilation), pick False.  See memory det-use-dilation-true
    # (updated: reversed to False).
    # det-use-dilation-true (updated: reversed to False).
    "Det.use_dilation": False,
    "EngineConfig.onnxruntime.intra_op_num_threads": -1,
    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    "EngineConfig.onnxruntime.enable_cpu_mem_arena": False,
}


# -- pure functions ----------------------------------------------------

def ppocr_box_to_bbox(box) -> tuple[float, float, float, float]:
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


def bbox_to_ocr_box(left: float, top: float, right: float, bottom: float) -> OcrBox:
    return OcrBox(x=left, y=top, width=max(0.0, right - left), height=max(0.0, bottom - top))


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


def block_separator(left: str, right: str) -> str:
    if not left or not right:
        return ""
    if is_cjk_or_fullwidth(left[-1]) and is_cjk_or_fullwidth(right[0]):
        return ""
    if left[-1] in "-—–…」』":
        return ""
    if right[0] in (
        ",.;:!?)]}"
        "，。、；：！？"
        "）】》〉〕｝"
        "…—–·"
        "\"'”’"
        "」』"
    ):
        return ""
    return " "


# -- Centre-distance line clustering ------------------------------------
# Greedy centre-distance clustering replaces the old recursive XY-Cut
# pipeline.  The single _CENTER_RATIO (0.4) governs all grouping
# decisions — no DPI-, font-size-, or line-spacing-dependent constants.
#
# Horizontal text: sort by y_top, greedily group into lines by centre
#   distance → sort lines top→bottom, within-line left→right.
# Vertical CJK text: sort by x_right descending, greedily group into
#   columns by centre distance → sort columns right→left, within-
#   column top→bottom.
#
# Design rationale — why a single centre-distance condition is enough.
#
# 1.  The previous two-condition approach (overlap + centre) was
#     redundant: at the 0.4 threshold, |Δcenter| < 0.4 × median_h
#     mathematically implies overlap_ratio > 0.5.  Dropping overlap
#     removes ~20 lines of ref-band / min()-denominator computation
#     with zero behavioural change.
#
#     The overlap-ratio check (the standard approach in layout analysis)
#     tests whether a candidate box sufficiently overlaps the reference
#     line's vertical band [M − ½H, M + ½H]:
#
#         overlap / min(box_h, H)  >  r      (e.g. r = 0.5)
#
#     The centre-distance gate tests a simpler condition:
#
#         |box.center_y − M|  <  k × H       (e.g. k = 0.4)
#
#     These two gates have a clean logical relationship.  Provided
#     k ≤ min(½, 1−r), whenever the centre-distance gate accepts a pair,
#     the overlap-ratio gate is *guaranteed* to accept it too.  In other
#     words, under this parameter regime centre-distance is a stricter
#     (more conservative) sufficient condition for overlap-ratio.  The
#     entailment is one-way: overlap-ratio does NOT imply centre-
#     distance, so centre-distance rejects some pairs overlap-ratio
#     would accept.  That asymmetry is exactly why dropping the overlap
#     check is safe: it can never ACCEPT a pair overlap-ratio would
#     REJECT (no false merge relative to the old two-condition gate),
#     while side-stepping overlap-ratio's sensitivity to detection-box
#     precision.
#     (Verified numerically, 2 M samples + fine grid, scripts/
#     verify_centre_implies_overlap.py: worst-case overlap ratio = 1−k,
#     attained at h = H, d -> kH⁻.  Production (k=0.4, r=0.5) clears r
#     by 0.10; the code's /_BOX_H_TO_FS_RATIO divisor makes the
#     effective k = 1/3, widening the margin to 0.167.)
#
#     Our pair (k=0.4, r=0.5) satisfies 0.4 ≤ min(0.5, 0.5), so
#     centre-distance ⇒ overlap-ratio holds unconditionally.  If k is
#     ever raised past 0.5, the overlap-ratio check must be
#     reintroduced as a second gate.
#
# 2.  PP-OCR v6 small rarely fragments punctuation or CJK characters
#     into separate small boxes (verified on ~10 test images with
#     mixed Chinese/Latin/symbol text).  The "fragment-first" edge
#     case — a tiny detection box blocking subsequent normal boxes
#     from joining a line — can be triggered only with deliberate
#     special-symbol placement (™, •, ®).  Even then the fragmentation
#     is unstable: ±1 px viewport offset or 0.1× scale change reshapes
#     the fragment set entirely.  Optimising the denominator formula
#     for inputs that are not reproducible is not worthwhile.
#
# 3.  Zebra's word→line centreDistanceRatio (0.6) and height-averaged
#     denominator are designed for a pairwise (word vs word) model.
#     Our median-based (box vs line) model has different statistical
#     properties; the 0.4 threshold was tuned on real screenshots.
#     Importing Zebra's constants without Zebra's pairwise architecture
#     would be cargo-cult tuning.
#
# 4.  PP-OCR detection boxes are systematically taller than the actual
#     font-size (see scripts/measure_box_inflation.py).  Measured across
#     8–64 px, Latin + CJK, n ≈ 2700:
#
#         ratio = box_h / font_size
#         Latin:  median 1.23×   stdev 0.17×   p5–p95  1.00–1.56×
#         CJK:    median 1.19×   stdev 0.14×   p5–p95  1.00–1.43×
#         delta = box_h − font_size
#         Latin:  median +5.0 px           CJK:    median +4.0 px
#
#     The ratio is not a simple function of font-size — the within-size
#     variance (stdev ≈ 0.14–0.17×) dominates any systematic trend.
#     This means you cannot reliably recover the original typesetting
#     intent from box dimensions: box height is an unreliable proxy for
#     font-size, and therefore any gate built on box height (ref-band
#     width, overlap denominator) is gated by a quantity that is
#     systematically decoupled from the actual text.  Centre-distance
#     is structurally immune: the box centre is determined by the real
#     text position on the page, not by the detector's bounding-box
#     padding.  You don't need to know the font-size to know whether
#     two words sit on the same baseline.
#
#     So even if k were raised past 0.5 and overlap had to be
#     reintroduced as a second gate, the height-inflation problem would
#     make overlap a *less reliable* gate than centre-distance, not a
#     complementary one.  Centre-distance wins on two independent
#     grounds: it is mathematically sufficient (§1, no overlap needed
#     at k=0.4), and it does not depend on box-height estimates that
#     are systematically decoupled from font-size.
#
#     If the detector model is upgraded in the future, re-measure —
#     the ratio distribution above is model-specific.

# ---------------------------------------------------------------------------


def _normalize_blocks(blocks: list[dict]) -> list[dict]:
    """Convert raw PP-OCR detection blocks to internal representation; filter junk.

    Blocks without a valid bounding box are skipped — without real
    coordinates we cannot place them in reading order, and a fabricated
    box would distort line clustering.
    """
    empty_skipped = 0
    invalid_skipped = 0
    normalized: list[dict] = []
    for block in (blocks or []):
        raw_text = str(block.get("text", "") or "")
        # Filter out truly empty or whitespace-only blocks
        if not raw_text.strip():
            empty_skipped += 1
            continue
        # Strip boundary whitespace once, at the source.  Every block that
        # reaches the layout engine is then clean; the only whitespace in
        # downstream text is whitespace the engine deliberately inserts
        # (block_separator / inline gap / indent).  This makes the render-time
        # rstrips in text.py a no-op rather than a guard - they stay as a
        # safety net but no longer compensate for dirty rec boundaries.
        raw_text = raw_text.strip()

        left, top, right, bottom = ppocr_box_to_bbox(block.get("box"))
        w = right - left
        h = bottom - top
        if w <= 0 or h <= 0:
            # Block has text but no valid bounding box — skip it.
            # Without real coordinates we cannot place it in reading order,
            # and a fabricated box at (0,0) would distort line clustering.
            invalid_skipped += 1
            logger.debug("[DET] _normalize_blocks: skip invalid bbox (w=%.1f h=%.1f) %r",
                         w, h, raw_text[:80])
            continue
        normalized.append({
            "text": raw_text,
            "left": left, "top": top,
            "right": right, "bottom": bottom,
            "width": w, "height": h,
            "center_x": (left + right) / 2,
            "center_y": (top + bottom) / 2,
        })
    if empty_skipped or invalid_skipped:
        logger.debug("[DET] _normalize_blocks: %d blocks → %d valid (empty=%d invalid=%d)",
                     len(blocks or []), len(normalized), empty_skipped, invalid_skipped)
    return normalized


def _greedy_line_cluster(blocks: list[dict]) -> list[list[dict]]:
    """Greedy line clustering for horizontal LTR text.

    1. Sort boxes by y_top (top → bottom)
    2. Greedy: if box centre is close to the line's median centre,
       add to current line.
    3. Within each line: sort by x_left (left → right)
    4. Between lines: sort by average y_center (top → bottom)

    Single condition::

        abs(box.center_y - median_center) < 0.4 × median_height

    This centre-distance check alone is functionally equivalent to the
    previous two-condition (overlap + centre) approach: at the 0.4
    threshold the overlap check is mathematically implied and therefore
    redundant.
    """
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: b["top"])

    lines: list[list[dict]] = []
    current: list[dict] = [sorted_blocks[0]]
    line_centers: list[float] = [sorted_blocks[0]["center_y"]]
    line_heights: list[float] = [sorted_blocks[0]["height"]]

    for box in sorted_blocks[1:]:
        sorted_centers = sorted(line_centers)
        n = len(sorted_centers)
        median_center = sorted_centers[n // 2]
        sorted_h = sorted(line_heights)
        median_h = sorted_h[n // 2]

        if abs(box["center_y"] - median_center) < _CENTER_RATIO * median_h / _BOX_H_TO_FS_RATIO:
            current.append(box)
            line_centers.append(box["center_y"])
            line_heights.append(box["height"])
        else:
            lines.append(current)
            current = [box]
            line_centers = [box["center_y"]]
            line_heights = [box["height"]]

    lines.append(current)

    # Step 3: sort within each line left → right
    for line in lines:
        line.sort(key=lambda b: b["left"])

    # Step 4: sort lines top → bottom by average y_center
    lines.sort(key=lambda ln: sum(b["center_y"] for b in ln) / len(ln))

    logger.debug("[DET] _greedy_line_cluster: %d blocks → %d lines",
                 len(blocks), len(lines))
    for i, ln in enumerate(lines):
        ys = sorted({b["top"] for b in ln})
        texts = [b["text"][:30] for b in ln]
        logger.debug("[DET]   line[%d]: %d boxes  y_range=%s  texts=%s",
                     i, len(ln), ys[:6], texts)

    return lines


def _greedy_column_cluster(blocks: list[dict]) -> list[list[dict]]:
    """Greedy column clustering for vertical RTL text (CJK).

    Mirror of :func:`_greedy_line_cluster` with x/y roles swapped and
    sort directions reversed:

    1. Sort boxes by x_right descending (right → left)
    2. Greedy: if box centre is close to the column's median centre,
       add to current column.
    3. Within each column: sort by y_top (top → bottom)
    4. Between columns: sort by average x_center descending (right → left)

    Same single centre-distance condition as the line variant."""
    if not blocks:
        return []

    # Step 1: sort right → left
    sorted_blocks = sorted(blocks, key=lambda b: -b["right"])

    columns: list[list[dict]] = []
    current = [sorted_blocks[0]]
    col_centers: list[float] = [sorted_blocks[0]["center_x"]]
    col_widths: list[float] = [sorted_blocks[0]["width"]]

    for box in sorted_blocks[1:]:
        sorted_centers = sorted(col_centers)
        n = len(sorted_centers)
        median_center = sorted_centers[n // 2]
        sorted_w = sorted(col_widths)
        median_w = sorted_w[n // 2]

        if abs(box["center_x"] - median_center) < _CENTER_RATIO * median_w / _BOX_H_TO_FS_RATIO:
            current.append(box)
            col_centers.append(box["center_x"])
            col_widths.append(box["width"])
        else:
            columns.append(current)
            current = [box]
            col_centers = [box["center_x"]]
            col_widths = [box["width"]]

    columns.append(current)

    # Step 3: sort within each column top → bottom
    for col in columns:
        col.sort(key=lambda b: b["top"])

    # Step 4: sort columns right → left by average x_center
    columns.sort(
        key=lambda col: -sum(b["center_x"] for b in col) / len(col)
    )

    logger.debug("[DET] _greedy_column_cluster: %d blocks → %d columns",
                 len(blocks), len(columns))
    for i, col in enumerate(columns):
        xs = sorted({b["left"] for b in col})
        texts = [b["text"][:30] for b in col]
        logger.debug("[DET]   column[%d]: %d boxes  x_range=%s  texts=%s",
                     i, len(col), xs[:6], texts)

    return columns


def _build_lines_from_clusters(
    clusters: list[list[dict]],
    is_vertical: bool = False,
) -> list[OcrLine]:
    """Convert clustered blocks into OcrLine objects.

    Each cluster (a line for horizontal text, or a column for vertical
    CJK) becomes one OcrLine.  Blocks within a cluster are already sorted
    in reading order (left→right for horizontal, top→bottom for vertical).

    Completes the full within-line text rendering in one pass: block
    boundaries (block_separator), inline-gap geometry, and intra-block
    CJK<->Latin spacing (_apply_cjk_spacing).  Downstream stages see line
    text already spacing-final at the within-line level.
    """
    result: list[OcrLine] = []
    for cluster in clusters:
        text_parts: list[str] = []
        words: list[OcrWord] = []
        prev_block = None

        min_l = min(b["left"] for b in cluster)
        min_t = min(b["top"] for b in cluster)
        max_r = max(b["right"] for b in cluster)
        max_b = max(b["bottom"] for b in cluster)

        for block in cluster:
            if prev_block:
                sep = block_separator(prev_block["text"], block["text"])
                # ── inline gap spacing ──
                # Geometry is mirrored for vertical text: gap measured along
                # y (column stack) and normalised by box width, vs. x + box
                # height for horizontal.  Same ratio threshold & round rule -
                # a large visual gap between adjacent blocks becomes leading
                # spaces in the output regardless of orientation.
                if is_vertical:
                    gap = block["top"] - prev_block["bottom"]
                    est = min(prev_block["width"], block["width"]) / _BOX_H_TO_FS_RATIO
                else:
                    gap = block["left"] - prev_block["right"]
                    est = min(prev_block["height"], block["height"]) / _BOX_H_TO_FS_RATIO
                if est > 0:
                    gap_ratio = gap / est
                    if gap_ratio > 2.0:
                        n = max(1, round(gap_ratio))
                        sep = (sep or "") + (" " * n)
                if sep:
                    text_parts.append(sep)
            text_parts.append(block["text"])
            words.append(OcrWord(
                text=block["text"],
                bounding_box=bbox_to_ocr_box(
                    block["left"], block["top"],
                    block["right"], block["bottom"],
                ),
            ))
            prev_block = block

        result.append(OcrLine(
            text=_apply_cjk_spacing("".join(text_parts)),
            words=words,
            bounding_box=bbox_to_ocr_box(min_l, min_t, max_r, max_b),
        ))

    return result


def _word_upper_median(line: OcrLine, *, axis: str) -> float:
    """High median (``sorted[n // 2]``) of the line's word-box geometry.

    Used wherever a line's *scale* (height / centre) is needed as a ruler
    for thresholds - the same drift-robust role the greedy clusterer's
    running median plays, but computed over the *complete* line (the
    clusterer's median is a partial sample missing the last box).

    The union bbox (``max(bottom) - min(top)``) is *not* the ruler here -
    but not for the reason of the absolute box-height inflation documented
    in comment §4 (every detection box runs larger than the font-size,
    ratio ~1.2-1.7× per scripts/measure_box_inflation.py; that inflation
    is shared by all boxes, so it alone favours neither the median nor
    the union).  The ruler-relevant failure is *relative*: when
    a line yields several boxes their heights diverge (mixed font sizes,
    descenders vs caps, CJK vs Latin on one baseline) and the union takes
    the extreme, amplifying that divergence.  On realistic mixed-content
    rendered lines (``scripts/measure_within_line_drift.py``) 65 % of
    multi-box lines show >=2 px of such union inflation (median +7.7 %,
    p95 +19 %); the high median anchors to the majority of boxes and is
    robust to it.

    Scope - do not over-read those figures: most desktop screenshots carry
    continuous text, so the detector emits few boxes per line (typically
    <=3).  With so few boxes the relative divergence has little room to
    act and the median's practical gain over the union is small.  The
    median is a correctness backstop for the multi-box edge case (drop-caps,
    inline smaller runs, mixed-script lines), not a high-frequency win.

    For *extent / position* queries (leftmost edge, the area a line
    occupies) use ``line.bounding_box`` directly - the union is correct
    there because taking the extreme is the point.

    axis:
        ``'h'``  -> upper-median word-box height
        ``'cy'`` -> upper-median word-box centre_y
        ``'cx'`` -> upper-median word-box centre_x (reserved, unused now)

    Falls back to the union-bbox value when the line has no usable words
    (e.g. tests construct ``OcrLine`` with only ``bounding_box`` set).
    """
    bbox = line.bounding_box
    if axis == "h":
        vals = [w.bounding_box.height for w in line.words
                if w.bounding_box.height > 0]
        fallback = bbox.height
    elif axis == "cy":
        vals = [w.bounding_box.y + w.bounding_box.height / 2
                for w in line.words if w.bounding_box.height > 0]
        fallback = bbox.y + bbox.height / 2
    else:
        raise ValueError(f"unsupported axis {axis!r}")
    if not vals:
        return fallback
    s = sorted(vals)
    return s[len(s) // 2]


def _decide_paragraph_breaks(lines: list[OcrLine]) -> set[int]:
    """Return indices after which a blank separator line should be inserted.

    Deliberately conservative: this does NOT try to detect typographic
    paragraph spacing, which varies wildly (Markdown's blank line, Word's
    8pt space-after, a letter's signature gap, a poem's stanza break all
    differ, and a ci's upper/lower folio is often barely wider than a normal
    line).  It only separates lines that are obviously not one continuous
    block - the gap is large enough to fit a whole other line of text, so no
    reader would consider them adjacent.

    The decision is local rather than based on the page's average line height.
    Both the line height (threshold) and the line centre (gap) are taken as
    the high-median word-box value via :func:`_word_upper_median` (axis
    ``'h'`` and ``'cy'``).  The union bbox is *not* used as a ruler here:
    with several boxes on a line their heights diverge and
    ``max(bottom) - min(top)`` takes the extreme, amplifying that
    divergence.  The break rule's effective working region is the narrow
    band where the gap just fits one line - exactly where that union
    inflation would cause missed breaks.

    The remaining centre distance must exceed the taller adjacent line by
    half again.  In centre coordinates::

        next_center_y - current_center_y
            > current_h / 2 + next_h / 2 + 1.5 * max(current_h, next_h)

    i.e. the whitespace gap between the two boxes must exceed 1.5x the taller
    adjacent line's font size.  Symmetric for mixed font sizes and
    deliberately conservative: a smaller fraction (e.g. 0.6) would guess at
    paragraph semantics and fragment tight multi-line text; requiring room
    for one and a half taller lines only splits clearly-disconnected blocks.

    Returns a set of indices *i* such that a blank line is inserted after
    ``lines[i]`` during render.  Gap magnitude beyond the threshold does not
    produce additional blank lines - the separator is binary (one blank
    line, never several).  Horizontal-only; vertical is handled upstream.
    """
    if len(lines) <= 1:
        return set()

    medians = [_word_upper_median(line, axis="h") for line in lines]
    break_after: set[int] = set()

    for i in range(len(lines) - 1):
        line = lines[i]
        next_line = lines[i + 1]
        current_h = medians[i] / _BOX_H_TO_FS_RATIO
        next_h = medians[i + 1] / _BOX_H_TO_FS_RATIO
        if current_h <= 0 or next_h <= 0:
            continue
        current_center_y = _word_upper_median(line, axis="cy")
        next_center_y = _word_upper_median(next_line, axis="cy")
        threshold = current_h / 2 + next_h / 2 + 1.5 * max(current_h, next_h)
        if next_center_y - current_center_y > threshold:
            break_after.add(i)

    if break_after:
        logger.debug(
            "[DET] _decide_paragraph_breaks: local centre-distance rule  "
            "%d blank lines (%d lines)",
            len(break_after), len(lines),
        )

    return break_after


# -- CJK spacing post-processing (core patterns from pangu.py) ----------
# Applied as a final safety net: PP-OCR sometimes merges CJK+Latin into
# a single detection block, so block_separator() (block boundaries) misses
# those boundaries.  These two regexes catch them.
# Reference: https://github.com/vinta/pangu.py (MIT licensed)
#
# CJK Unicode blocks (verified code points):
#   CJK Radicals Supplement       \u2E80-\u2EFF
#   Kangxi Radicals               \u2F00-\u2FDF
#   Hiragana                      \u3040-\u309F
#   Katakana                      \u30A0-\u30FF
#   Katakana/Hiragana marks       \u30FB-\u30FF
#   Bopomofo                      \u3100-\u312F
#   Enclosed CJK Letters          \u3200-\u32FF
#   CJK Extension A               \u3400-\u4DBF
#   CJK Unified Ideographs        \u4E00-\u9FFF
#   CJK Compatibility             \uF900-\uFAFF
_CJK_RANGES = (
    r'\u2E80-\u2EFF'
    r'\u2F00-\u2FDF'
    r'\u3040-\u309F'
    r'\u30A0-\u30FF'
    r'\u30FB-\u30FF'
    r'\u3100-\u312F'
    r'\u3200-\u32FF'
    r'\u3400-\u4DBF'
    r'\u4E00-\u9FFF'
    r'\uF900-\uFAFF'
)

# Non-CJK (ANS) character class matched on the other side of the boundary:
#   A-Z a-z          Latin letters
#   \u0370-\u03FF    Greek and Coptic
#   0-9              digits
#   @$%^&*\-+\\=\|/  common symbols
#   \u00A1-\u00FF    Latin-1 Supplement
#   \u2150-\u218F    Number Forms
#   \u2700-\u27BF    Dingbats
_ANS_CLASS = (
    r'A-Za-z'
    r'\u0370-\u03FF'
    r'0-9'
    r'@$%^&*\-+\\=\|/'
    r'\u00A1-\u00FF'
    r'\u2150-\u218F'
    r'\u2700-\u27BF'
)

# CJK followed by ANS -> insert space
_CJK_ANS_RE = re.compile(f'([{_CJK_RANGES}])([{_ANS_CLASS}])')

# ANS followed by CJK -> insert space
_ANS_CJK_RE = re.compile(
    f'([{_ANS_CLASS}~!;:,./?])([{_CJK_RANGES}])'
)


def _apply_cjk_spacing(text: str) -> str:
    """Ensure a single space between CJK and Latin characters.

    Idempotent: will not double-space text that already has correct spacing.

    URLs are exempted: the pangu-style CJK↔Latin spacers would otherwise
    insert a space at every CJK/Latin boundary *inside* a URL (e.g.
    ``kw=测试页面&fr=pb`` → ``kw= 测试页面 &fr=pb``), and the link highlighter —
    which stops at whitespace — would then only colour the fragment up to the
    first inserted space.  Spacing is applied only to the non-URL runs.
    """
    from .text import apply_outside_urls

    if not text:
        return text

    def _space_runs(s: str) -> str:
        s = _CJK_ANS_RE.sub(r'\1 \2', s)
        s = _ANS_CJK_RE.sub(r'\1 \2', s)
        return s

    return apply_outside_urls(text, _space_runs)


# -- public API ------------------------------------------------------------


def compose_ppocr_structures(blocks: list[dict], is_vertical: bool = False) -> list[OcrLine]:
    """Convert PP-OCR detection blocks into ordered OcrLines.

    Three-stage pipeline - geometry, decide, render - so that layout
    decisions (indent level, where blank lines go) are made on clean
    geometry *before* any text mutation, and text is rewritten once at
    the end:

      Stage 1 - geometry + text assembly (no decisions):
        1. Normalize raw blocks (filter empty / zero-size)
        2. Greedy overlap-based clustering -> reading order
        3. Build OcrLine objects from clusters (text = rec + block_separator
           + inline-gap spaces; CJK<->Latin spacing applied)
      Stage 2 - decide (compute, don't mutate text):
        4. Indentation: indent_level per line, baseline on clean lines
        5. Paragraph breaks: which line indices get a blank line after
      Stage 3 - render (mutate text once):
        6. Prepend indent spaces, insert is_blank separator lines

    Stages 4-6 are horizontal-only by design (see note below).  When
    *is_vertical* is True the image contains predominantly vertical CJK
    text (tall boxes, h > w * 1.3); column clustering detects vertical
    columns, reading order is right->left, top->bottom.

    Why decide-then-render: previously paragraph-break *sentinel* OcrLines
    (bounding_box=OcrBox() -> x=0) were inserted before indentation ran, so
    the indent baseline calculation had to exclude them by flag
    (``if not line.paragraph_break``).  Splitting decide from render lets
    the baseline see only real lines - no sentinel exclusion needed.
    """
    # Stage 1 - geometry + text assembly
    normalized = _normalize_blocks(blocks)
    if not normalized:
        return []

    if is_vertical:
        clusters = _greedy_column_cluster(normalized)
    else:
        clusters = _greedy_line_cluster(normalized)

    lines = _build_lines_from_clusters(clusters, is_vertical=is_vertical)
    if not lines:
        return []

    # Stages 2-3 are horizontal-only by design, NOT a TODO to mirror onto
    # vertical.  The horizontal rules work because "gap > one line height =
    # obviously not one continuous block" is an uncontroversial judgment -
    # the gap could fit a whole other line.  That judgment does NOT transfer
    # to vertical: there is no column-based UI analogue to horizontally-
    # separated UI text, and vertical documents (classical texts, calligraphy,
    # vertical Japanese, couplets) have column-spacing conventions that
    # don't map onto horizontal line spacing.  So no gap/width ratio is
    # "obviously a different block" the way > avg_h is for horizontal - any
    # chosen value would be a guess at typography semantics, the very
    # failure mode the horizontal rule avoids.  Adding vertical support
    # requires first measuring real gap/avg_w distributions (must be
    # bimodal) - not mirroring a horizontal constant.
    if not is_vertical:
        # Stage 2 - decide on clean geometry (no sentinel lines present yet)
        _decide_indentation(lines)
        break_after = _decide_paragraph_breaks(lines)
        # Stage 3 - render: mutate text once
        lines = _render_layout(lines, break_after)

    return lines


def _decide_indentation(lines: list[OcrLine]) -> None:
    """Decide each line's ``indent_level`` from left-edge clustering.

    Mutates only ``line.indent_level`` - text is left untouched (rendered
    later by :func:`_render_layout`).  Runs on clean geometry: no blank-line
    separators are present yet, so the baseline needs no sentinel exclusion.

    Baseline is the leftmost box edge (a *position* query - union bbox is
    correct there).  Each line's height denominator is the high-median
    word-box height via :func:`_word_upper_median` (axis ``'h'``), matching
    the paragraph-break ruler: with several boxes on a line their heights
    diverge and the union takes the extreme, which would suppress the
    ratio and miss indents.

    ``indent_ratio = (x - baseline) / line_height``.  When the ratio
    exceeds 1.0 the line is considered intentionally indented; smaller
    offsets are treated as detection jitter.  1.0 deliberately gives up
    1-character indents (ratio ~0.79-1.10x font size): the calibrated
    divisor _BOX_H_TO_FS_RATIO leaves ``h`` ~9 % above real font size, so
    a 1-char offset lands at ~0.72-1.01 -> mostly below the threshold.
    Only multi-character indentation (code blocks, nested lists) is
    detected.  Per-line thresholds handle mixed-height text correctly - a
    tall title with a small absolute offset is not falsely flagged, and a
    short body line with the same offset is properly recognised.

    The indent *unit* is the smallest non-jitter offset from baseline.
    ``level = round(offset / unit)``; rendering applies ``level * 4``
    spaces.  Multi-level indentation (code, nested quotes, outlines) is
    handled without any per-language constants.
    """
    if len(lines) <= 1:
        return

    baseline = min(line.bounding_box.x for line in lines)

    def _is_indented(line: OcrLine) -> bool:
        """Offset exceeds jitter threshold relative to this line's own height."""
        h = _word_upper_median(line, axis="h") / _BOX_H_TO_FS_RATIO
        if h <= 0:
            return False
        indent_ratio = (line.bounding_box.x - baseline) / h
        return indent_ratio > 1.0

    offsets = sorted(set(
        round(line.bounding_box.x) - baseline for line in lines
        if _is_indented(line)
    ))
    if not offsets:
        logger.debug("[DET] _decide_indentation: no offsets > per-line "
                     "threshold -> no indent  (baseline=%.1f  n_lines=%d)",
                     baseline, len(lines))
        return
    unit = offsets[0]

    logger.debug("[DET] _decide_indentation: baseline=%.1f  indent_unit=%d px  "
                 "offsets=%s  n_lines=%d",
                 baseline, unit, offsets, len(lines))

    indented = 0
    for line in lines:
        if not _is_indented(line):
            continue
        offset = round(line.bounding_box.x) - baseline
        line.indent_level = max(round(offset / unit), 1)
        indented += 1
        logger.debug("[DET]   indent L%d: offset=%d px -> level=%d  %r",
                     indented, offset, line.indent_level, line.text[:60])

    if indented:
        logger.debug("[DET] _decide_indentation: %d/%d lines indented", indented, len(lines))


def _render_layout(lines: list[OcrLine], break_after: set[int]) -> list[OcrLine]:
    """Render layout decisions into text: prepend indent spaces and insert
    blank-line separators.  Single mutation pass - text was untouched during
    the decide stage.

    *break_after* holds indices (into the input *lines* list) after which a
    blank separator line should be inserted.
    """
    result: list[OcrLine] = []
    for i, line in enumerate(lines):
        if line.indent_level > 0:
            line.text = ("    " * line.indent_level) + line.text
        result.append(line)
        if i in break_after:
            result.append(OcrLine(text="", bounding_box=OcrBox(), is_blank=True))
    return result


def compose_ppocr_text(blocks: list[dict]) -> str:
    """Compatibility wrapper that returns plain text string."""
    lines = compose_ppocr_structures(blocks)
    return "\n".join(line.text for line in lines).rstrip()


# -- engine singleton ---------------------------------------------------

_engine = None
_engine_lock = threading.Lock()
_active_requests = 0
_active_requests_cv = threading.Condition()
_engine_params_override: dict | None = None

# Crash-storm guard: if the engine crashes _CRASH_LIMIT times within
# _CRASH_WINDOW_S seconds, refuse to recreate until the window expires.
# Persistent failures (corrupt models, ABI mismatch) otherwise loop
# silently forever — create → crash → _engine=None → next OCR tries again.
_engine_crash_times: list[float] = []
_CRASH_LIMIT = 3
_CRASH_WINDOW_S = 60


def set_engine_params_override(params: dict | None):
    """Override engine parameters for the next engine creation.

    Forces release of the existing engine singleton so that the new
    parameters take effect on the next OCR call.  Pass ``None`` to
    revert to production defaults.

    Intended for benchmarking / A/B testing — the production path never
    calls this function.
    """
    global _engine_params_override, _engine
    _engine_params_override = dict(params) if params else None
    with _engine_lock:
        _engine = None
    logger.info("[PPOCR] Engine params override set: %s", _engine_params_override)


def _trim_working_set():
    """Trim the process working set once OCR is done.

    gc.collect() before the OS call was benchmarked and provides no
    additional benefit: SetProcessWorkingSetSize(-1, -1) already
    swaps out every page regardless of Python GC state.
    """
    # Guard against trimming while a recognition request is actively running
    # in another thread. Trimming during active inference causes heavy paging
    # lag (thrashing) as the OS swaps model data back into RAM immediately.
    with _active_requests_cv:
        if _active_requests > 0:
            logger.debug("[PPOCR] Skipping _trim_working_set: %d active requests", _active_requests)
            return

    before_mb = get_working_set_mb()
    logger.debug("[PPOCR] _trim_working_set: before trim  %s", fmt_memory())

    res = trim_working_set()

    after_mb = get_working_set_mb()
    if res:
        logger.debug("[PPOCR] _trim_working_set: after  trim  %s (delta=%.1f MB)",
                     fmt_memory(), after_mb - before_mb)
    else:
        logger.warning("[PPOCR] trim_working_set failed. %s", fmt_memory())


def _acquire_request():
    global _active_requests
    with _active_requests_cv:
        _active_requests += 1


def _release_request():
    global _active_requests
    with _active_requests_cv:
        _active_requests -= 1
        _active_requests_cv.notify_all()


def _purge_stale_crash_times():
    """Remove crash entries older than _CRASH_WINDOW_S."""
    now = time.monotonic()
    cutoff = now - _CRASH_WINDOW_S
    global _engine_crash_times
    _engine_crash_times = [t for t in _engine_crash_times if t > cutoff]


def _record_engine_success():
    """Clear crash history after a successful OCR pass."""
    global _engine_crash_times
    if _engine_crash_times:
        _engine_crash_times.clear()
        logger.info("[PPOCR] Crash storm cleared — engine recovered")


def _get_engine() -> "PPOCR":
    global _engine

    # Crash-storm guard: if the engine has crashed _CRASH_LIMIT times within
    # _CRASH_WINDOW_S, refuse to recreate — persistent failures (corrupt models,
    # ABI mismatch, OOM) would otherwise loop silently forever.
    _purge_stale_crash_times()
    if len(_engine_crash_times) >= _CRASH_LIMIT:
        raise RuntimeError(
            f"PP-OCR engine crashed {len(_engine_crash_times)} times "
            f"in {_CRASH_WINDOW_S}s; refusing to recreate"
        )

    if _engine is None:
        logger.debug("[PPOCR] _get_engine: Initializing new engine instance...")
        with _engine_lock:
            if _engine is None:
                ws_before = get_working_set_mb()
                logger.info("[PPOCR] Initializing engine singleton (models loading)...")

                # Optimized for desktop CPU inference (see module header for details)
                # CLS (direction classifier) disabled by default — only useful
                # for 180° flipped images (e.g. phone held upside-down).
                # Saves ~10% latency + ~6 MB memory with no accuracy impact
                # on correctly-oriented or slightly tilted input.
                params = {
                    "Det.ocr_version": OCRVersion.PPOCRV6,
                    "Det.model_type": ModelType.SMALL,
                    "Rec.ocr_version": OCRVersion.PPOCRV6,
                    "Rec.model_type": ModelType.SMALL,
                    "Global.use_cls": False,
                    **_DEFAULT_ENGINE_PARAMS,
                }
                if _engine_params_override:
                    params.update(_engine_params_override)
                    logger.info("[PPOCR] Applying engine params override: %s",
                                {k: v for k, v in _engine_params_override.items()})
                _engine = PPOCR(params=params)

                ws_after = get_working_set_mb()
                logger.debug(
                    "[PPOCR] Engine created. %s (delta=%.1f MB)",
                    fmt_memory(), ws_after - ws_before,
                )
    return _engine


def release_engine():
    """Release the PP-OCR engine singleton to free memory.

    Waits for in-flight requests, tears down ONNX sessions, then forces
    garbage collection and a working-set trim. The engine is lazily
    re-initialized on the next OCR call.
    """
    global _engine

    ws_entry = get_working_set_mb()
    logger.debug("[PPOCR] release_engine: entry  %s", fmt_memory())

    with _active_requests_cv:
        while _active_requests > 0:
            _active_requests_cv.wait()

        with _engine_lock:
            if _engine is None:
                logger.debug("[PPOCR] release_engine: engine already None, skipping")
                return
            # Let CPython's reference counting clean up ONNX sessions naturally
            _engine = None

    ws_before_trim = get_working_set_mb()
    logger.debug("[PPOCR] release_engine: after del, before trim  %s (delta from entry=%.1f MB)",
                 fmt_memory(), ws_before_trim - ws_entry)

    _trim_working_set()

    ws_exit = get_working_set_mb()
    logger.debug("[PPOCR] release_engine: exit  %s (total delta=%.1f MB)",
                 fmt_memory(), ws_exit - ws_entry)


# -- vertical text ordering -----------------------------------------------

# -- text orientation detection -------------------------------------------

_VERTICAL_BOX_RATIO = 1.5          # h/w threshold for a "tall" (vertical) text box
_VERTICAL_WEIGHTED_THRESHOLD = 0.5  # weighted tall-area fraction to trigger rotation


def _is_vertical_json(json_data: list[dict]) -> bool:
    """Return True if text is predominantly vertical (tall boxes by area).

    Uses area-weighted voting: each box votes for "vertical" or "horizontal"
    in proportion to its pixel area.  Small fragments (stray chars, labels)
    contribute negligible weight (< 1 % of a real text column), so no
    explicit noise-filter threshold is needed — the weighting is inherently
    robust to outliers.

    This replaces the earlier ad-hoc approach of filtering by a percentage
    of the largest box's area (5 %) with an absolute floor (500 px²), which
    required tuning per image resolution.
    """
    if not json_data:
        return False

    total_area = 0.0
    tall_area = 0.0
    for item in json_data:
        box = item.get("box", [])
        if not box:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if w <= 0:
            continue
        area = w * h
        total_area += area
        if h > w * _VERTICAL_BOX_RATIO:
            tall_area += area

    if total_area == 0.0:
        return False
    ratio = tall_area / total_area
    is_vert = ratio >= _VERTICAL_WEIGHTED_THRESHOLD
    logger.debug("[DET] _is_vertical_json: tall_area_ratio=%.2f (threshold=%.2f) "
                 "total_area=%d → vertical=%s",
                 ratio, _VERTICAL_WEIGHTED_THRESHOLD, int(total_area), is_vert)
    return is_vert


# -- public API --------------------------------------------------------

def _recognize_without_detection(engine, arr) -> OcrRecognition:
    """Fallback: skip text detection and run recognition on the whole image.

    This fires when the detector finds zero text regions.  Direct-feed the
    entire image to the recognizer (use_det=False, use_cls=False), matching
    upstream rapidocr rec-only.  No content crop, no contrast normalize.

    The recognizer resizes any input to a fixed 48 px height (CRNN input
    shape [3, 48, <=320]), so it can only read text that fills most of the
    original image height -- typically single-line short phrases.  Multi-line,
    wide-format desktop captures are already illegible at 48 px even clean.

    Cost is constant ~5-9 ms regardless of image size (always the same
    48 px feature map); ~1 % of a typical det+rec call.

    Narrow insurance: degrades gracefully on low-quality single-line small
    images where the detector drops out.  Don't expect it to recover
    multi-line desktop screenshots.
    """
    from ..constants import OCR_ENGINE_PPOCR

    orig_det = engine.use_det
    orig_cls = engine.use_cls
    try:
        _acquire_request()
        try:
            rec_result = engine(arr, use_det=False, use_cls=False)
        finally:
            _release_request()

        txts = getattr(rec_result, "txts", None)
        if not txts or not txts[0] or not txts[0].strip():
            logger.debug("PP-OCR recognition-only fallback returned no text")
            return OcrRecognition(
                engine_type=OCR_ENGINE_PPOCR,
            )

        recognized_text = txts[0].strip()
        logger.debug("PP-OCR recognition-only fallback succeeded: %r", recognized_text)

        return OcrRecognition(
            text=recognized_text,
            engine_type=OCR_ENGINE_PPOCR,
        )
    finally:
        engine.use_det = orig_det
        engine.use_cls = orig_cls


def recognize_ppocr_qimage(image_or_result, language_tag: str = "") -> OcrRecognition:
    from .preprocess import OcrPreprocessResult
    from ..constants import OCR_ENGINE_PPOCR

    if isinstance(image_or_result, OcrPreprocessResult):
        image = image_or_result.image
        original_size = image_or_result.original_size
    elif isinstance(image_or_result, QtGui.QImage):
        image = image_or_result
        original_size = image.size()
    else:
        # Handle QPixmap or other types (fallback to manual conversion)
        from .preprocess import prepare_ocr_image
        image = prepare_ocr_image(image_or_result)
        original_size = image.size()

    if image.isNull():
        return OcrRecognition(engine_type=OCR_ENGINE_PPOCR)

    # Defense: the buffer math below assumes devicePixelRatio == 1.0.
    # image.width()/height() return *logical* dimensions, while image.bits()
    # is the *physical* pixel buffer — at DPR 2.0 a 960x540 logical QImage
    # has a 1920x1080 buffer, and the reshape((height, width, 4)) would read
    # only a quarter of it, silently corrupting OCR. The normal pipeline
    # (run_minimal_pipeline → prepare_ocr_image) normalizes DPR to 1.0, but
    # this is a public entry point; a caller passing a raw high-DPR QImage
    # would hit the mismatch. Normalize here when needed. convertToFormat is
    # only called in the off-normal case, so the happy path pays no copy.
    if image.devicePixelRatio() != 1.0:
        image = image.convertToFormat(QtGui.QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(1.0)
        original_size = image.size()

    # Pre-declare to ensure cleanup in 'finally' doesn't fail
    result = None
    json_data = None
    arr = None
    bgr_image = None
    
    try:
        logger.debug("[ANCHOR] IMAGE_CONVERT_START")
        # Callers must pass a QImage whose format stores [B, G, R] in the
        # first three bytes on this platform (RGB32 / ARGB32 / ARGB32_PM on
        # little-endian).  prepare_ocr_image() in preprocess.py guarantees
        # RGB32, and ARGB32 is byte-identical for the first 3 channels.
        # convertToFormat is omitted because it would add a redundant copy
        # (~1.3 ms) with no effect on the BGR slice below.
        width = image.width()
        height = image.height()
        ptr = image.bits()
        ptr.setsize(image.sizeInBytes())
        # [:, :, :3] drops the X/A channel; .copy() makes it contiguous for ONNX.
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4))[:, :, :3].copy()
        logger.debug("[ANCHOR] IMAGE_CONVERT_END")

        _acquire_request()
        try:
            engine = _get_engine()
            logger.debug("[ANCHOR] INFERENCE_START")
            result = engine(arr)
            logger.debug("[ANCHOR] INFERENCE_END")
            if hasattr(result, "elapse_list"):
                logger.debug("[ANCHOR] ELAPSE_DETAIL: %s", result.elapse_list)
            json_data = result.to_json()
            # DET debug: raw detection summary
            n_raw = len(json_data) if json_data else 0
            n_valid_raw = 0
            if json_data:
                for item in json_data:
                    box = item.get("box", [])
                    txt = item.get("txt", "") or ""
                    if box and txt.strip():
                        left, top, right, bottom = ppocr_box_to_bbox(box)
                        if right > left and bottom > top:
                            n_valid_raw += 1
            logger.debug("[DET] engine returned %d raw blocks (%d with valid box+text)",
                         n_raw, n_valid_raw)
            if json_data and n_valid_raw < n_raw:
                logger.debug("[DET] %d/%d blocks have invalid/missing box → will be filtered",
                             n_raw - n_valid_raw, n_raw)
        finally:
            _release_request()

        # ── vertical CJK layout ──────────────────────────────────────────
        # PP-OCRv6 SMALL handles upright CJK characters in vertical columns
        # natively — rotating 90° CCW is counterproductive (empirically:
        # rotation introduces garbage detections, merges short text, and
        # loses 5–20 % of CJK characters).  Instead, detect vertical text
        # with the area-weighted tall-box heuristic and route through a
        # vertical-aware line builder that respects column boundaries.
        is_vertical = _is_vertical_json(json_data) if json_data else False
        if is_vertical:
            logger.debug("PP-OCR: vertical CJK text detected — using vertical layout")
        else:
            logger.debug("[DET] layout direction: horizontal (vertical not detected)")

        if not json_data:
            logger.debug("[DET] FALLBACK TRIGGERED: json_data empty → _recognize_without_detection()")
            logger.debug("[DET]   reason: PP-OCR detector found zero text regions")

            if width > original_size.width() or height > original_size.height():
                y_off = (height - original_size.height()) // 2
                x_off = (width - original_size.width()) // 2
                fallback_arr = arr[y_off : y_off + original_size.height(),
                                   x_off : x_off + original_size.width()].copy()
            else:
                fallback_arr = arr

            final_res = _recognize_without_detection(engine, fallback_arr)
            return final_res

        blocks = [{"text": item["txt"], "box": item["box"]} for item in json_data]
        lines = compose_ppocr_structures(blocks, is_vertical=is_vertical)
        text = "\n".join(line.text for line in lines).rstrip()

        logger.debug("[DET] compose_ppocr_structures → %d OcrLines, %d chars total",
                     len(lines), len(text))
        for i, ln in enumerate(lines):
            b = ln.bounding_box
            logger.debug("[DET]   L%d: (%d,%d %dx%d) text=%r",
                         i, int(b.x), int(b.y), int(b.width), int(b.height),
                         ln.text[:80])

        _record_engine_success()

        return OcrRecognition(
            text=text,
            lines=lines,
            engine_type=OCR_ENGINE_PPOCR,
        )
    except Exception:
        logger.exception("PP-OCR engine call failed — discarding engine for recovery")
        _engine_crash_times.append(time.monotonic())
        _purge_stale_crash_times()
        with _engine_lock:
            _engine = None
        import gc
        gc.collect()  # force ORT session destructor → VirtualFree
        return OcrRecognition(engine_type=OCR_ENGINE_PPOCR)
    finally:
        # Explicit GC: ONNX Runtime allocates large native buffers whose
        # lifetime Python ref-counting cannot fully track.  Without GC,
        # repeated OCR calls leak private bytes and kernel handles within
        # a handful of iterations.
        #
        # _trim_working_set() is deliberately NOT called here — trimming
        # while OCR is still active would thrash (swap out model pages
        # only to fault them back on the next call).  Trim fires 30 s
        # after the last OCR request completes (OcrController._trim_timer).
        import gc
        del result, json_data, arr, bgr_image
        gc.collect()



def recognize_ppocr_result_from_pixmap(
    image_or_result,
    language_tag: str = "",
) -> OcrRecognition:
    """PP-OCR engine entry point. Receives a preprocessed QImage or OcrPreprocessResult."""
    if isinstance(image_or_result, QtGui.QImage):
        if image_or_result.isNull():
            return OcrRecognition()
    elif isinstance(image_or_result, OcrPreprocessResult):
        if image_or_result.image.isNull():
            return OcrRecognition()

    return recognize_ppocr_qimage(image_or_result, language_tag=language_tag)


def warmup_ppocr():
    """Pre-initialize the PP-OCR engine singleton to avoid cold-start latency."""
    ws_before = get_working_set_mb()
    t0 = time.perf_counter()
    logger.debug("[PPOCR] warmup_ppocr: start  %s", fmt_memory())
    try:
        _get_engine()
        elapsed = (time.perf_counter() - t0) * 1000
        ws_after = get_working_set_mb()
        logger.debug(
            "[PPOCR] warmup_ppocr: done  %s (delta=%.1f MB, took %.1fms)",
            fmt_memory(), ws_after - ws_before, elapsed,
        )
    except Exception:
        logger.exception("PP-OCR engine warmup failed")


# Register PP-OCR engine
from .engine import register_engine  # noqa: E402
from ..constants import OCR_ENGINE_PPOCR  # noqa: E402
register_engine(
    OCR_ENGINE_PPOCR,
    recognize=recognize_ppocr_result_from_pixmap,
    release=release_engine,
    trim=_trim_working_set,
    warmup=warmup_ppocr,
    metadata={
        "display_name": "PP-OCR",
        "error_prefixes": [],
    },
)
