"""上下文变量管理模块：支持跨协程、线程池与子调用的追踪字段自动传播。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
from typing import Any, Iterator

# 核心上下文变量
_RUN_ID: ContextVar[str | None] = ContextVar("flrc_run_id", default=None)
_REQUEST_ID: ContextVar[str | None] = ContextVar("flrc_request_id", default=None)
_COMMAND_ID: ContextVar[str | None] = ContextVar("flrc_command_id", default=None)
_SDR_TYPE: ContextVar[str | None] = ContextVar("flrc_sdr_type", default=None)
_SESSION_ID: ContextVar[str | None] = ContextVar("flrc_session_id", default=None)
_EXTRA_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("flrc_extra_context", default={})


def init_context_from_env() -> None:
    """从环境变量初始化预设上下文（例如子进程继承父进程环境）。"""
    if run_id := os.environ.get("FLRC_RUN_ID"):
        _RUN_ID.set(run_id)
    if command_id := os.environ.get("FLRC_COMMAND_ID"):
        _COMMAND_ID.set(command_id)
    if sdr_type := os.environ.get("FLRC_SDR_TYPE"):
        _SDR_TYPE.set(sdr_type)
    if session_id := os.environ.get("FLRC_SESSION_ID"):
        _SESSION_ID.set(session_id)


def get_current_context() -> dict[str, Any]:
    """获取当前协程/线程绑定的全部追踪上下文字典。"""
    ctx: dict[str, Any] = {}
    if run_id := _RUN_ID.get():
        ctx["run_id"] = run_id
    if request_id := _REQUEST_ID.get():
        ctx["request_id"] = request_id
    if command_id := _COMMAND_ID.get():
        ctx["command"] = command_id
    if sdr_type := _SDR_TYPE.get():
        ctx["sdr_type"] = sdr_type
    if session_id := _SESSION_ID.get():
        ctx["session_id"] = session_id
    if extra := _EXTRA_CONTEXT.get():
        ctx.update(extra)
    return ctx


@contextmanager
def bind_context(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    command_id: str | None = None,
    sdr_type: str | None = None,
    session_id: str | None = None,
    **extra: Any,
) -> Iterator[dict[str, Any]]:
    """在代码块执行期间绑定指定上下文变量，退出时安全恢复。

    用法:
        with bind_context(run_id="run_123", command_id="duplex"):
            logger.info("开始处理")  # 自动携带 run_id 与 command_id
    """
    tokens = []
    if run_id is not None:
        tokens.append((_RUN_ID, _RUN_ID.set(run_id)))
    if request_id is not None:
        tokens.append((_REQUEST_ID, _REQUEST_ID.set(request_id)))
    if command_id is not None:
        tokens.append((_COMMAND_ID, _COMMAND_ID.set(command_id)))
    if sdr_type is not None:
        tokens.append((_SDR_TYPE, _SDR_TYPE.set(sdr_type)))
    if session_id is not None:
        tokens.append((_SESSION_ID, _SESSION_ID.set(session_id)))

    if extra:
        merged_extra = {**_EXTRA_CONTEXT.get(), **extra}
        tokens.append((_EXTRA_CONTEXT, _EXTRA_CONTEXT.set(merged_extra)))

    try:
        yield get_current_context()
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def set_context_var(name: str, value: Any) -> None:
    """直接设置全局或顶级上下文变量。"""
    match name:
        case "run_id":
            _RUN_ID.set(value)
        case "request_id":
            _REQUEST_ID.set(value)
        case "command_id":
            _COMMAND_ID.set(value)
        case "sdr_type":
            _SDR_TYPE.set(value)
        case "session_id":
            _SESSION_ID.set(value)
        case _:
            merged = {**_EXTRA_CONTEXT.get(), name: value}
            _EXTRA_CONTEXT.set(merged)


def clear_context() -> None:
    """重置所有上下文变量。"""
    _RUN_ID.set(None)
    _REQUEST_ID.set(None)
    _COMMAND_ID.set(None)
    _SDR_TYPE.set(None)
    _SESSION_ID.set(None)
    _EXTRA_CONTEXT.set({})
