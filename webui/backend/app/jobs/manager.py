"""任务管理器：管理 CLI 子进程生命周期、Windows 进程树终止、输出收集与设备锁联动。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import uuid
from typing import Any

from ..commands.registry import get_command_registry
from ..devices.manager import get_device_manager
from ..events.hub import get_event_hub
from ..schemas import JobStatus, RunRecord
from ..storage.db import get_db

logger = logging.getLogger(__name__)


class JobManager:
    """管理子进程执行与状态跟踪。"""

    def __init__(self):
        # 正在活跃运行中的子进程句柄: run_id -> asyncio.subprocess.Process
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}
        # 运行中的监控协程任务: run_id -> asyncio.Task
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def create_and_start_run(
        self,
        command_id: str,
        parameters: dict[str, Any],
        enable_ui_events: bool = True,
    ) -> RunRecord:
        registry = get_command_registry()
        cmd = registry.get(command_id)
        if not cmd:
            raise ValueError(f"未找到指定的命令 ID: {command_id}")

        db = get_db()
        device_mgr = get_device_manager()

        # 生成唯一 run_id
        now_utc = datetime.now(timezone.utc)
        run_id = f"run_{now_utc.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        # 检查 SDR 设备互斥占用需求
        sdr_type = None
        if cmd.requires_sdr or cmd.requires_usrp:
            sdr_type = str(parameters.get("sdr", "usrp") if not cmd.requires_usrp else "usrp").lower()
            acquired, reason = device_mgr.acquire_device(sdr_type, run_id)
            if not acquired:
                now_str = now_utc.isoformat()
                run_rec = {
                    "id": run_id,
                    "command_id": command_id,
                    "command_args": parameters,
                    "cli_argv": [],
                    "status": JobStatus.FAILED.value,
                    "created_at": now_str,
                    "started_at": now_str,
                    "completed_at": now_str,
                    "exit_code": 1,
                    "error": reason,
                    "requires_usrp": cmd.requires_usrp or sdr_type == "usrp",
                    "sdr_type": sdr_type,
                    "pipeline_id": cmd.pipeline_id,
                    "summary": {"error": reason},
                }
                db.insert_run(run_rec)
                raise RuntimeError(reason)

        # 构建安全参数列表 (非 shell 字符串拼接)
        cli_argv = registry.build_cli_argv(command_id, parameters, enable_ui_events)

        now_str = now_utc.isoformat()
        run_dict = {
            "id": run_id,
            "command_id": command_id,
            "command_args": parameters,
            "cli_argv": cli_argv,
            "status": JobStatus.QUEUED.value,
            "created_at": now_str,
            "started_at": None,
            "completed_at": None,
            "exit_code": None,
            "error": None,
            "requires_usrp": cmd.requires_usrp or sdr_type == "usrp",
            "sdr_type": sdr_type,
            "pipeline_id": cmd.pipeline_id,
            "summary": {},
        }
        db.insert_run(run_dict)

        # 异步启动执行监控
        task = asyncio.create_task(
            self._execute_process(run_id, cli_argv, sdr_type)
        )
        self._tasks[run_id] = task

        return RunRecord(**run_dict)

    async def stop_run(self, run_id: str) -> bool:
        """终止正在运行的任务子进程。"""
        db = get_db()
        device_mgr = get_device_manager()
        event_hub = get_event_hub()

        proc = self._active_processes.get(run_id)
        if not proc:
            return False

        logger.info(f"正在停止任务 {run_id} (PID: {proc.pid})...")
        now_str = datetime.now(timezone.utc).isoformat()

        # Windows 下使用 taskkill /F /T 强行终止整棵子进程树
        if sys.platform == "win32":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/T", "/PID", str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            except Exception as e:
                logger.warning(f"taskkill 终止进程失败: {e}，尝试使用 proc.kill()")
                try:
                    proc.kill()
                except Exception:
                    pass
        else:
            try:
                proc.terminate()
            except Exception:
                pass

        # 释放设备
        device_mgr.release_device(run_id)

        # 更新状态
        db.update_run_status(
            run_id,
            status=JobStatus.CANCELLED.value,
            completed_at=now_str,
            exit_code=130,
            error="用户手动终止任务",
        )

        await event_hub.dispatch_event(
            run_id,
            type_="task.state",
            seq=999999,
            stage_id=None,
            timestamp=now_str,
            payload={"state": "CANCELLED", "reason": "user_cancelled"},
        )
        return True

    async def _execute_process(
        self, run_id: str, argv: list[str], sdr_type: str | None
    ) -> None:
        db = get_db()
        device_mgr = get_device_manager()
        event_hub = get_event_hub()

        started_at = datetime.now(timezone.utc).isoformat()
        db.update_run_status(run_id, status=JobStatus.RUNNING.value, started_at=started_at)
        await event_hub.dispatch_event(
            run_id,
            type_="task.state",
            seq=1,
            stage_id=None,
            timestamp=started_at,
            payload={"state": "RUNNING"},
        )

        proc: asyncio.subprocess.Process | None = None
        exit_code: int = 1
        error_msg: str | None = None

        try:
            logger.info(f"启动子进程: {' '.join(argv)}")
            # 保证 cwd 为当前工程根目录并传递环境变量
            current_env = os.environ.copy()
            current_cwd = str(Path.cwd().resolve())

            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=current_cwd,
                env=current_env,
                creationflags=0x00000200 if sys.platform == "win32" else 0,
            )
            self._active_processes[run_id] = proc

            # 同时读取 stdout 和 stderr
            stdout_task = asyncio.create_task(self._stream_reader(run_id, proc.stdout, is_stdout=True))
            stderr_task = asyncio.create_task(self._stream_reader(run_id, proc.stderr, is_stdout=False))

            await asyncio.gather(stdout_task, stderr_task)
            exit_code = await proc.wait()

        except Exception as err:
            logger.error(f"子进程执行异常 ({run_id}): {err}", exc_info=True)
            error_msg = str(err)
            exit_code = 2
        finally:
            completed_at = datetime.now(timezone.utc).isoformat()
            device_mgr.release_device(run_id)

            async with self._lock:
                self._active_processes.pop(run_id, None)
                self._tasks.pop(run_id, None)

            current = db.get_run(run_id)
            if current and current.get("status") == JobStatus.CANCELLED.value:
                return

            final_status = JobStatus.SUCCEEDED.value if exit_code == 0 else JobStatus.FAILED.value
            db.update_run_status(
                run_id,
                status=final_status,
                completed_at=completed_at,
                exit_code=exit_code,
                error=error_msg,
            )

            await event_hub.dispatch_event(
                run_id,
                type_="task.completed",
                seq=999998,
                stage_id=None,
                timestamp=completed_at,
                payload={"success": exit_code == 0, "exit_code": exit_code, "error": error_msg},
            )

    async def _stream_reader(
        self, run_id: str, stream: asyncio.StreamReader | None, is_stdout: bool
    ) -> None:
        if not stream:
            return

        db = get_db()
        event_hub = get_event_hub()

        while not stream.at_eof():
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue

            if is_stdout:
                # 尝试解析为机器 NDJSON 事件
                if line.startswith("{") and line.endswith("}"):
                    try:
                        record = json.loads(line)
                        if "type" in record and "seq" in record:
                            type_ = record["type"]
                            seq = int(record["seq"])
                            stage_id = record.get("stage_id")
                            timestamp = record.get("timestamp") or datetime.now(timezone.utc).isoformat()
                            payload = record.get("payload") or {}

                            await event_hub.dispatch_event(
                                run_id, type_, seq, stage_id, timestamp, payload
                            )

                            if type_ == "artifact.created":
                                artifact_path = payload.get("path")
                                kind = payload.get("kind", "file")
                                metadata = payload.get("metadata", {})
                                if artifact_path:
                                    db.insert_artifact(
                                        run_id, stage_id, artifact_path, kind, metadata, timestamp
                                    )
                            continue
                    except json.JSONDecodeError:
                        pass

                # 普通 stdout 日志
                await event_hub.dispatch_log(run_id, "stdout", line)
            else:
                # stderr 输出
                await event_hub.dispatch_log(run_id, "stderr", line)


_JOB_MGR: JobManager | None = None


def get_job_manager() -> JobManager:
    global _JOB_MGR
    if _JOB_MGR is None:
        _JOB_MGR = JobManager()
    return _JOB_MGR
