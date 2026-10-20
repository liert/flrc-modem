"""CLI 子进程生命周期；退出后才释放设备，支持排队取消和服务退出。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import signal
import sys
import uuid
from typing import Any

from ..commands.registry import get_command_registry
from ..devices.manager import get_device_manager
from ..events.hub import get_event_hub
from ..paths import PROJECT_ROOT
from ..schemas import JobStatus, RunRecord
from ..storage.db import get_db

logger = logging.getLogger(__name__)


class JobManager:
    def __init__(self):
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop_requests: set[str] = set()
        self._closing = False

    async def create_and_start_run(self, command_id: str, parameters: dict[str, Any],
                                   enable_ui_events: bool = True) -> RunRecord:
        if self._closing:
            raise RuntimeError("服务正在停止，无法启动任务")
        registry = get_command_registry()
        cmd = registry.get(command_id)
        if not cmd:
            raise ValueError(f"未找到指定的命令 ID: {command_id}")
        argv = registry.build_cli_argv(command_id, parameters, enable_ui_events)
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        device_mgr = get_device_manager()
        sdr_type = None
        if cmd.requires_sdr or cmd.requires_usrp:
            sdr_type = ("usrp" if cmd.requires_usrp else "hackrf" if command_id in ("live", "transmit", "hackrf")
                        else str(parameters.get("sdr") or "usrp").lower())
            acquired, reason = device_mgr.acquire_device(sdr_type, run_id)
            if not acquired:
                raise RuntimeError(reason)
        record = RunRecord(id=run_id, command_id=command_id, command_args=parameters, cli_argv=argv,
                           status=JobStatus.QUEUED, created_at=datetime.now(timezone.utc).isoformat(),
                           requires_usrp=cmd.requires_usrp or sdr_type == "usrp", sdr_type=sdr_type,
                           pipeline_id=cmd.pipeline_id)
        try:
            get_db().insert_run(record.model_dump())
            self._tasks[run_id] = asyncio.create_task(self._execute_process(run_id, argv, sdr_type))
        except BaseException:
            device_mgr.release_device(run_id)
            raise
        return record

    async def stop_run(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        first_request = run_id not in self._stop_requests
        self._stop_requests.add(run_id)
        proc = self._active_processes.get(run_id)
        if first_request and proc is not None:
            await self._terminate_process(proc)
        # 创建进程期间的取消由执行协程处理；不得提前释放设备。
        await asyncio.shield(task)
        return True

    async def shutdown(self) -> None:
        self._closing = True
        await asyncio.gather(*(self.stop_run(run_id) for run_id in list(self._tasks)), return_exceptions=True)

    async def _terminate_process(self, proc) -> None:
        if proc.returncode is not None:
            return
        try:
            if sys.platform == "win32":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/T", "/PID", str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                try:
                    await asyncio.wait_for(killer.wait(), 5)
                except asyncio.TimeoutError:
                    killer.kill()
                    await killer.wait()
            else:
                os.killpg(proc.pid, signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), 5)
        except (OSError, asyncio.TimeoutError):
            if proc.returncode is None:
                try:
                    if sys.platform == "win32":
                        proc.kill()
                    else:
                        os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()

    async def _execute_process(self, run_id: str, argv: list[str], sdr_type: str | None) -> None:
        db, hub = get_db(), get_event_hub()
        proc = None
        readers = []
        exit_code, error = 1, None
        try:
            if run_id not in self._stop_requests:
                started = datetime.now(timezone.utc).isoformat()
                db.update_run_status(run_id, JobStatus.RUNNING.value, started_at=started)
                await hub.dispatch_event(run_id, "task.state", 0, None, started, {"state": "RUNNING"})
                env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
                options = {"creationflags": 0x00000200} if sys.platform == "win32" else {"start_new_session": True}
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    cwd=str(PROJECT_ROOT), env=env, **options)
                self._active_processes[run_id] = proc
                readers = [asyncio.create_task(self._stream_reader(run_id, proc.stdout, True)),
                           asyncio.create_task(self._stream_reader(run_id, proc.stderr, False))]
                if run_id in self._stop_requests:
                    await self._terminate_process(proc)
                await asyncio.gather(*readers)
                exit_code = await proc.wait()
        except asyncio.CancelledError:
            self._stop_requests.add(run_id)
        except Exception as err:
            logger.exception("任务执行异常: %s", run_id)
            exit_code, error = 2, str(err)
        finally:
            try:
                if proc is not None and proc.returncode is None:
                    await self._terminate_process(proc)
                for reader in readers:
                    if not reader.done():
                        reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
            finally:
                get_device_manager().release_device(run_id)
                self._active_processes.pop(run_id, None)
                self._tasks.pop(run_id, None)
            cancelled = run_id in self._stop_requests
            self._stop_requests.discard(run_id)
            status = "CANCELLED" if cancelled else "SUCCEEDED" if exit_code == 0 else "FAILED"
            if cancelled:
                exit_code, error = 130, "用户或服务停止任务"
            completed = datetime.now(timezone.utc).isoformat()
            db.update_run_status(run_id, status, completed_at=completed, exit_code=exit_code, error=error)
            await hub.dispatch_event(run_id, "task.completed", 0, None, completed,
                                     {"state": status, "success": status == "SUCCEEDED",
                                      "exit_code": exit_code, "error": error})

    async def _stream_reader(self, run_id: str, stream, is_stdout: bool) -> None:
        if stream is None:
            return
        buffer = bytearray()
        # 不使用受默认 64KiB 行长限制的 readline；超大日志分块保存。
        while chunk := await stream.read(65536):
            buffer.extend(chunk)
            while (end := buffer.find(b"\n")) >= 0 or len(buffer) > 1024 * 1024:
                end = end if end >= 0 else 1024 * 1024
                line = bytes(buffer[:end])
                consumed = end + int(buffer[end:end + 1] == b"\n")
                del buffer[:consumed]
                await self._handle_line(run_id, line, is_stdout)
        if buffer:
            await self._handle_line(run_id, bytes(buffer), is_stdout)

    async def _handle_line(self, run_id: str, raw: bytes, is_stdout: bool) -> None:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            return
        hub = get_event_hub()
        record = None
        if is_stdout and line.startswith("{"):
            try:
                candidate = json.loads(line)
                if (isinstance(candidate, dict) and isinstance(candidate.get("type"), str)
                        and type(candidate.get("seq")) is int and isinstance(candidate.get("payload", {}), dict)
                        and (candidate.get("stage_id") is None or isinstance(candidate["stage_id"], str))
                        and (candidate.get("timestamp") is None or isinstance(candidate["timestamp"], str))):
                    record = candidate
            except (ValueError, TypeError):
                pass
        if record is None:
            await hub.dispatch_log(run_id, "stdout" if is_stdout else "stderr", line)
            return
        payload = record.get("payload", {})
        timestamp = record.get("timestamp") or datetime.now(timezone.utc).isoformat()
        await hub.dispatch_event(run_id, record["type"], record["seq"], record.get("stage_id"), timestamp, payload)
        if record["type"] == "artifact.created" and isinstance(payload.get("path"), str):
            get_db().insert_artifact(run_id, record.get("stage_id"), payload["path"],
                                     payload.get("kind", "file"), payload.get("metadata", {}), timestamp)


_JOB_MGR: JobManager | None = None


def get_job_manager() -> JobManager:
    global _JOB_MGR
    if _JOB_MGR is None:
        _JOB_MGR = JobManager()
    return _JOB_MGR
