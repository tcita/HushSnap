"""Helpers for startup performance logging (all timings in milliseconds)."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

_SEC_TO_MS = 1000


class StartupProfiler:
    """Track startup timing without cluttering the main boot flow."""

    def __init__(
        self,
        logger: logging.Logger,
        overall_start: float,
        boot_start_time=None,
        detailed_enabled: bool = False,
    ):
        self.logger = logger
        self.overall_start = overall_start
        self.boot_start_time = boot_start_time
        self.detailed_enabled = detailed_enabled

    def log_header(self) -> None:
        """Emit the startup audit header."""
        boot_duration = (
            (self.overall_start - self.boot_start_time) * _SEC_TO_MS
            if self.boot_start_time
            else 0.0
        )
        self.logger.info("--- STARTUP PERFORMANCE AUDIT ---")
        self.logger.info(f"OS/Import overhead: {boot_duration:.1f}ms")

    def log_elapsed(self, label: str) -> None:
        """Log elapsed time since entering main()."""
        if not self.detailed_enabled:
            return
        elapsed = (time.perf_counter() - self.overall_start) * _SEC_TO_MS
        self.logger.debug(f"{label}. Elapsed inside main: {elapsed:.1f}ms")

    @contextmanager
    def step(self, label: str) -> Iterator[None]:
        """Measure and log the duration of a startup step."""
        if not self.detailed_enabled:
            yield
            return

        step_start = time.perf_counter()
        try:
            yield
        finally:
            duration = (time.perf_counter() - step_start) * _SEC_TO_MS
            self.logger.debug(f"{label}. Duration: {duration:.1f}ms")

    def log_summary(self) -> None:
        """Emit final startup timing summary."""
        logic_init_duration = (time.perf_counter() - self.overall_start) * _SEC_TO_MS
        total_wall_time = (
            (time.perf_counter() - self.boot_start_time) * _SEC_TO_MS
            if self.boot_start_time
            else logic_init_duration
        )
        self.logger.info(f"Application logic init: {logic_init_duration:.1f}ms")
        self.logger.info(
            f"Initialization complete. Total wall-clock startup time: {total_wall_time:.1f}ms"
        )
