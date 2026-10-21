"""SDR 实时频谱管理器：协调会话生命周期、防泄漏看门狗、参数热重载与设备互斥控制。"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from .schemas import (
    SpectrumStartRequest,
    SpectrumStatusResponse,
    SpectrumUpdateRequest,
    SpectrumRecordStartRequest,
    SpectrumRecordStatus,
)
from .session import SpectrumSession

logger = logging.getLogger(__name__)


class SpectrumManager:
    """管理全局实时频谱会话生命周期。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._active_session: SpectrumSession | None = None
        self._last_subscriber_activity = time.monotonic()
        self._watchdog_task: asyncio.Task | None = None

    def start_session(self, req: SpectrumStartRequest) -> SpectrumStatusResponse:
        """启动新实时频谱会话；若已有会话，则先安全释放旧资源。"""
        with self._lock:
            if self._active_session is not None:
                logger.info("发现旧会话 %s，正在优雅停止并释放设备...", self._active_session.session_id)
                self._active_session.stop()
                self._active_session = None

            session = SpectrumSession(req)
            self._active_session = session
            session.start()
            self._last_subscriber_activity = time.monotonic()
            return session.get_status()

    def stop_session(self, session_id: str | None = None) -> SpectrumStatusResponse:
        """停止指定的或当前活跃的实时频谱会话。"""
        with self._lock:
            if self._active_session is None:
                return SpectrumStatusResponse(state="idle")

            if session_id and self._active_session.session_id != session_id:
                logger.warning(
                    "请求停止的会话 %s 与当前活跃会话 %s 不匹配",
                    session_id,
                    self._active_session.session_id,
                )
                return self._active_session.get_status()

            session = self._active_session
            session.stop()
            self._active_session = None
            return session.get_status()

    def update_parameters(self, session_id: str, req: SpectrumUpdateRequest) -> dict[str, Any]:
        with self._lock:
            if self._active_session is None or self._active_session.session_id != session_id:
                raise KeyError(f"实时频谱会话 {session_id} 不存在或已停止")
            return self._active_session.update_parameters(req)

    def pause_session(self, session_id: str) -> SpectrumStatusResponse:
        with self._lock:
            if self._active_session is None or self._active_session.session_id != session_id:
                raise KeyError(f"会话 {session_id} 不存在")
            self._active_session.pause_display()
            return self._active_session.get_status()

    def resume_session(self, session_id: str) -> SpectrumStatusResponse:
        with self._lock:
            if self._active_session is None or self._active_session.session_id != session_id:
                raise KeyError(f"会话 {session_id} 不存在")
            self._active_session.resume_display()
            return self._active_session.get_status()

    def start_recording(self, session_id: str, req: SpectrumRecordStartRequest) -> dict[str, Any]:
        with self._lock:
            if self._active_session is None or self._active_session.session_id != session_id:
                raise KeyError(f"实时频谱会话 {session_id} 不存在或未运行")
            return self._active_session.start_recording(duration_s=req.duration_s, filename_prefix=req.filename_prefix)

    def stop_recording(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            if self._active_session is None or self._active_session.session_id != session_id:
                raise KeyError(f"实时频谱会话 {session_id} 不存在或未运行")
            return self._active_session.stop_recording()

    def get_recording_status(self, session_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self._active_session is None or (session_id and self._active_session.session_id != session_id):
                return {"is_recording": False, "completed": False}
            return self._active_session.get_recording_status()

    def get_session(self, session_id: str | None = None) -> SpectrumSession | None:
        with self._lock:
            if self._active_session is None:
                return None
            if session_id and self._active_session.session_id != session_id:
                return None
            return self._active_session

    def get_status(self) -> SpectrumStatusResponse:
        with self._lock:
            if self._active_session is None:
                return SpectrumStatusResponse(state="idle")
            return self._active_session.get_status()

    def touch_activity(self, session_id: str | None = None):
        """记录客户端心跳或连接活跃时间。"""
        with self._lock:
            if self._active_session and (session_id is None or self._active_session.session_id == session_id):
                self._last_subscriber_activity = time.monotonic()

    def check_watchdog(self):
        """若长时间无客户端连接或心跳，自动关闭会话防止硬件锁泄漏。"""
        with self._lock:
            if self._active_session:
                # 无订阅者且超过 15 秒无活跃心跳
                age = time.monotonic() - self._last_subscriber_activity
                if age > (30.0 if self._active_session.has_subscribers else 15.0):
                    logger.warning("实时频谱无活跃订阅者已超时 (15s)，自动释放硬件资源...")
                    try:
                        self._active_session.stop()
                    except Exception as err:
                        logger.warning("看门狗停止会话告警: %s", err)
                    else:
                        self._active_session = None

    def start_watchdog(self):
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog())

    async def _watchdog(self):
        while True:
            await asyncio.sleep(1)
            await asyncio.to_thread(self.check_watchdog)

    async def shutdown(self):
        """系统退出时的全局清理策略。"""
        if self._watchdog_task:
            self._watchdog_task.cancel()
            await asyncio.gather(self._watchdog_task, return_exceptions=True)
            self._watchdog_task = None
        await asyncio.to_thread(self.stop_session)


_SPECTRUM_MGR: SpectrumManager | None = None


def get_spectrum_manager() -> SpectrumManager:
    global _SPECTRUM_MGR
    if _SPECTRUM_MGR is None:
        _SPECTRUM_MGR = SpectrumManager()
    return _SPECTRUM_MGR
