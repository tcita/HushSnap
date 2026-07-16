"""Pytest fixtures for OCR layout integration tests.

Provides session-scoped ``engine`` and ``browser`` fixtures so the
expensive initialisation happens once per test run.

Usage::

    def test_my_case(engine, browser):
        ...
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def engine():
    """PP-OCR engine singleton — initialised once per session."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    from .pipeline import get_engine
    eng = get_engine()
    yield eng
    from .pipeline import release_engine
    release_engine()


@pytest.fixture(scope="session")
def browser():
    """Playwright Chromium browser — launched once per session."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="function")
def page(browser):
    """Fresh browser page for each test."""
    ctx = browser.new_context(device_scale_factor=1)
    pg = ctx.new_page()
    yield pg
    pg.close()
    ctx.close()
