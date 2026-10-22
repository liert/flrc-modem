"""日志格式化器与上下文过滤器：支持紧凑彩色终端与结构化 JSONL 输出。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import sys
import traceback
from typing import Any

from .context import get_current_context


class ContextFilter(logging.Filter):
    """提取 contextvars 中的上下文并注入到 LogRecord 中。"""

    def filter(self, record: logging.LogRecord) -> bool:
        # 如果 record 已经由调用线程注入过上下文，直接沿用，避免异步消费线程覆盖
        if getattr(record, "_context_applied", False):
            return True

        ctx = get_current_context()
        record.run_id = ctx.get("run_id")
        record.command = ctx.get("command")
        record.request_id = ctx.get("request_id")
        record.sdr_type = ctx.get("sdr_type")
        record.session_id = ctx.get("session_id")
        record.extra_context = ctx
        record._context_applied = True
        return True


# ANSI 颜色定义
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_COLORS = {
    logging.DEBUG: "\033[36m",     # 青色
    logging.INFO: "\033[32m",      # 绿色
    logging.WARNING: "\033[33m",   # 黄色
    logging.ERROR: "\033[31m",     # 红色
    logging.CRITICAL: "\033[1;35m", # 亮洋红
}


class ConsoleColoredFormatter(logging.Formatter):
    """面向交互控制台的紧凑着色格式化器（严格定向至 stderr）。"""

    def __init__(self, use_colors: bool | None = None):
        super().__init__()
        if use_colors is None:
            # 自动探测终端与环境变量
            no_color = "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb"
            force_color = "FORCE_COLOR" in os.environ
            self.use_colors = force_color or (not no_color and sys.stderr.isatty())
        else:
            self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        # 1. 时间戳
        dt = datetime.fromtimestamp(record.created)
        ts_str = dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        # 2. 上下文标签 Tag 构建
        tags = []
        if getattr(record, "command", None):
            tags.append(str(record.command))
        if getattr(record, "run_id", None):
            # 截短 run_id 保持界面整洁
            run_id = str(record.run_id)
            short_id = run_id[-8:] if len(run_id) > 12 else run_id
            tags.append(short_id)
        elif getattr(record, "request_id", None):
            short_req = str(record.request_id)[:8]
            tags.append(short_req)

        tag_str = f"[{':'.join(tags)}]" if tags else ""

        # 3. 颜色与级别
        level_name = record.levelname
        if self.use_colors:
            color = _COLORS.get(record.levelno, _RESET)
            level_part = f"{color}{level_name:5s}{_RESET}"
            ts_part = f"{_DIM}{ts_str}{_RESET}"
            tag_part = f"{_BOLD}{tag_str}{_RESET} " if tag_str else ""
        else:
            level_part = f"{level_name:5s}"
            ts_part = ts_str
            tag_part = f"{tag_str} " if tag_str else ""

        # 4. 消息内容处理 (支持普通字符串与字典/宽事件)
        if isinstance(record.msg, dict):
            if getattr(record, "is_wide_event", False):
                event_name = record.msg.get("event", "wide_event")
                outcome = record.msg.get("outcome", "info")
                dur = record.msg.get("duration_ms")
                dur_str = f" in {dur:.1f}ms" if dur is not None else ""
                raw_msg = f"⚡ [{event_name}] outcome={outcome}{dur_str} {json.dumps(record.msg, ensure_ascii=False)}"
            else:
                raw_msg = json.dumps(record.msg, ensure_ascii=False)
        else:
            raw_msg = record.getMessage()

        line = f"[{ts_part}] {level_part} {tag_part}{raw_msg}"

        # 5. 异常堆栈格式化
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            if record.exc_text:
                line = f"{line}\n{record.exc_text}"
        return line


class JsonLinesFormatter(logging.Formatter):
    """结构化 NDJSON 格式化器，符合可观测性与集中式日志检索规范。"""

    def format(self, record: logging.LogRecord) -> str:
        ctx = getattr(record, "extra_context", {}) or get_current_context()

        # 基础条目
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
        }

        # 注入关联上下文
        if ctx:
            entry["context"] = ctx

        # 宽事件或者常规消息展开
        if isinstance(record.msg, dict):
            if getattr(record, "is_wide_event", False):
                # 宽事件平铺至顶级字典，保持 Canonical Log Lines 扁平查询友好性
                entry.update(record.msg)
            else:
                entry["payload"] = record.msg
        else:
            entry["message"] = record.getMessage()

        # 异常捕获
        if record.exc_info:
            exc_type, exc_val, exc_tb = record.exc_info
            entry["error"] = {
                "type": getattr(exc_type, "__name__", str(exc_type)),
                "message": str(exc_val),
                "stack": traceback.format_exception(exc_type, exc_val, exc_tb),
            }

        return json.dumps(entry, ensure_ascii=False)
