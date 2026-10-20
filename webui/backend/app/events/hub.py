"""实时事件中心：统一接收、校验、分发任务事件及轻量级指标。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any
from fastapi import WebSocket

from ..storage.db import get_db

logger = logging.getLogger(__name__)


class EventHub:
    """管理 WebSocket 客户端连接并分发任务事件流。"""

    def __init__(self):
        # 针对特定 run_id 的订阅者集合
        self._run_subscribers: dict[str, set[WebSocket]] = {}
        # 全局订阅者集合
        self._global_subscribers: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket, run_id: str | None = None) -> None:
        await ws.accept()
        async with self._lock:
            if run_id:
                if run_id not in self._run_subscribers:
                    self._run_subscribers[run_id] = set()
                self._run_subscribers[run_id].add(ws)
            else:
                self._global_subscribers.add(ws)

    async def unregister(self, ws: WebSocket, run_id: str | None = None) -> None:
        async with self._lock:
            if run_id and run_id in self._run_subscribers:
                self._run_subscribers[run_id].discard(ws)
                if not self._run_subscribers[run_id]:
                    del self._run_subscribers[run_id]
            self._global_subscribers.discard(ws)

    async def dispatch_event(
        self,
        run_id: str,
        type_: str,
        seq: int,
        stage_id: str | None,
        timestamp: str,
        payload: dict[str, Any],
    ) -> None:
        """持久化并推送结构化事件。"""
        db = get_db()
        db.insert_event(run_id, seq, type_, stage_id, timestamp, payload)

        event_msg = {
            "type": type_,
            "run_id": run_id,
            "seq": seq,
            "stage_id": stage_id,
            "timestamp": timestamp,
            "payload": payload,
        }
        await self._broadcast(run_id, event_msg)

    async def dispatch_log(self, run_id: str, stream: str, line: str) -> None:
        """持久化并推送日志行。"""
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        db.insert_log(run_id, stream, line, now)

        msg = {
            "type": "task.log",
            "run_id": run_id,
            "stream": stream,
            "line": line,
            "timestamp": now,
        }
        await self._broadcast(run_id, msg)

    async def _broadcast(self, run_id: str, message: dict[str, Any]) -> None:
        text = json.dumps(message, ensure_ascii=False)

        targets: set[WebSocket] = set()
        async with self._lock:
            if run_id in self._run_subscribers:
                targets.update(self._run_subscribers[run_id])
            targets.update(self._global_subscribers)

        if not targets:
            return

        async def send(ws):
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=1.0)
            except Exception:
                await self.unregister(ws, run_id)
                try:
                    await asyncio.wait_for(ws.close(), timeout=0.1)
                except Exception:
                    pass
        await asyncio.gather(*(send(ws) for ws in targets))


_EVENT_HUB: EventHub | None = None


def get_event_hub() -> EventHub:
    global _EVENT_HUB
    if _EVENT_HUB is None:
        _EVENT_HUB = EventHub()
    return _EVENT_HUB
