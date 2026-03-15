"""
HushSnap 日志配置模块
负责初始化应用程序的全局日志系统，支持文件轮转和通过环境变量动态调整日志级别。
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 用于控制日志级别的环境变量名 (例如在 PS 中执行: $env:HUSHSNAP_LOG_LEVEL = "DEBUG")
LOG_LEVEL_ENV = "HUSHSNAP_LOG_LEVEL"
# 默认日志级别
DEFAULT_LEVEL = logging.INFO

def setup_logging(log_file_path: Path):
    """
    初始化全局日志系统。
    配置包括：
    - 自动文件轮转：单个文件最大 5MB，保留 1 个备份。
    - 动态日志等级：优先从环境变量读取，默认为 INFO。
    - 格式化输出：包含时间戳、级别、模块名和行号。
    
    Args:
        log_file_path (Path): 日志文件的完整路径。
    """
    # 1. 确定日志级别
    level_str = os.environ.get(LOG_LEVEL_ENV, "INFO").upper().strip()
    # 动态获取 logging 模块对应的级别常量 (DEBUG, INFO, etc.)
    level = getattr(logging, level_str, DEFAULT_LEVEL)
    if not isinstance(level, int):
        level = DEFAULT_LEVEL

    # 2. 定义日志格式化器
    # 格式: [时间] [级别] [模块名:行号] 消息内容
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 3. 配置文件处理器 (支持大文件自动分片/轮转)
    try:
        # 确保日志目录存在
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # RotatingFileHandler: 超过 maxBytes 后自动重命名并创建新文件
        file_handler = RotatingFileHandler(
            log_file_path, 
            maxBytes=5*1024*1024, # 5MB
            backupCount=1,        # 保留 1 个历史备份
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)

        # 4. 配置根 Logger (Root Logger)
        root = logging.getLogger()
        root.setLevel(level)
        
        # 清除已有的 Handler 防止重复输出（常见于单元测试或重载环境）
        if root.hasHandlers():
            root.handlers.clear()
        
        # 将文件处理器添加到根记录器
        root.addHandler(file_handler)
        
        logging.info(f"Logging initialized. Level: {logging.getLevelName(level)}, Path: {log_file_path}")
    except Exception as e:
        # 如果日志初始化失败，回退到标准输出打印错误，避免程序崩溃
        print(f"Failed to setup file logging: {e}")

def get_logger(name: str):
    """
    获取指定名称（通常是模块名）的 Logger 实例。
    
    Args:
        name (str): 模块名称，通常传入 __name__。
        
    Returns:
        logging.Logger: 配置好的记录器对象。
    """
    return logging.getLogger(name)
