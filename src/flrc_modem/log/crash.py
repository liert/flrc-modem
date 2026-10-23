"""崩溃防护与未捕获异常捕获模块。

集成 Python 级未捕获异常钩子与 C/C++ 原生层崩溃追踪 (faulthandler)。
确保在系统异常终止、多线程崩溃或驱动访问违规时，现场堆栈与日志绝对不丢失。
"""
from __future__ import annotations

from datetime import datetime, timezone
import faulthandler
import logging
import os
from pathlib import Path
import sys
import threading
from typing import TextIO

from .handlers import shutdown_logging

_FAULT_STREAM: TextIO | None = None
_HOOKS_INSTALLED = False


def enable_native_fault_monitor(crash_log_path: Path) -> Path | None:
    """启用 C/C++ 原生崩溃 (SEGV, 0xC0000005 访问违规等) 捕获，写入指定文件。"""
    global _FAULT_STREAM
    if _FAULT_STREAM is not None:
        return Path(_FAULT_STREAM.name)

    stream = None
    try:
        crash_log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = crash_log_path.open("a", encoding="utf-8", buffering=1)
        stream.write(
            f"\n=== [Native Fault Monitor] Started at {datetime.now(timezone.utc).isoformat()} "
            f"pid={os.getpid()} python={sys.version.split()[0]} ===\n"
        )
        stream.flush()
        faulthandler.enable(file=stream, all_threads=True)
        _FAULT_STREAM = stream
        return crash_log_path
    except Exception as err:
        if stream is not None:
            stream.close()
        logging.getLogger("flrc_modem").warning(f"未能启用底层原生崩溃日志: {err}")
        return None


def disable_native_fault_monitor() -> None:
    """禁用原生崩溃追踪并关闭日志流句柄。"""
    global _FAULT_STREAM
    if _FAULT_STREAM is not None:
        try:
            faulthandler.disable()
            _FAULT_STREAM.flush()
            _FAULT_STREAM.close()
        except Exception:
            pass
        _FAULT_STREAM = None


def install_crash_guards(
    log_dir: Path | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """安装全局异常守卫：挂载 sys.excepthook、threading.excepthook 与 faulthandler。"""
    global _HOOKS_INSTALLED
    log = logger or logging.getLogger("flrc_modem")
    target_dir = log_dir or (Path.cwd() / "logs")
    crash_file = target_dir / "crash.log"

    # 1. 挂载原生崩溃处理器 (C 级异常)
    enable_native_fault_monitor(crash_file)

    if _HOOKS_INSTALLED:
        return

    # 2. 挂载主线程未捕获异常钩子
    orig_sys_excepthook = sys.excepthook

    def _sys_excepthook(exc_type, exc_val, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            # 允许用户 Ctrl+C 正常中止
            orig_sys_excepthook(exc_type, exc_val, exc_tb)
            return

        log.critical(
            f"主线程未捕获异常: {exc_type.__name__}: {exc_val}",
            exc_info=(exc_type, exc_val, exc_tb),
        )
        # 进程退出前务必刷新异步队列，确保日志落盘
        shutdown_logging()
        orig_sys_excepthook(exc_type, exc_val, exc_tb)

    sys.excepthook = _sys_excepthook

    # 3. 挂载子线程未捕获异常钩子 (Python 3.8+)
    def _threading_excepthook(args: threading.ExceptHookArgs):
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        thread_name = args.thread.name if args.thread else "UnknownThread"
        log.critical(
            f"后台线程 [{thread_name}] 发生未捕获异常: {args.exc_type.__name__}: {args.exc_value}",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _threading_excepthook
    _HOOKS_INSTALLED = True
