"""Shared helpers for the OCR stress-test script.

Split out of ``scripts/stress_test_ocr.py`` so the entry point stays a thin
orchestrator. Modules:

  win32_input      — synthesized keyboard/mouse + monitor enumeration (ctypes)
  process_sampler  — out-of-process memory/handle queries against HushSnap.exe
  log_markers      — log tailing, [OCR_CHAIN] marker parsing, RoundBench record
  reporting        — per-round / aggregate bench printing + JSON/CSV persistence

All modules are pure-stdlib + ctypes (no pyautogui / psutil / pywin32) so the
stress test runs on any Windows Python.
"""
