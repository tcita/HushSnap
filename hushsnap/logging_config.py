import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

#debug: $env:HUSHSNAP_LOG_LEVEL = "DEBUG" 
LOG_LEVEL_ENV = "HUSHSNAP_LOG_LEVEL"
DEFAULT_LEVEL = logging.INFO

def setup_logging(log_file_path: Path):
    """
    初始化全局日志系统。
    - 自动轮转：最大 5MB，保留 1 个备份。
    - 动态等级：读取 HUSHSNAP_LOG_LEVEL (DEBUG, INFO, WARNING, ERROR)，默认为 INFO。
    """
    # 1. 确定日志级别
    level_str = os.environ.get(LOG_LEVEL_ENV, "INFO").upper().strip()
    # 尝试映射为标准级别数值，非法则回退
    level = getattr(logging, level_str, DEFAULT_LEVEL)
    if not isinstance(level, int):
        level = DEFAULT_LEVEL

    # 2. 创建格式化器
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 3. 配置文件处理器 (带轮转)
    try:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file_path, maxBytes=5*1024*1024, backupCount=1, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)

        # 4. 配置根 Logger
        root = logging.getLogger()
        root.setLevel(level)
        if root.hasHandlers():
            root.handlers.clear()
        root.addHandler(file_handler)
        
        logging.info(f"Logging initialized. Level: {logging.getLevelName(level)}, Path: {log_file_path}")
    except Exception as e:
        print(f"Failed to setup file logging: {e}")

def get_logger(name: str):
    """获取指定模块的 logger。"""
    return logging.getLogger(name)
