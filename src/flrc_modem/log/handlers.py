"""非阻塞日志处理器：通过 QueueHandler + QueueListener 隔离 I/O 与实时 SDR/异步处理。"""
from __future__ import annotations

import atexit
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
from typing import TextIO

from .formatters import ConsoleColoredFormatter, ContextFilter, JsonLinesFormatter

_LOG_QUEUE: queue.SimpleQueue | None = None
_LISTENER: QueueListener | None = None
_MANAGED_HANDLERS: list[logging.Handler] = []
_INITIALIZED = False


def build_console_handler(
    stream: TextIO | None = None,
    output_format: str = "text",
    level: int = logging.INFO,
) -> logging.Handler:
    """构建控制台日志处理器，默认定向至 sys.stderr。"""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(level)
    handler.addFilter(ContextFilter())

    if output_format == "json":
        handler.setFormatter(JsonLinesFormatter())
    else:
        handler.setFormatter(ConsoleColoredFormatter())

    return handler


def build_file_handler(
    file_path: str | Path,
    output_format: str = "json",
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Handler:
    """构建支持轮转的磁盘日志文件处理器。"""
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        filename=str(path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.addFilter(ContextFilter())

    if output_format == "json":
        handler.setFormatter(JsonLinesFormatter())
    else:
        handler.setFormatter(ConsoleColoredFormatter(use_colors=False))

    return handler


def register_managed_handlers(handlers: list[logging.Handler]) -> None:
    """注册托管的处理器，以便在 shutdown 时统一关闭文件句柄。"""
    global _MANAGED_HANDLERS
    _MANAGED_HANDLERS.extend(handlers)


def start_nonblocking_listener(
    root_logger: logging.Logger,
    handlers: list[logging.Handler],
    extra_loggers: list[logging.Logger] | None = None,
) -> QueueListener:
    """启动全局非阻塞日志队列与后台监听线程。"""
    global _LOG_QUEUE, _LISTENER, _INITIALIZED, _MANAGED_HANDLERS

    # 如果先前存在监听器，先安全停止
    shutdown_logging()

    _LOG_QUEUE = queue.SimpleQueue()
    queue_handler = QueueHandler(_LOG_QUEUE)
    queue_handler.setLevel(logging.NOTSET)
    queue_handler.addFilter(ContextFilter())

    # 将 QueueHandler 挂载至 root_logger 及额外 logger
    root_logger.handlers.clear()
    root_logger.addHandler(queue_handler)

    for extra in extra_loggers or []:
        extra.handlers.clear()
        extra.addHandler(queue_handler)
        extra.setLevel(root_logger.level)
        extra.addFilter(ContextFilter())

    _MANAGED_HANDLERS = list(handlers)
    _LISTENER = QueueListener(_LOG_QUEUE, *handlers, respect_handler_level=True)
    _LISTENER.start()
    _INITIALIZED = True

    return _LISTENER


def shutdown_logging() -> None:
    """安全停止后台日志监听线程并刷新关闭全部托管 Handler。"""
    global _LISTENER, _INITIALIZED, _MANAGED_HANDLERS
    if _LISTENER is not None:
        try:
            _LISTENER.stop()
        except Exception:
            pass
        _LISTENER = None

    for h in _MANAGED_HANDLERS:
        try:
            h.flush()
            h.close()
        except Exception:
            pass
    _MANAGED_HANDLERS.clear()

    # 清理 root_logger 上的 handler
    root_logger = logging.getLogger("flrc_modem")
    for h in list(root_logger.handlers):
        try:
            h.flush()
            h.close()
        except Exception:
            pass
    root_logger.handlers.clear()

    # 安全关闭底层崩溃日志流 (faulthandler)
    try:
        from .crash import disable_native_fault_monitor
        disable_native_fault_monitor()
    except Exception:
        pass

    _INITIALIZED = False


# 注册进程退出清理钩子
atexit.register(shutdown_logging)
