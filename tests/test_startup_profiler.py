from unittest.mock import MagicMock

from hushsnap.startup_profiler import StartupProfiler


def test_log_elapsed_is_noop_when_detailed_logging_disabled():
    logger = MagicMock()
    profiler = StartupProfiler(logger, overall_start=0.0, detailed_enabled=False)

    profiler.log_elapsed("STEP X")

    logger.debug.assert_not_called()


def test_step_is_noop_when_detailed_logging_disabled():
    logger = MagicMock()
    profiler = StartupProfiler(logger, overall_start=0.0, detailed_enabled=False)

    with profiler.step("STEP X"):
        pass

    logger.debug.assert_not_called()


def test_step_logs_when_detailed_logging_enabled():
    logger = MagicMock()
    profiler = StartupProfiler(logger, overall_start=0.0, detailed_enabled=True)

    with profiler.step("STEP X"):
        pass

    logger.debug.assert_called_once()
