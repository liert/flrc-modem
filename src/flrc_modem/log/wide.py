"""宽事件（Wide Events / Canonical Log Lines）模块。

遵循每任务/请求单次聚合发射规范，集中携带高维业务上下文与性能度量。
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any, Self

from .context import get_current_context


class WideEvent:
    """宽事件聚合器与生命周期管理器。

    用法 (作为上下文管理器):
        with WideEvent("demod_job_completed") as event:
            event.set("decoded_frames", 120)
            # 正常退出自动发射 outcome="success", level=INFO
            # 异常退出自动记录 error 并发射 outcome="error", level=ERROR

    用法 (手动管理):
        event = WideEvent("sdr_acquisition")
        event.update(samples=2500000, overflows=0)
        event.emit()
    """

    def __init__(
        self,
        event_name: str,
        logger: logging.Logger | None = None,
        **initial_fields: Any,
    ):
        self.event_name = event_name
        self.logger = logger or logging.getLogger("flrc_modem")
        self.start_perf = time.perf_counter()
        self.emitted = False
        self.data: dict[str, Any] = {
            "event": event_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **initial_fields,
        }

    def set(self, key: str, value: Any) -> Self:
        """设置单个字段。"""
        self.data[key] = value
        return self

    def update(self, **kwargs: Any) -> Self:
        """批量更新字段。"""
        self.data.update(kwargs)
        return self

    def record_error(self, err: Exception | str, err_type: str | None = None) -> Self:
        """记录错误信息。"""
        if isinstance(err, Exception):
            self.data["error"] = {
                "type": type(err).__name__,
                "message": str(err),
            }
        else:
            self.data["error"] = {
                "type": err_type or "Error",
                "message": str(err),
            }
        self.data["outcome"] = "error"
        return self

    def emit(self, level: int = logging.INFO, outcome: str | None = None) -> None:
        """完成度量计算并向日志系统发射宽事件。"""
        if self.emitted:
            return
        self.emitted = True

        duration_ms = round((time.perf_counter() - self.start_perf) * 1000, 2)
        self.data["duration_ms"] = duration_ms

        if outcome is not None:
            self.data["outcome"] = outcome
        elif "outcome" not in self.data:
            self.data["outcome"] = "success" if level < logging.ERROR else "error"

        # 注入活跃的上下文追踪变量
        for k, v in get_current_context().items():
            if k not in self.data and v is not None:
                self.data[k] = v

        self.logger.log(level, self.data, extra={"is_wide_event": True})

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        if exc_type is not None:
            type_name = getattr(exc_type, "__name__", str(exc_type))
            if isinstance(exc_val, Exception):
                self.record_error(exc_val)
            else:
                self.record_error(str(exc_val or type_name), err_type=type_name)
            self.emit(level=logging.ERROR, outcome="error")
            return False  # 不吞没异常，继续上抛
        if not self.emitted:
            self.emit(level=logging.INFO, outcome=self.data.get("outcome", "success"))
        return False


def wide_event(event_name: str, logger: logging.Logger | None = None, **kwargs: Any) -> WideEvent:
    """创建宽事件对象的便捷函数。"""
    return WideEvent(event_name, logger=logger, **kwargs)
