"""
Unit tests for the logging configuration.
Verifies that loggers and handlers are correctly initialized.
"""

import pytest
import logging
from hushsnap.logging_config import setup_logging, get_logger

@pytest.fixture
def temp_log_file(tmp_path):
    """Fixture providing a temporary log file path."""
    return tmp_path / "test.log"

def test_setup_logging_basic(temp_log_file):
    """Test the basic logging setup with file and stream handlers."""
    # Ensure root logger is clean
    root = logging.getLogger()
    root.handlers.clear()
    
    setup_logging(temp_log_file)
    
    assert temp_log_file.exists()
    assert len(root.handlers) == 2
    assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    assert root.level == logging.INFO

def test_setup_logging_custom_level(temp_log_file):
    """Test the logging setup with a custom log level."""
    root = logging.getLogger()
    root.handlers.clear()

    setup_logging(temp_log_file, force_level=logging.DEBUG)

    assert root.level == logging.DEBUG

def test_get_logger():
    """Verify that get_logger returns a properly named logger instance."""
    logger = get_logger("test_module")
    assert logger.name == "test_module"
    assert isinstance(logger, logging.Logger)
