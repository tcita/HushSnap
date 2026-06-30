"""Log tailing, [OCR_CHAIN] marker parsing, and the per-round bench record.

hushsnap.log's asctime is second-resolution (logging_config datefmt lacks
msec), so a 50-300ms inference's begin/end markers share the same second and
a log-timestamp delta is useless. Instead the stress test stamps
``time.perf_counter()`` at the moment it FIRST observes each marker. Accuracy
is bounded by the tail poll interval (±poll_interval), reported in the
aggregate. This needs ZERO source changes to the MSIX app — it works purely
off the existing [OCR_CHAIN] markers added across hotkey→capture→ocr_service→
popup.
"""

import os
import re
import time
from pathlib import Path

CHAIN = "[OCR_CHAIN]"
SUCCESS_MARKER = "[OCR_CHAIN] show_text done"


# ── log path auto-detection ───────────────────────────────────────────────────

def autodetect_log_path():
    """Find hushsnap.log under the MSIX package data folder.

    Since bc044de (redirect packaged user data to package LocalState), the
    packaged app writes its log to
    %LOCALAPPDATA%\\Packages\\<PackageFamilyName>\\LocalState\\hushsnap.log.
    Earlier builds used LocalCache\\Local\\HushSnap\\ — still checked for
    back-compat. We glob under Packages\\*HushSnap* so the exact PFN (with
    publisher hash) need not be known ahead of time.

    Falls back to unpackaged/dev paths: %LOCALAPPDATA%\\HushSnap and
    %LOCALAPPDATA%\\HushSnap_Dev.
    """
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    candidates = []
    pkg_root = Path(local) / "Packages"
    if pkg_root.exists():
        # Prefer the current LocalState layout; keep the legacy LocalCache
        # path as a fallback so older installs still autodetect.
        candidates.extend(pkg_root.glob("*HushSnap*/LocalState/hushsnap.log"))
        candidates.extend(pkg_root.glob("*HushSnap*/LocalCache/Local/HushSnap/hushsnap.log"))
    candidates.append(Path(local) / "HushSnap" / "hushsnap.log")      # unpackaged
    candidates.append(Path(local) / "HushSnap_Dev" / "hushsnap.log")  # dev run
    for c in candidates:
        if c.exists():
            return c
    return None


# ── log tailing ───────────────────────────────────────────────────────────────

def read_new_lines(log_path, offset):
    """Return (new_text, new_offset) for bytes added since offset."""
    try:
        size = log_path.stat().st_size
    except OSError:
        return "", offset
    if size < offset:
        # log was rotated/truncated — start from the beginning
        offset = 0
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            text = f.read()
            new_offset = f.tell()
    except OSError:
        return "", offset
    return text, new_offset


# ── marker classification ─────────────────────────────────────────────────────

_SEQ_RE = re.compile(r"seq=(\d+)")

# Map a marker message (the text after "[OCR_CHAIN] ") to a stage key. The
# engine begin/end markers carry no seq; in wait mode only one request is
# in-flight at a time, so they unambiguously belong to the current round.
_MARKER_STAGES = [
    ("recognize_async, seq=",            "async"),
    ("worker picked up, seq=",           "pickup"),
    ("recognize() engine call begin",    "eng_begin"),
    ("recognize() engine call end",      "eng_end"),
    ("worker recognize done, seq=",      "done"),
    ("worker callback emitted, seq=",    "callback"),
    ("start_request",                    "start_request"),
    ("show_text done",                   "show_done"),
]


def classify_marker(msg):
    """Return (stage_key, seq) for a marker message, or (None, None).

    `msg` is the full line with the [OCR_CHAIN] prefix stripped (as produced by
    `line.split(CHAIN, 1)[1].strip()`).
    """
    for needle, key in _MARKER_STAGES:
        if msg.startswith(needle):
            m = _SEQ_RE.search(msg)
            return key, (int(m.group(1)) if m else None)
    return None, None


