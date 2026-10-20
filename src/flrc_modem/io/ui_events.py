"""机器可读 UI 实时事件输出模块。

在开启 --ui-events ndjson 时，将结构化事件以 NDJSON 形式逐行写入 stdout。
stderr 保持日志与错误输出。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import sys
import threading
from typing import Any, Iterator


class UIEventEmitter:
    """管理并格式化机器事件流输出。"""

    def __init__(self, stream=None, enabled: bool = False):
        self.stream = stream or sys.stdout
        self.enabled = enabled
        self._seq = 0
        self._lock = threading.Lock()

    def emit(
        self,
        event_type: str,
        stage_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._seq += 1
            record = {
                "type": event_type,
                "seq": self._seq,
                "stage_id": stage_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": payload or {},
            }
            try:
                line = json.dumps(record, ensure_ascii=False)
                self.stream.write(line + "\n")
                self.stream.flush()
            except Exception as err:
                print(f"[ui-events error] Failed to emit event: {err}", file=sys.stderr)

    @contextmanager
    def stage(
        self,
        stage_id: str,
        start_payload: dict[str, Any] | None = None,
    ) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        self.emit("stage.started", stage_id=stage_id, payload=start_payload)
        try:
            yield
            self.emit("stage.completed", stage_id=stage_id, payload={"success": True})
        except Exception as err:
            self.emit(
                "stage.failed",
                stage_id=stage_id,
                payload={"error": str(err), "exception_type": type(err).__name__},
            )
            raise

    def progress(self, stage_id: str, progress: float, **extra: Any) -> None:
        self.emit(
            "stage.progress",
            stage_id=stage_id,
            payload={"progress": max(0.0, min(1.0, progress)), **extra},
        )

    def metric(self, stage_id: str, metrics: dict[str, Any]) -> None:
        self.emit("metric.sample", stage_id=stage_id, payload=metrics)

    def artifact(
        self,
        stage_id: str,
        path: str | os.PathLike,
        kind: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "path": str(path),
            "kind": kind,
            "metadata": metadata or {},
        }
        self.emit("artifact.created", stage_id=stage_id, payload=payload)

    def task_completed(self, success: bool, **extra: Any) -> None:
        self.emit("task.completed", payload={"success": success, **extra})


_GLOBAL_EMITTER: UIEventEmitter | None = None


def get_emitter(enabled: bool = False) -> UIEventEmitter:
    global _GLOBAL_EMITTER
    if _GLOBAL_EMITTER is None or _GLOBAL_EMITTER.enabled != enabled:
        _GLOBAL_EMITTER = UIEventEmitter(enabled=enabled)
    return _GLOBAL_EMITTER
