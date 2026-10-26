"""SDR 实时频谱管理器：协调会话生命周期、防泄漏看门狗、参数热重载与设备互斥控制。"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any
from flrc_modem.log import WideEvent
from .history import IQHistoryError

from .schemas import (
    SpectrumStartRequest,
    SpectrumStatusResponse,
    SpectrumUpdateRequest,
    SpectrumRecordStartRequest,
    SpectrumRecordStatus,
    SpectrumSelectionRequest,
)
from .session import SpectrumSession

logger = logging.getLogger(__name__)


class SpectrumManager:
    """管理全局实时频谱会话生命周期。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._active_session: SpectrumSession | None = None
        self._history_session: SpectrumSession | None = None
        self._selection_lock = threading.Lock()
        self._last_subscriber_activity = time.monotonic()
        self._watchdog_task: asyncio.Task | None = None

    def start_session(self, req: SpectrumStartRequest) -> SpectrumStatusResponse:
        """启动新实时频谱会话；若已有会话，则先安全释放旧资源。"""
        with self._lock:
            # Reject invalid templates before releasing the current receiver.
            session = SpectrumSession(req)
            if self._active_session is not None:
                logger.info("发现旧会话 %s，正在优雅停止并释放设备...", self._active_session.session_id)
                self._active_session.stop()
                self._active_session = None

            self._active_session = session
            self._history_session = None
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
            self._history_session = session
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

    def export_selection(self, session_id: str, req: SpectrumSelectionRequest):
        with WideEvent('spectrum.selection_export', logger=logger, entry_point='webui.spectrum.selection',
                       request_id=uuid.uuid4().hex, session_id=session_id,
                       time_start=req.time_start, time_end=req.time_end,
                       target_frequency=req.target_frequency) as event:
            if not self._selection_lock.acquire(blocking=False):
                event.set('error_code', 'export_busy')
                raise RuntimeError('正在导出另一选区，请稍后重试')
            try:
                with self._lock:
                    session = next((item for item in (self._active_session, self._history_session)
                                    if item is not None and item.session_id == session_id), None)
                if session is None:
                    event.set('error_code', 'session_not_found')
                    raise KeyError('选区所属采集会话已不存在，请在当前会话重新框选')
                # Snapshot holds immutable blocks; neither the manager lock nor
                # the RX history lock is held while writing the selected file.
                try:
                    result = session.export_selection(req)
                except IQHistoryError as error:
                    event.set('error_code', error.code)
                    raise
                event.update(bytes=result['bytes'], samples_count=result['samples_count'],
                             sample_rate=result['sample_rate'], duration_s=result['duration_s'])
                return result
            finally:
                self._selection_lock.release()

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
        with self._lock:
            self._history_session = None


_SPECTRUM_MGR: SpectrumManager | None = None


def get_spectrum_manager() -> SpectrumManager:
    global _SPECTRUM_MGR
    if _SPECTRUM_MGR is None:
        _SPECTRUM_MGR = SpectrumManager()
    return _SPECTRUM_MGR
