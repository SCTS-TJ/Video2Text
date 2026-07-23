"""Video2Text 集中式日志配置。

日志路径: /Volumes/Studio_IT_Dev/Video2Text/
├── video2text.log   # 主日志 (INFO+, 按日轮转, 保留30天)
├── error.log        # 错误日志 (WARNING+, 按日轮转, 保留30天)
└── uvicorn.log      # Uvicorn HTTP 访问日志 (由 run.sh 定向)

用法:
    from ingestion.logger import get_logger
    log = get_logger(__name__)
    log.info("任务启动: task_id=%s", task_id)
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = "/Volumes/Studio_IT_Dev/Video2Text"
os.makedirs(LOG_DIR, exist_ok=True)

# ---- 格式 ----
_DETAIL_FMT = logging.Formatter(
    "[%(asctime)s] [%(levelname)-7s] [%(name)s:%(funcName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_SIMPLE_FMT = logging.Formatter(
    "[%(asctime)s] [%(levelname)-7s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _build_handler(path: str, level: int, formatter: logging.Formatter) -> TimedRotatingFileHandler:
    """按日轮转文件处理器, 保留 30 天备份。"""
    h = TimedRotatingFileHandler(path, when="midnight", interval=1, backupCount=30, encoding="utf-8")
    h.setLevel(level)
    h.setFormatter(formatter)
    return h


def _setup_root_logger() -> None:
    """全局配置一次。"""
    root = logging.getLogger()
    if root.handlers:
        return  # 避免重复配置

    root.setLevel(logging.DEBUG)

    # 主日志文件: INFO+
    main_path = os.path.join(LOG_DIR, "video2text.log")
    root.addHandler(_build_handler(main_path, logging.INFO, _DETAIL_FMT))

    # 错误日志文件: WARNING+
    err_path = os.path.join(LOG_DIR, "error.log")
    root.addHandler(_build_handler(err_path, logging.WARNING, _DETAIL_FMT))

    # 控制台输出 (开发时可见)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    console.setFormatter(_SIMPLE_FMT)
    root.addHandler(console)

    root.info("══════════════════════════════════════════")
    root.info("  Video2Text 日志系统初始化完成")
    root.info("  主日志: %s", main_path)
    root.info("  错误日志: %s", err_path)
    root.info("══════════════════════════════════════════")


# 模块导入时自动初始化
_setup_root_logger()


def get_logger(name: str) -> logging.Logger:
    """获取 module-level logger。

    用法:
        log = get_logger(__name__)
        log.info("消息")
    """
    return logging.getLogger(name)
