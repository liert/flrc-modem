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
from ..storage.db import get_db, artifact_path_key
from ..artifacts.lifecycle import file_version
from ..artifacts.cleanup import prune_overwritten_runs
from ..commands.preflight import preflight
from flrc_modem.sync.control import validate_request

logger = logging.getLogger(__name__)


class JobManager:
    def __init__(self):
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop_requests: set[str] = set()
        self._stop_tasks: dict[str, asyncio.Task] = {}
        self._controlled_runs: set[str] = set()
        self._closing = False
        self._output_claims: dict[str, str] = {}
        self._session_inputs: dict[str, dict] = {}

    async def create_and_start_run(self, command_id: str, parameters: dict[str, Any],
                                   enable_ui_events: bool = True, name: str = "") -> RunRecord:
        if self._closing:
            raise RuntimeError("服务正在停止，无法启动任务")
        registry = get_command_registry()
        cmd = registry.get(command_id)
        if not cmd:
            raise ValueError(f"未找到指定的命令 ID: {command_id}")
        argv = registry.build_cli_argv(command_id, parameters, enable_ui_events)
        if command_id in ('transmit', 'duplex'):
            # Pure validation must finish before acquiring or opening a radio.
            await asyncio.to_thread(preflight, command_id, argv)
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        output_key = artifact_path_key(parameters["output"]) if parameters.get("output") else None
        if output_key and output_key in self._output_claims:
            raise RuntimeError("输出文件正被另一个任务写入，请等待完成或使用不同文件名")
        device_mgr = get_device_manager()
        sdr_type = None
        if cmd.requires_sdr or cmd.requires_usrp:
            sdr_type = ("usrp" if cmd.requires_usrp else "hackrf" if command_id in ("live", "transmit", "hackrf", "exchange")
                        else str(parameters.get("sdr") or "usrp").lower())
            selector = device_mgr.resolve_selector(sdr_type, parameters)
            acquired, reason = (device_mgr.acquire_device(sdr_type, run_id, selector)
                                if selector else device_mgr.acquire_device(sdr_type, run_id))
            if not acquired:
                raise RuntimeError(reason)
        record = RunRecord(id=run_id, name=name.strip(), command_id=command_id, command_args=parameters, cli_argv=argv,
                           status=JobStatus.QUEUED, created_at=datetime.now(timezone.utc).isoformat(),
                           requires_usrp=cmd.requires_usrp or sdr_type == "usrp", sdr_type=sdr_type,
                           pipeline_id=cmd.pipeline_id)
        try:
            if output_key:
                self._output_claims[output_key] = run_id
            get_db().insert_run(record.model_dump())
            self._tasks[run_id] = asyncio.create_task(self._execute_process(run_id, argv, sdr_type))
        except BaseException:
            device_mgr.release_device(run_id)
            if output_key:
                self._output_claims.pop(output_key, None)
            raise
        return record

    async def send_message(self, run_id: str, request: dict) -> dict:
        ident, payload, _ = validate_request(request)
        wire = (json.dumps(request, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        if len(wire) > 65536:
            raise ValueError('报文请求不能超过 64KiB')
        state = self._session_inputs.get(run_id)
        proc = self._active_processes.get(run_id)
        if state is None or not state['ready'] or proc is None or proc.returncode is not None or run_id in self._stop_requests:
            raise RuntimeError('常驻会话尚未就绪或已经停止')
        if isinstance(payload, dict):
            from flrc_modem.encoding import EncodeContext, MessageTemplate, load_encoder
            from flrc_modem.phy.encoder import encode_packet
            record = get_db().get_run(run_id)
            raw = MessageTemplate(load_encoder(record['command_args'].get('protocol') or 'dk32s'), payload).encode(
                EncodeContext(0, 0, state['sample_rate']))
            encode_packet(raw, b'\x00' * 4)
        async with state['lock']:
            if ident in state['seen']:
                raise RuntimeError('此 request_id 已提交；请查看原请求结果，避免重复发送')
            if len(state['seen']) >= 10000:
                raise RuntimeError('会话已接收 10000 条请求，请结束后重新建立会话')
            if len(state['pending']) >= 16:
                raise RuntimeError('会话投递队列已满，请等待已有请求完成')
            if run_id in self._stop_requests or proc.returncode is not None:
                raise RuntimeError('会话正在停止')
            state['seen'].add(ident)
            state['pending'].add(ident)
            try:
                proc.stdin.write(wire)
                await asyncio.wait_for(proc.stdin.drain(), timeout=2)
            except (OSError, asyncio.TimeoutError) as error:
                # Keep the request ID: a partial write must never be retried blindly.
                raise RuntimeError('会话管道写入未确认，请查看任务事件后再决定是否重新投递') from error
        return {'request_id': ident, 'status': 'accepted'}

    async def stop_run(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        self._stop_requests.add(run_id)
        stopping = self._stop_tasks.get(run_id)
        if stopping is None or stopping.done():
            stopping = asyncio.create_task(self._stop_run(run_id, task))
            self._stop_tasks[run_id] = stopping
            def finished(done):
                if self._stop_tasks.get(run_id) is done:
                    self._stop_tasks.pop(run_id, None)
                if not done.cancelled() and done.exception() is not None:
                    logger.error("停止任务失败: %s", run_id, exc_info=done.exception())
            stopping.add_done_callback(finished)
        # HTTP 断开或请求取消不能中断已经发起的停止流程。
        return await asyncio.shield(stopping)

    async def _stop_run(self, run_id: str, task: asyncio.Task) -> bool:
        proc = self._active_processes.get(run_id)
        if proc is not None:
            if (run_id in self._session_inputs or run_id in self._controlled_runs) and proc.stdin is not None:
                try:
                    proc.stdin.write(b'{"action":"stop"}\n')
                    await asyncio.wait_for(proc.stdin.drain(), 1)
                    await asyncio.wait_for(proc.wait(), 5)
                except (OSError, asyncio.TimeoutError):
                    await self._terminate_process(proc)
            else:
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
                session = '--session' in argv and argv[3] == 'duplex'
                controlled = argv[1:4] == ['-m', 'flrc_modem', 'transmit'] and '--stdin-jsonl' not in argv
                if controlled:
                    # 私有控制管道；不与 JSONL 报文输入复用。
                    env['FLRC_CONTROL_STDIN'] = '1'
                    self._controlled_runs.add(run_id)
                else:
                    env.pop('FLRC_CONTROL_STDIN', None)
                if session:
                    self._session_inputs[run_id] = {'ready': False, 'lock': asyncio.Lock(),
                        'pending': set(), 'seen': set(), 'sample_rate': 2500000}
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.PIPE if session or controlled else asyncio.subprocess.DEVNULL,
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
                self._controlled_runs.discard(run_id)
                state = self._session_inputs.pop(run_id, None)
                if state:
                    for ident in state['pending']:
                        await hub.dispatch_event(run_id, 'duplex.result', 0, None,
                            datetime.now(timezone.utc).isoformat(), {'request_id': ident,
                            'status': 'interrupted', 'error': '会话已结束，未收到此请求的最终执行确认；请勿自动重发'})
                self._tasks.pop(run_id, None)
                for path, owner in list(self._output_claims.items()):
                    if owner == run_id:
                        del self._output_claims[path]
            cancelled = run_id in self._stop_requests
            self._stop_requests.discard(run_id)
            status = "CANCELLED" if cancelled else "SUCCEEDED" if exit_code == 0 else "FAILED"
            if cancelled:
                exit_code, error = 130, "用户或服务停止任务"
            completed = datetime.now(timezone.utc).isoformat()
            db.update_run_status(run_id, status, completed_at=completed, exit_code=exit_code, error=error)
            if status == "SUCCEEDED":
                try:
                    pruned = await asyncio.to_thread(prune_overwritten_runs, db, run_id)
                    if pruned:
                        await hub.notify_run_change(pruned[0], "task.deleted", {
                            "run_ids": pruned, "replacement_run_id": run_id, "reason": "outputs_overwritten",
                        })
                except Exception:
                    logger.exception("清理被覆盖的旧任务失败: %s", run_id)
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
        state = self._session_inputs.get(run_id)
        if state is not None:
            if record['type'] == 'duplex.ready':
                state['ready'] = True
                state['sample_rate'] = payload.get('sample_rate', 2500000)
            elif record['type'] == 'duplex.result':
                state['pending'].discard(payload.get('request_id'))
        timestamp = record.get("timestamp") or datetime.now(timezone.utc).isoformat()
        if record["type"] == "artifact.created" and isinstance(payload.get("path"), str):
            metadata = {**(payload.get("metadata") or {}), "file_version": file_version(payload["path"])}
            get_db().insert_artifact(run_id, record.get("stage_id"), payload["path"],
                                     payload.get("kind", "file"), metadata, timestamp)
        # 浏览器收到事件后立即拉取产物；先落库，避免拉取时登记尚未完成。
        await hub.dispatch_event(run_id, record["type"], record["seq"], record.get("stage_id"), timestamp, payload)


_JOB_MGR: JobManager | None = None


def get_job_manager() -> JobManager:
    global _JOB_MGR
    if _JOB_MGR is None:
        _JOB_MGR = JobManager()
    return _JOB_MGR