# ── per-round bench record ────────────────────────────────────────────────────

class RoundBench:
    """Per-round benchmark measurements collected entirely out-of-process.

    All timing fields are in milliseconds; -1 means the marker pair needed to
    compute that segment was never observed (e.g. a crash mid-stage, or a
    DEBUG-only marker absent from INFO-level logs).
    """

    __slots__ = ("round_idx", "status", "engine_ms", "e2e_ms", "pickup_ms",
                 "callback_ms", "peak_ws_mb", "peak_pv_mb", "ws_after_mb",
                 "pf_delta", "h_delta", "retention", "text_len", "seq",
                 "detail", "last_marker")

    def __init__(self, round_idx, status):
        self.round_idx = round_idx
        self.status = status
        self.engine_ms = -1.0      # eng_begin → eng_end (pure ONNX inference; INFO)
        self.e2e_ms = -1.0         # eng_begin → show_done (inference-start → UI landed; INFO)
        self.pickup_ms = -1.0      # async → pickup (schedule latency; DEBUG-only → -1 at INFO)
        self.callback_ms = -1.0    # eng_end → callback (inference-end → callback emit; INFO)
        self.peak_ws_mb = -1.0
        self.peak_pv_mb = -1.0
        self.ws_after_mb = -1.0
        self.pf_delta = -1
        self.h_delta = -1
        self.retention = -1.0
        self.text_len = -1
        self.seq = None
        self.detail = ""        # human-readable outcome (e.g. crash reason + last marker)
        self.last_marker = ""   # the last [OCR_CHAIN] marker observed before resolve
                                # — pinpoints the pipeline stage a crash halted in


def finalize_bench(rb, stamps, sampler, ws0, pv0, h0, pf0, peak_ws, peak_pv):
    """Compute per-stage deltas and memory deltas for a finished round.

    Called once the round resolves (ok / crash / hang). Timing segments that
    never had both endpoints observed stay -1. Memory deltas use the snapshot
    taken just before the round vs. just after resolution.
    """
    def _delta(a, b):
        if a in stamps and b in stamps:
            return (stamps[b] - stamps[a]) * 1000.0
        return -1.0

    # ── Stage pairings (chosen so every segment is computable at INFO log
    # level — the DEBUG-only markers `start_request`, `recognize_async`,
    # `worker picked up`, `worker recognize done` are absent from INFO logs,
    # so any pairing that needs them yields -1 in production MSIX runs).
    #
    #   engine_ms   : eng_begin → eng_end        (pure ONNX inference; both INFO)
    #   e2e_ms      : eng_begin → show_done      (inference-start → UI landed;
    #                  both INFO. Drops the hotkey→capture→schedule prefix,
    #                  which needs the DEBUG `start_request` marker.)
    #   callback_ms : eng_end → callback         (inference-end → callback
    #                  emitted, incl. text composition; both INFO. The
    #                  tighter `done → callback` pairing needs the DEBUG
    #                  `worker recognize done` marker.)
    #   pickup_ms   : async → pickup             (schedule latency; BOTH DEBUG
    #                  → always -1 at INFO level. Kept for DEBUG-log runs.)
    rb.engine_ms = _delta("eng_begin", "eng_end")
    rb.e2e_ms = _delta("eng_begin", "show_done")
    rb.pickup_ms = _delta("async", "pickup")
    rb.callback_ms = _delta("eng_end", "callback")

    ws_a, pv_a, h_a, pf_a = sampler.snapshot()
    rb.peak_ws_mb = peak_ws if peak_ws >= 0 else -1.0
    rb.peak_pv_mb = peak_pv if peak_pv >= 0 else -1.0
    rb.ws_after_mb = ws_a
    if pf0 >= 0 and pf_a >= 0:
        rb.pf_delta = pf_a - pf0
    if h0 >= 0 and h_a >= 0:
        rb.h_delta = h_a - h0
    if peak_ws > 0 and ws_a >= 0:
        rb.retention = ws_a / peak_ws
