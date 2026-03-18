import pytest
import logging
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from hushsnap.logging_config import setup_logging, get_logger

@pytest.fixture
def temp_log_file(tmp_path):
    return tmp_path / "test.log"

def test_setup_logging_basic(temp_log_file):
    # Ensure root logger is clean
    root = logging.getLogger()
    root.handlers.clear()
    
    setup_logging(temp_log_file)
    
    assert temp_log_file.exists()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], logging.handlers.RotatingFileHandler)
    assert root.level == logging.INFO

def test_setup_logging_custom_level(temp_log_file):
    with patch.dict(os.environ, {"HUSHSNAP_LOG_LEVEL": "DEBUG"}):
        root = logging.getLogger()
        root.handlers.clear()
        
        setup_logging(temp_log_file)
        
        assert root.level == logging.DEBUG

def test_get_logger():
    logger = get_logger("test_module")
    assert logger.name == "test_module"
    assert isinstance(logger, logging.Logger)
