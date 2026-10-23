"""FLRC Modem 模块化日志与可观测性系统。

提供统一的非阻塞日志分发、高维度宽事件（Wide Events）支持与跨协程/子进程追踪上下文。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
from typing import TextIO

from .context import (
    bind_context,
    clear_context,
    get_current_context,
    init_context_from_env,
    set_context_var,
)
from .formatters import ConsoleColoredFormatter, ContextFilter, JsonLinesFormatter
from .handlers import (
    build_console_handler,
    build_file_handler,
    shutdown_logging,
    start_nonblocking_listener,
)
from .wide import WideEvent, wide_event

__all__ = [
    "setup_logging",
    "get_logger",
    "shutdown_logging",
    "bind_context",
    "get_current_context",
    "clear_context",
    "set_context_var",
    "WideEvent",
    "wide_event",
    "ConsoleColoredFormatter",
    "JsonLinesFormatter",
    "ContextFilter",
    "get_default_log_dir",
    "install_crash_guards",
]

from .crash import install_crash_guards


def get_default_log_dir() -> Path:
    """获取默认日志存储目录 (优先项目根目录 logs/，回退到当前工作目录 logs/)。"""
    repo_root = Path(__file__).resolve().parents[3]
    if (repo_root / "pyproject.toml").exists():
        log_dir = repo_root / "logs"
    else:
        log_dir = Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _resolve_target_file(
    file_path: str | Path | bool | None,
    default_filename: str = "flrc.log",
) -> Path | None:
    """解析实际目标日志文件路径；支持显式禁用或环境变量控制。"""
    # 显式禁止落盘 (如 file_path=False)
    if file_path is False:
        return None

    # 显式指定路径
    if isinstance(file_path, (str, Path)):
        return Path(file_path)

    # 环境变量控制
    env_file = os.environ.get("FLRC_LOG_FILE", "").strip()
    if env_file:
        if env_file.lower() in ("none", "false", "0", "off", "disable"):
            return None
        return Path(env_file)

    # 默认开启落盘至 logs/
    return get_default_log_dir() / default_filename


def _resolve_log_level(level: str | int | None, quiet: bool = False) -> int:
    if quiet:
        return logging.WARNING
    if level is not None:
        if isinstance(level, int):
            return level
        if isinstance(level, str):
            level_str = level.upper().strip()
            return getattr(logging, level_str, logging.INFO)

    env_level = os.environ.get("FLRC_LOG_LEVEL", "").upper().strip()
    return getattr(logging, env_level, logging.INFO)


def _resolve_log_format(fmt: str | None) -> str:
    if fmt:
        return fmt.lower().strip()
    env_fmt = os.environ.get("FLRC_LOG_FORMAT", "").lower().strip()
    if env_fmt in ("json", "text"):
        return env_fmt
    return "text"


def _resolve_file_format(file_fmt: str | None, general_fmt: str) -> str:
    if file_fmt:
        return file_fmt.lower().strip()
    env_file_fmt = os.environ.get("FLRC_LOG_FILE_FORMAT", "").lower().strip()
    if env_file_fmt in ("json", "text"):
        return env_file_fmt
    return general_fmt


def setup_logging(
    level: str | int | None = None,
    output_format: str | None = None,
    file_path: str | Path | bool | None = None,
    file_format: str | None = None,
    quiet: bool = False,
    nonblocking: bool = True,
    stream: TextIO | None = None,
    capture_warnings: bool = True,
    extra_loggers: list[str] | None = None,
    default_filename: str = "flrc.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    install_guards: bool = True,
) -> logging.Logger:
    """初始化并配置统一日志系统（默认双路输出至 stderr 与 logs/ 磁盘文件）。

    参数:
        level: 日志级别 (如 "DEBUG", "INFO", "WARNING", logging.INFO)
        output_format: 控制台输出格式 ("text" 或 "json")
        file_path: 可选的磁盘落盘文件路径；传入 False 可完全禁用文件输出；默认写入 logs/flrc.log
        file_format: 磁盘文件格式 ("text" 或 "json")，默认继承 output_format (通常为 "text")
        quiet: 是否开启静默模式 (仅输出 WARNING 及以上)
        nonblocking: 是否开启非阻塞后台队列 (推荐开启，防止 SDR 欠载)
        stream: 控制台目标流 (默认严格定向至 sys.stderr)
        capture_warnings: 是否捕获 Python py.warnings
        extra_loggers: 需要一并接入同一非阻塞管道的附加记录器名称列表
        default_filename: 默认日志文件名 (默认 "flrc.log")
        max_bytes: 单个日志文件最大字节数 (默认 10MB)
        backup_count: 轮转保留历史备份个数 (默认 5 个)
    """
    resolved_level = _resolve_log_level(level, quiet=quiet)
    resolved_format = _resolve_log_format(output_format)
    resolved_file_format = _resolve_file_format(file_format, resolved_format)
    target_stream = stream or sys.stderr

    # 初始化进程环境上下文 (如 FLRC_RUN_ID)
    init_context_from_env()

    # 捕获标准警告
    logging.captureWarnings(capture_warnings)

    root_logger = logging.getLogger("flrc_modem")
    root_logger.setLevel(resolved_level)
    root_logger.addFilter(ContextFilter())

    target_extra = [logging.getLogger(name) for name in (extra_loggers or [])]

    handlers: list[logging.Handler] = []

    # 1. 终端控制台 Handler (绑定 stderr)
    console_handler = build_console_handler(
        stream=target_stream,
        output_format=resolved_format,
        level=resolved_level,
    )
    handlers.append(console_handler)

    # 2. 磁盘日志文件 Handler (默认开启，支持轮转)
    target_file = _resolve_target_file(file_path, default_filename=default_filename)
    if target_file is not None:
        file_handler = build_file_handler(
            file_path=target_file,
            output_format=resolved_file_format,
            level=resolved_level,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
        handlers.append(file_handler)

    if nonblocking:
        start_nonblocking_listener(root_logger, handlers, extra_loggers=target_extra)
    else:
        from .handlers import register_managed_handlers
        shutdown_logging()
        root_logger.handlers.clear()
        for h in handlers:
            root_logger.addHandler(h)
        for extra in target_extra:
            extra.handlers.clear()
            for h in handlers:
                extra.addHandler(h)
            extra.setLevel(resolved_level)
            extra.addFilter(ContextFilter())
        register_managed_handlers(handlers, [root_logger, *target_extra])

    # 监听器初始化会关闭旧日志系统；在其后启用崩溃监控，避免刚打开就被关闭。
    if install_guards:
        log_dir = target_file.parent if target_file is not None else get_default_log_dir()
        install_crash_guards(log_dir=log_dir, logger=root_logger)

    return root_logger


def get_logger(name: str = "flrc_modem") -> logging.Logger:
    """获取指定名称的 Logger 实例。"""
    if name in ("flrc_modem", "flrc_webui") or name.startswith(("flrc_modem.", "flrc_webui.")):
        return logging.getLogger(name)
    return logging.getLogger(f"flrc_modem.{name}")
