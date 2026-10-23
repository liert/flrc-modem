"""FastAPI 统一 API 路由定义 (/api/v1 与 /ws)。"""
from __future__ import annotations
import asyncio

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
import uuid
from anyio import CancelScope
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from ..artifacts.manager import get_artifact_manager
from ..artifacts.context import analysis_context
from ..artifacts.lifecycle import context_file_states, describe_artifacts
from ..commands.registry import get_command_registry
from ..commands.preflight import preflight
from ..devices.manager import get_device_manager
from ..events.hub import get_event_hub
from ..jobs.manager import get_job_manager
from ..pipelines.registry import get_pipeline_registry
from ..schemas import (
    ArtifactItem,
    CommandDefinition,
    CreateRunRequest,
    DeviceInfo,
    PipelineDefinition,
    PresetConfig,
    RunRecord,
    RenameRunRequest,
    DeleteRunsRequest,
    JobStatus,
    SavePresetRequest,
    SaveTemplateRequest,
    TemplateItem,
    ModulatePreviewRequest,
    ModulatePreviewResponse,
    FrameSlice,
    DeviceDiagnosticsResponse,
)
from ..storage.db import get_db
from ..storage.db import artifact_path_key
from ..paths import PROJECT_ROOT
from ..templates.manager import get_template_manager

from ..spectrum.manager import get_spectrum_manager
from ..spectrum.schemas import (
    SpectrumStartRequest,
    SpectrumStatusResponse,
    SpectrumUpdateRequest,
    SpectrumRecordStartRequest,
    SpectrumRecordStatus,
)

api_router = APIRouter(prefix="/api/v1")
ws_router = APIRouter()


# ==========================================
# 1. 命令注册 (Commands)
# ==========================================
@api_router.get("/commands", response_model=list[CommandDefinition])
async def list_commands():
    registry = get_command_registry()
    return registry.list_all()


@api_router.get("/commands/{command_id}", response_model=CommandDefinition)
async def get_command(command_id: str):
    registry = get_command_registry()
    cmd = registry.get(command_id)
    if not cmd:
        raise HTTPException(status_code=404, detail=f"命令不存在: {command_id}")
    return cmd


@api_router.post('/commands/{command_id}/preflight')
async def check_message_config(command_id: str, req: CreateRunRequest):
    if command_id not in ('transmit', 'duplex'):
        raise HTTPException(status_code=400, detail='此检查仅用于报文收发')
    try:
        import asyncio
        argv = get_command_registry().build_cli_argv(command_id, req.parameters)
        return await asyncio.to_thread(preflight, command_id, argv)
    except (ValueError, TypeError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error))


# ==========================================
# 2. 流水线定义 (Pipelines)
# ==========================================
@api_router.get("/pipelines", response_model=list[PipelineDefinition])
async def list_pipelines():
    return get_pipeline_registry().list_all()


@api_router.get("/system/status")
async def get_system_status():
    import sys
    db = get_db()
    cmd_reg = get_command_registry()
    pipe_reg = get_pipeline_registry()
    run_count = len(await asyncio.to_thread(db.get_runs, limit=1000))
    now_utc = datetime.now(timezone.utc).isoformat()
    return {
        "status": "ok",
        "app_version": "0.2.0",
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "timestamp_utc": now_utc,
        "task_output": get_event_hub().snapshot(),
        "counts": {
            "commands": len(cmd_reg.list_all()),
            "pipelines": len(pipe_reg.list_all()),
            "runs": run_count,
        },
    }


@api_router.get("/pipelines/{pipeline_id}", response_model=PipelineDefinition)
async def get_pipeline(pipeline_id: str):
    pipe = get_pipeline_registry().get(pipeline_id)
    if not pipe:
        raise HTTPException(status_code=404, detail=f"流水线不存在: {pipeline_id}")
    return pipe


# ==========================================
# 3. 任务管理 (Runs / Jobs)
# ==========================================
@api_router.post("/runs", response_model=RunRecord)
async def create_run(req: CreateRunRequest):
    job_mgr = get_job_manager()
    try:
        record = await job_mgr.create_and_start_run(
            command_id=req.command_id,
            parameters=req.parameters,
            enable_ui_events=req.enable_ui_events,
            name=req.name,
        )
        return record
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.get("/runs", response_model=list[RunRecord])
def list_runs(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
              status: JobStatus | None = None, search: str = Query("", max_length=200)):
    db = get_db()
    return db.get_runs(limit=limit, offset=offset, status=status.value if status else None, search=search.strip())


@api_router.patch("/runs/{run_id}", response_model=RunRecord)
async def rename_run(run_id: str, req: RenameRunRequest):
    db = get_db()
    try:
        await asyncio.to_thread(db.rename_run, run_id, req.name.strip())
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在或已删除")
    record = await asyncio.to_thread(db.get_run, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在或已删除")
    await get_event_hub().notify_run_change(run_id, "task.updated", {})
    return record


@api_router.post("/runs/delete")
async def delete_runs(req: DeleteRunsRequest):
    try:
        ids = await asyncio.to_thread(get_db().delete_runs, req.run_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail="部分任务不存在或已删除，请刷新列表")
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err))
    await get_event_hub().notify_run_change(ids[0], "task.deleted", {"run_ids": ids})
    return {"deleted_run_ids": ids, "files_retained": True}


@api_router.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    return await delete_runs(DeleteRunsRequest(run_ids=[run_id]))


@api_router.get("/runs/{run_id}", response_model=RunRecord)
def get_run(run_id: str, attempt: int | None = Query(None, ge=1)):
    db = get_db()
    run = db.get_run_attempt(run_id, attempt)
    if not run:
        raise HTTPException(status_code=404, detail=f"任务未找到: {run_id}")
    return run


@api_router.get("/runs/{run_id}/attempts", response_model=list[RunRecord])
def get_run_attempts(run_id: str):
    attempts = get_db().get_run_attempts(run_id)
    if not attempts:
        raise HTTPException(status_code=404, detail="任务不存在或已删除")
    return attempts


@api_router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str):
    job_mgr = get_job_manager()
    success = await job_mgr.stop_run(run_id)
    return {"run_id": run_id, "stopped": success}


@api_router.post("/runs/{run_id}/rerun", response_model=RunRecord)
async def rerun_run(run_id: str):
    run = await asyncio.to_thread(get_db().get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="任务不存在或已删除")
    if run["status"] not in ("SUCCEEDED", "FAILED", "CANCELLED"):
        raise HTTPException(status_code=409, detail="请先停止运行中或排队中的任务，再重新执行")
    # 重新构建并校验参数，不能直接执行历史记录中的 argv。
    try:
        return await get_job_manager().create_and_start_run(
            command_id=run["command_id"], parameters=run["command_args"],
            enable_ui_events="--ui-events" in run["cli_argv"], name=run.get("name") or "", rerun_id=run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在或已删除")
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.post('/runs/{run_id}/messages')
async def send_session_message(run_id: str, payload: dict[str, Any]):
    try:
        return await get_job_manager().send_message(run_id, payload)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error))


@api_router.get('/runs/{run_id}/session')
def get_session_state(run_id: str, attempt: int | None = Query(None, ge=1)):
    run = get_db().get_run_attempt(run_id, attempt)
    if not run or run['command_id'] != 'duplex' or not run['command_args'].get('session'):
        raise HTTPException(status_code=404, detail='常驻会话不存在')
    return get_db().get_message_state(run_id, attempt)


@api_router.post('/runs/{run_id}/plan')
async def control_session_plan(run_id: str, payload: dict[str, Any]):
    try:
        return await get_job_manager().control_plan(run_id, payload)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error))


@api_router.get("/runs/{run_id}/analysis")
def get_run_analysis(run_id: str):
    db = get_db()
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"任务未找到: {run_id}")
    artifacts = db.get_artifacts(run_id)
    context = analysis_context(run, artifacts)
    context["file_states"] = context_file_states(context, run, artifacts, db)
    return context


def checked_analysis_file(path: str, run_id: str | None, role: str) -> Path:
    safe_path = get_artifact_manager().validate_safe_path(path)
    if run_id:
        context = get_run_analysis(run_id)
        expected = context["iq_path" if role == "iq" else "frames_path"]
        if not expected or artifact_path_key(path) != artifact_path_key(expected):
            raise HTTPException(status_code=409, detail="文件不属于当前任务的分析结果")
        state = context["file_states"][role]
        if not state["readable"]:
            raise HTTPException(status_code=404 if state["status"] == "missing" else 409, detail=state["message"])
    return safe_path


@api_router.get("/runs/{run_id}/logs")
def get_run_logs(run_id: str, limit: int = Query(2000, ge=1, le=10000), attempt: int | None = Query(None, ge=1)):
    if not get_db().get_run_attempt(run_id, attempt):
        raise HTTPException(status_code=404, detail="任务不存在或已删除")
    db = get_db()
    return db.get_logs(run_id, limit=limit, attempt=attempt)


@api_router.get("/runs/{run_id}/events")
def get_run_events(run_id: str, limit: int = Query(2000, ge=1, le=10000), attempt: int | None = Query(None, ge=1)):
    if not get_db().get_run_attempt(run_id, attempt):
        raise HTTPException(status_code=404, detail="任务不存在或已删除")
    db = get_db()
    return db.get_events(run_id, limit=limit, attempt=attempt)


@api_router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactItem])
def get_run_artifacts(run_id: str, attempt: int | None = Query(None, ge=1)):
    db = get_db()
    if not db.get_run_attempt(run_id, attempt):
        raise HTTPException(status_code=404, detail="执行记录不存在或已删除")
    return describe_artifacts(db.get_artifacts(run_id=run_id, attempt=attempt), db)


# ==========================================
# 4. 产物管理与信号分析 (Artifacts)
# ==========================================
@api_router.get("/artifacts", response_model=list[ArtifactItem])
def list_all_artifacts():
    db = get_db()
    return describe_artifacts(db.get_artifacts(), db)


@api_router.get("/artifacts/{artifact_id}")
def get_artifact_content(artifact_id: int):
    db = get_db()
    target = db.get_artifact(artifact_id)
    if not target:
        raise HTTPException(status_code=404, detail="产物记录未找到")
    state = describe_artifacts([target], db)[0]["file_state"]
    if not state["readable"]:
        raise HTTPException(status_code=404 if state["status"] == "missing" else 409, detail=state["message"])

    mgr = get_artifact_manager()
    try:
        safe_path = mgr.validate_safe_path(target["path"])
        if not safe_path.is_file():
            raise HTTPException(status_code=404, detail="文件在磁盘上已不存在")
        return FileResponse(safe_path, filename=safe_path.name)
    except PermissionError as err:
        raise HTTPException(status_code=403, detail=str(err))


@api_router.get("/analysis/window")
def get_signal_window(run_id: str, frame_time_s: float | None = Query(None, ge=0, allow_inf_nan=False),
                      window_ms: float = Query(5, ge=0.1, le=20, allow_inf_nan=False),
                      n_fft: int = Query(1024, ge=16, le=65536)):
    try:
        context = get_run_analysis(run_id)
        if not context["iq_path"]:
            raise HTTPException(status_code=409, detail="此任务未保存可关联的 IQ 文件")
        if frame_time_s is not None and context["frame_time_reference"] != "iq_start":
            raise HTTPException(status_code=409, detail="此任务的帧时间未与 IQ 文件建立对应关系")
        if any(context[key] is None for key in ("sample_rate", "center_frequency", "iq_format")):
            raise HTTPException(status_code=409, detail="任务记录缺少有效的采样率、中心频率或 IQ 格式")
        if frame_time_s is not None:
            if not context["frames_path"]:
                raise HTTPException(status_code=409, detail="此任务没有可关联的解调结果文件")
            checked_analysis_file(context["frames_path"], run_id, "frames")
        safe_path = checked_analysis_file(context["iq_path"], run_id, "iq")
        result = get_artifact_manager().compute_window(
            safe_path, iq_format=context["iq_format"], sample_rate=context["sample_rate"],
            center_freq=context["center_frequency"], frame_time_s=frame_time_s, window_ms=window_ms, n_fft=n_fft,
        )
        checked_analysis_file(context["iq_path"], run_id, "iq")
        if frame_time_s is not None:
            checked_analysis_file(context["frames_path"], run_id, "frames")
        return result
    except HTTPException:
        raise
    except (ValueError, TypeError, OSError) as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.get("/analysis/spectrum")
def get_signal_spectrum(
    path: str,
    iq_format: str = "cs8",
    sample_rate: float = Query(2500000.0, gt=0, allow_inf_nan=False),
    center_freq: float = Query(2480000000.0, allow_inf_nan=False),
    n_fft: int = Query(1024, ge=16, le=65536),
    offset_samples: int = Query(0, ge=0),
    run_id: str | None = None,
):
    mgr = get_artifact_manager()
    try:
        safe_path = checked_analysis_file(path, run_id, "iq")
        return mgr.compute_spectrum(
            safe_path,
            iq_format=iq_format,
            sample_rate=sample_rate,
            center_freq=center_freq,
            n_fft=n_fft,
            offset_samples=offset_samples,
        )
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.get("/analysis/constellation")
def get_signal_constellation(
    path: str,
    iq_format: str = "cs8",
    max_points: int = Query(2000, ge=1, le=10000),
    offset_samples: int = Query(0, ge=0),
    run_id: str | None = None,
):
    mgr = get_artifact_manager()
    try:
        safe_path = checked_analysis_file(path, run_id, "iq")
        return mgr.compute_constellation(
            safe_path,
            iq_format=iq_format,
            max_points=max_points,
            offset_samples=offset_samples,
        )
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.get("/analysis/frames")
def get_decoded_frames(path: str, limit: int = Query(500, ge=1, le=5000), run_id: str | None = None):
    mgr = get_artifact_manager()
    try:
        safe_path = checked_analysis_file(path, run_id, "frames")
        if not safe_path.is_file():
            raise HTTPException(status_code=404, detail="解调结果文件已删除或移动")
        frames = mgr.read_jsonl_frames(safe_path, limit=limit)
        if run_id:
            # 读取期间也可能启动下一次同名输出；再次核对后才返回历史任务的数据。
            checked_analysis_file(path, run_id, "frames")
        return {"total": len(frames), "frames": frames}
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


# ==========================================
# 5. 硬件设备状态 (Devices)
# ==========================================
@api_router.get("/devices", response_model=list[DeviceInfo])
def list_devices():
    device_mgr = get_device_manager()
    return device_mgr.list_devices()


@api_router.get("/devices/diagnostics", response_model=DeviceDiagnosticsResponse)
def get_device_diagnostics():
    device_mgr = get_device_manager()
    return device_mgr.diagnose(force_refresh=False)


@api_router.post("/devices/probe", response_model=DeviceDiagnosticsResponse)
def probe_devices():
    device_mgr = get_device_manager()
    return device_mgr.diagnose(force_refresh=True)


# ==========================================
# 6. 配置预设 (Configs / Presets)
# ==========================================
@api_router.get("/configs", response_model=list[PresetConfig])
def list_presets(command_id: str | None = None):
    db = get_db()
    return db.get_presets(command_id=command_id)


@api_router.post("/configs", response_model=PresetConfig)
def save_preset(req: SavePresetRequest):
    db = get_db()
    preset_id = req.id or f"preset_{uuid.uuid4().hex[:8]}"
    now_str = datetime.now(timezone.utc).isoformat()
    db.save_preset(
        preset_id=preset_id,
        name=req.name,
        command_id=req.command_id,
        values=req.values,
        updated_at=now_str,
    )
    return PresetConfig(
        id=preset_id,
        name=req.name,
        command_id=req.command_id,
        values=req.values,
        updated_at=now_str,
    )


# ==========================================
# 7. 静态配置与业务模板 (Templates)
# ==========================================
@api_router.get("/templates", response_model=list[TemplateItem])
def list_templates(command_id: str | None = None):
    mgr = get_template_manager()
    return mgr.list_templates(command_id=command_id)


@api_router.get("/templates/{name:path}", response_model=TemplateItem)
def get_template(name: str):
    mgr = get_template_manager()
    try:
        tpl = mgr.get_template(name)
    except (OSError, ValueError, TypeError) as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    if not tpl:
        raise HTTPException(status_code=404, detail=f"模板未找到: {name}")
    return tpl


@api_router.post("/templates/{name:path}", response_model=TemplateItem)
def save_template(name: str, req: SaveTemplateRequest):
    mgr = get_template_manager()
    try:
        return mgr.save_template(name, req.content)
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.delete("/templates/{name:path}")
def delete_template(name: str):
    mgr = get_template_manager()
    try:
        return mgr.delete_template(name)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except (OSError, ValueError, TypeError) as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


# ==========================================
# 8. WebSocket 实时事件流
# ==========================================
def _allowed_ws_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:  # CLI / 原生客户端不携带浏览器 Origin。
        return True
    parsed = urlsplit(origin)
    return parsed.scheme in ("http", "https") and (
        parsed.netloc == websocket.headers.get("host")
        or parsed.hostname in ("localhost", "127.0.0.1", "::1"))


@ws_router.websocket("/ws/runs/{run_id}")
async def ws_run_events(websocket: WebSocket, run_id: str):
    if not _allowed_ws_origin(websocket):
        await websocket.close(code=1008)
        return
    hub = get_event_hub()
    await hub.register(websocket, run_id=run_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await hub.send_text(websocket, "pong")
    except WebSocketDisconnect:
        await hub.unregister(websocket, run_id=run_id)
    except Exception:
        await hub.unregister(websocket, run_id=run_id)


@ws_router.websocket("/ws/events")
async def ws_global_events(websocket: WebSocket):
    if not _allowed_ws_origin(websocket):
        await websocket.close(code=1008)
        return
    hub = get_event_hub()
    await hub.register(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await hub.send_text(websocket, "pong")
    except WebSocketDisconnect:
        await hub.unregister(websocket)
    except Exception:
        await hub.unregister(websocket)


# ==========================================
# 9. SDR 实时频谱 (Spectrum)
# ==========================================
@api_router.post("/spectrum/start", response_model=SpectrumStatusResponse)
async def start_spectrum(req: SpectrumStartRequest):
    mgr = get_spectrum_manager()
    try:
        return await asyncio.to_thread(mgr.start_session, req)
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.post("/spectrum/stop", response_model=SpectrumStatusResponse)
async def stop_spectrum(session_id: str | None = None):
    mgr = get_spectrum_manager()
    try:
        return await asyncio.to_thread(mgr.stop_session, session_id)
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err))


@api_router.post("/spectrum/pause", response_model=SpectrumStatusResponse)
async def pause_spectrum(session_id: str):
    mgr = get_spectrum_manager()
    try:
        return await asyncio.to_thread(mgr.pause_session, session_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.post("/spectrum/resume", response_model=SpectrumStatusResponse)
async def resume_spectrum(session_id: str):
    mgr = get_spectrum_manager()
    try:
        return await asyncio.to_thread(mgr.resume_session, session_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.post("/spectrum/update")
async def update_spectrum(session_id: str, req: SpectrumUpdateRequest):
    mgr = get_spectrum_manager()
    try:
        return await asyncio.to_thread(mgr.update_parameters, session_id, req)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.get("/spectrum/status", response_model=SpectrumStatusResponse)
async def get_spectrum_status():
    mgr = get_spectrum_manager()
    return await asyncio.to_thread(mgr.get_status)


@api_router.post("/spectrum/record/start", response_model=SpectrumRecordStatus)
async def start_spectrum_record(session_id: str, req: SpectrumRecordStartRequest):
    mgr = get_spectrum_manager()
    try:
        return await asyncio.to_thread(mgr.start_recording, session_id, req)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err))
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.post("/spectrum/record/stop", response_model=SpectrumRecordStatus)
async def stop_spectrum_record(session_id: str):
    mgr = get_spectrum_manager()
    try:
        return await asyncio.to_thread(mgr.stop_recording, session_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.get("/spectrum/record/status", response_model=SpectrumRecordStatus)
async def get_spectrum_record_status(session_id: str | None = None):
    mgr = get_spectrum_manager()
    return await asyncio.to_thread(mgr.get_recording_status, session_id)


@ws_router.websocket("/ws/spectrum")
async def ws_spectrum_stream(websocket: WebSocket, session_id: str | None = None):
    if not _allowed_ws_origin(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    mgr = get_spectrum_manager()
    loop = asyncio.get_running_loop()
    session = await asyncio.to_thread(mgr.get_session, session_id)
    if session is None:
        await websocket.close(code=1008)
        return
    sub_queue = session.register_subscriber(loop)
    await asyncio.to_thread(mgr.touch_activity, session.session_id)

    async def push_loop():
        last_status = float('-inf')
        while True:
            now = loop.time()
            status = session.get_status().model_dump()
            if now - last_status >= 1 or status['state'] in ('error', 'stopped'):
                await websocket.send_json({'type': 'spectrum_status', **status})
                last_status = now
            if status['state'] in ('error', 'stopped'):
                await websocket.close(code=1000)
                return
            try:
                frame = await asyncio.wait_for(sub_queue.get(), timeout=.25)
                if session.accepts_display_frame(frame):
                    await websocket.send_json(frame)
            except asyncio.TimeoutError:
                pass

    async def receive_loop():
        while True:
            await websocket.receive_text()
            await asyncio.to_thread(mgr.touch_activity, session.session_id)

    push_task = asyncio.create_task(push_loop())
    receive_task = asyncio.create_task(receive_loop())
    try:
        await asyncio.wait((push_task, receive_task), return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, asyncio.CancelledError, Exception):
        pass
    finally:
        push_task.cancel()
        receive_task.cancel()
        session.unregister_subscriber(sub_queue)
        try:
            await asyncio.gather(push_task, receive_task, return_exceptions=True)
            await asyncio.to_thread(mgr.touch_activity, session.session_id)
        except asyncio.CancelledError:
            # ASGI may cancel the endpoint while a browser closes the socket.
            # Subscription cleanup must happen synchronously before any await.
            pass


# ==========================================
# 10. 盲扫与信道感知 (Blind Scan & Sensing)
# ==========================================
from pydantic import BaseModel, Field

class QuickScanRequest(BaseModel):
    scan_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    iq: str | None = None
    sdr: Literal["hackrf", "usrp"] | None = None
    sample_rate: float | None = Field(default=None, gt=0, le=64_000_000, allow_inf_nan=False)
    center_frequency: float | None = None
    start_freq: float = 2_400_000_000.0
    stop_freq: float = 2_483_500_000.0
    step_bw: float = Field(default=10_000_000.0, gt=0, le=64_000_000, allow_inf_nan=False)
    dwell: float = Field(default=0.05, gt=0, le=60, allow_inf_nan=False)
    passes: int = Field(default=2, ge=1, le=20)
    refine_dwell: float = Field(default=0.25, ge=0, le=60, allow_inf_nan=False)
    max_refine_candidates: int = Field(default=32, ge=0, le=256)
    settle_seconds: float = Field(default=0.01, ge=0, le=5, allow_inf_nan=False)
    discard_seconds: float = Field(default=0.002, ge=0, le=5, allow_inf_nan=False)
    duration: float = 0.5
    threshold_db: float = 10.0
    min_snr: float = 6.0
    bit_rate: float | None = None
    no_flrc: bool = False
    gain: float = 30.0
    iq_format: str = "cs8"
    serial: str | None = None
    device_index: int | None = None
    amp: bool = False
    bias_tee: bool = False
    args: str | None = None
    master_clock_rate: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    channel: int = Field(default=0, ge=0)
    antenna: str | None = None


class ExportPlanRequest(BaseModel):
    frequencies: list[float]
    sample_rate: float = 10_000_000.0
    center_frequency: float | None = None
    bit_rate: float = 325_000.0
    sync_words: list[str] = Field(default_factory=list)
    output_path: str = "configs/scanned_channels.toml"


_active_scans: dict[uuid.UUID, tuple[Any, asyncio.Task]] = {}


def _execute_quick_scan(req: QuickScanRequest, scanner):
    verify_flrc = not req.no_flrc
    candidate_rates = (req.bit_rate,) if req.bit_rate is not None else (325_000.0, 1_300_000.0, 650_000.0, 260_000.0)

    try:
        if req.iq:
            from pathlib import Path
            safe_path = Path(req.iq)
            if not safe_path.is_absolute():
                safe_path = PROJECT_ROOT / safe_path
            if not safe_path.is_file():
                raise HTTPException(status_code=404, detail=f"IQ 文件未找到: {req.iq}")

            cf = req.center_frequency or 2_405_000_000.0
            report = scanner.scan_iq_file(
                path=safe_path,
                sample_rate=req.sample_rate if req.sample_rate is not None else 10_000_000.0,
                center_frequency=cf,
                iq_format=req.iq_format,
                duration=req.duration,
                threshold_db=req.threshold_db,
                verify_flrc=verify_flrc,
                candidate_bit_rates=candidate_rates,
                min_snr=req.min_snr,
            )
            return report.to_dict()
        elif req.sdr:
            sdr_options = {
                "serial": req.serial,
                "device_index": req.device_index,
                "amp": req.amp,
                "bias_tee": req.bias_tee,
                "args": req.args,
                "channel": req.channel,
                "antenna": req.antenna,
                "master_clock_rate": req.master_clock_rate,
            }
            manager = get_device_manager()
            owner = f"scan:{req.scan_id}"
            selector = manager.resolve_selector(req.sdr, sdr_options)
            acquired, reason = manager.acquire_device(req.sdr, owner, selector)
            if not acquired:
                raise HTTPException(status_code=409, detail=reason)
            try:
                report = scanner.scan_live_sdr(
                    sdr=req.sdr,
                    start_freq=req.start_freq,
                    stop_freq=req.stop_freq,
                    step_bandwidth=req.step_bw,
                    sample_rate=req.sample_rate,
                    dwell_seconds=req.dwell,
                    gain=req.gain,
                    threshold_db=req.threshold_db,
                    verify_flrc=verify_flrc,
                    candidate_bit_rates=candidate_rates,
                    min_snr=req.min_snr,
                    sdr_options=sdr_options,
                    passes=req.passes, refine_dwell=req.refine_dwell,
                    max_refine_candidates=req.max_refine_candidates,
                    settle_seconds=req.settle_seconds, discard_seconds=req.discard_seconds,
                )
            finally:
                # Release in the capture thread, after the native context exits.
                manager.release_device(owner)
            return report.to_dict()
        else:
            raise HTTPException(status_code=400, detail="必须指定 iq 或 sdr 之一")
    except HTTPException:
        raise
    except Exception as err:
        from flrc_modem.scan.cancellation import ScanCancelled
        if isinstance(err, ScanCancelled):
            return {"status": "cancelled"}
        raise HTTPException(status_code=400, detail=str(err))


@api_router.post("/scan/quick")
async def quick_scan(req: QuickScanRequest):
    from flrc_modem.log import wide_event
    from flrc_modem.scan.cancellation import ScanCancelled
    from flrc_modem.scan.engine import SpectrumScanner

    if req.scan_id in _active_scans:
        raise HTTPException(status_code=409, detail="扫描 ID 已在运行")
    scanner = SpectrumScanner()
    worker = asyncio.create_task(asyncio.to_thread(_execute_quick_scan, req, scanner))
    _active_scans[req.scan_id] = (scanner, worker)
    event = wide_event("scan.run", scan_id=str(req.scan_id), entry_point="webui.scan.quick",
                       mode=req.sdr or "offline_iq", requested_sample_rate=req.sample_rate,
                       requested_master_clock_rate=req.master_clock_rate, step_bandwidth=req.step_bw,
                       start_freq=req.start_freq, stop_freq=req.stop_freq, dwell_seconds=req.dwell,
                       passes=req.passes, refine_dwell=req.refine_dwell,
                       max_refine_candidates=req.max_refine_candidates,
                       settle_seconds=req.settle_seconds, discard_seconds=req.discard_seconds)

    def cancelled_result():
        result = {"status": "cancelled"}
        if scanner.progress["report"] is not None:
            result["report"] = scanner.progress["report"]
        event.set("outcome", "cancelled")
        return result

    try:
        # Cancelling an HTTP handler must not leave its capture thread running.
        result = await asyncio.shield(worker)
        if scanner.cancel_event.is_set():
            return cancelled_result()
        return result
    except ScanCancelled:
        return cancelled_result()
    except asyncio.CancelledError:
        event.set("outcome", "cancelled")
        scanner.cancel()
        # Starlette may cancel the handler through an AnyIO cancel scope.
        # Shield cleanup from that scope so the thread keeps its device until exit.
        with CancelScope(shield=True):
            await asyncio.gather(asyncio.shield(worker), return_exceptions=True)
        raise
    except Exception as err:
        event.record_error(err)
        raise
    finally:
        snapshot = scanner.progress
        event.update(progress_updates=snapshot["revision"], last_stage=snapshot["stage"],
                     channels_found=(snapshot["report"] or {}).get("total_channels", 0))
        device_info = (snapshot["report"] or {}).get("device_info", {})
        event.update(actual_sample_rate=device_info.get("actual_sample_rate"),
                     actual_master_clock_rate=device_info.get("actual_master_clock_rate"),
                     effective_bandwidth_hz=device_info.get("effective_bandwidth_hz"),
                     hops_completed=device_info.get("hops_completed"),
                     overflow_count=device_info.get("overflow_count"),
                     short_capture_hops=device_info.get("short_capture_hops"))
        event.emit()
        _active_scans.pop(req.scan_id, None)


@api_router.get("/scan/{scan_id}/progress")
async def quick_scan_progress(scan_id: uuid.UUID, after_revision: int | None = Query(None, ge=0)):
    active = _active_scans.get(scan_id)
    if active is None:
        # Polling can arrive just before registration or after final cleanup.
        return {"scan_id": str(scan_id), "active": False, "revision": 0,
                "stage": "waiting", "percent": 0, "report": None}
    snapshot = active[0].progress
    return {"scan_id": str(scan_id), "active": True, **snapshot,
            "report": None if after_revision == snapshot["revision"] else snapshot["report"]}


@api_router.post("/scan/{scan_id}/stop")
async def stop_quick_scan(scan_id: uuid.UUID):
    active = _active_scans.get(scan_id)
    # An immediate stop/navigation can overtake the start request in transit.
    for _ in range(5):
        if active is not None:
            break
        await asyncio.sleep(0.05)
        active = _active_scans.get(scan_id)
    if active is None:
        return {"scan_id": str(scan_id), "stopped": False}
    scanner, worker = active
    scanner.cancel()
    # Acknowledge only after receive() and its device context have exited.
    await asyncio.gather(asyncio.shield(worker), return_exceptions=True)
    return {"scan_id": str(scan_id), "stopped": True}


@api_router.post("/scan/export-plan")
def export_scan_plan(req: ExportPlanRequest):
    from pathlib import Path
    out = Path(req.output_path)
    if not out.is_absolute():
        out = PROJECT_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    cf = req.center_frequency or ((min(req.frequencies) + max(req.frequencies)) / 2.0 if req.frequencies else 2480000000.0)
    lines = [
        "# 由 WebUI 信道感知面板导出的 FLRC 信道配置",
        f"# 生成时间: {datetime.now(timezone.utc).isoformat()}",
        "",
        "[template]",
        'schema_version = 1',
        'kind = "physical_config"',
        'title = "信道感知导出的 FLRC 配置"',
        f'description = "包含 {len(req.frequencies)} 个候选信道"',
        "",
        "[demod]",
        f"sample_rate = {req.sample_rate:.1f}",
        f"center_frequency = {cf:.1f}",
        "frequencies = [",
    ]
    for f in req.frequencies:
        lines.append(f"    {f:.1f},")
    lines.append("]")
    lines.append(f"bit_rate = {req.bit_rate:.1f}")
    lines.append(f"filter_cutoff = {req.bit_rate * 350 / 325:.1f}")
    if req.sync_words:
        lines.append(f'sync_word = "{req.sync_words[0]}"')
        if len(req.sync_words) > 1:
            sw_str = ", ".join(f'"{sw}"' for sw in req.sync_words[1:])
            lines.append(f"sync_words = [{sw_str}]")
    lines.append("")
    content = "\n".join(lines)
    out.write_text(content, encoding="utf-8")
    return {"path": str(out), "content": content}


# ==========================================
# 12. 调制与物理空口帧剖析 (Modulate & Transmit Workbench)
# ==========================================
@api_router.post("/modulate/preview", response_model=ModulatePreviewResponse)
def preview_modulation(req: ModulatePreviewRequest):
    """基于 FLRC 物理层标准计算空口帧剖析切片、时间预算并生成时域包络与瞬时频偏预览波形。"""
    import numpy as np
    from flrc_modem.phy.encoder import encode_packet
    from flrc_modem.modulation import ModulationConfig
    from flrc_modem.modulation.waveform import modulate_bits
    from flrc_modem.phy.sync import TIMING_PREAMBLE

    raw_payload_hex = req.payload_hex.strip().replace(" ", "").replace("0x", "")
    if not raw_payload_hex:
        raw_payload_hex = "0102030405060708"
    try:
        payload = bytes.fromhex(raw_payload_hex)
    except ValueError:
        raise HTTPException(status_code=400, detail="载荷不是有效的十六进制字符串")

    if len(payload) < 6 or len(payload) > 127:
        raise HTTPException(
            status_code=400,
            detail=f"载荷长度为 {len(payload)} 字节，SX1280 FLRC 硬件物理要求必须在 6..127 字节之间",
        )

    raw_sw_hex = req.sync_word.strip().replace(" ", "").replace("0x", "")
    try:
        sync_bytes = bytes.fromhex(raw_sw_hex)
        if len(sync_bytes) != 4:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=400, detail="同步字必须为 4 字节十六进制 (如 2bd36a07 或 b42b2bb4)")

    try:
        pkt = encode_packet(
            payload=payload,
            sync_word=sync_bytes,
            crc_initial=req.crc_initial,
            coding_rate=req.coding_rate,
            header_type=req.header_type,
            crc_bytes=req.crc_bytes,
        )
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"物理层编码失败: {err}")

    # 构建物理空口帧切片结构
    slices = [
        FrameSlice(
            name="AGC Preamble",
            bits=32,
            bytes=4.0,
            color="zinc",
            description="32-bit AGC 动态增益稳定前导 (0101...)",
            hex_value="55555555",
        ),
        FrameSlice(
            name="Timing Preamble",
            bits=21,
            bytes=2.625,
            color="purple",
            description="21-bit 符号定时同步前导 (011001001000010101110)",
            hex_value=np.packbits(TIMING_PREAMBLE).tobytes().hex(),
        ),
        FrameSlice(
            name="Sync Word",
            bits=32,
            bytes=4.0,
            color="cyan",
            description="4-Byte 物理差分空口同步字",
            hex_value=pkt.sync_word.hex(),
        ),
    ]

    if req.header_type == "variable" and pkt.header:
        slices.append(
            FrameSlice(
                name="Hardware Header",
                bits=16,
                bytes=2.0,
                color="blue",
                description=f"SX1280 可变长度头 (标明载荷 {len(payload)} 字节)",
                hex_value=pkt.header.hex(),
            )
        )

    slices.append(
        FrameSlice(
            name="Payload Data",
            bits=len(payload) * 8,
            bytes=float(len(payload)),
            color="emerald",
            description=f"原始物理业务载荷 ({len(payload)} 字节)",
            hex_value=pkt.payload.hex(),
        )
    )

    slices.append(
        FrameSlice(
            name="CRC Checksum",
            bits=req.crc_bytes * 8,
            bytes=float(req.crc_bytes),
            color="amber",
            description=f"硬件 CRC{req.crc_bytes * 8} 循环冗余校验",
            hex_value=pkt.crc.hex(),
        )
    )

    if req.coding_rate != "1":
        slices.append(
            FrameSlice(
                name="FEC Tail Bits",
                bits=6,
                bytes=0.75,
                color="rose",
                description=f"卷积码状态归零尾比特 (6 bits · CR={req.coding_rate})",
                hex_value="00",
            )
        )

    redundancy = len(pkt.bits) - sum(section.bits for section in slices)
    if redundancy > 0:
        slices.append(FrameSlice(name="FEC Redundancy", bits=redundancy, bytes=redundancy / 8,
                                 color="rose", description="FEC 编码冗余（分布在编码数据中）"))

    # 调制生成基带波形
    try:
        config = ModulationConfig(
            sample_rate=req.sample_rate,
            bit_rate=req.bit_rate,
            offset_hz=0,
            bandwidth_hz=None,
            ramp_time_us=req.ramp_time_us,
            bt=req.bt,
            coding_rate=req.coding_rate,
            amplitude=req.amplitude,
            repeat=1,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    waveform = modulate_bits(pkt.bits, config)
    total_samples = len(waveform)
    duration_us = round(total_samples / req.sample_rate * 1e6, 2)

    # 降采样生成平滑渲染点 (~200 点)
    num_points = 200
    sampled_indices = np.linspace(0, total_samples - 1, min(num_points, total_samples), dtype=int)
    envelope_samples = [round(float(abs(waveform[i])), 4) for i in sampled_indices]

    # 计算瞬时频偏
    angles = np.unwrap(np.angle(waveform))
    freq_dev = np.diff(angles) * req.sample_rate / (2 * np.pi)
    freq_dev = np.pad(freq_dev, (0, 1), mode="edge")
    freq_indices = sampled_indices
    frequency_samples = [round(float(freq_dev[i]), 1) for i in freq_indices]

    rf_budget = {
        "bit_rate": req.bit_rate,
        "bandwidth_hz": config.bandwidth_hz,
        "deviation_hz": config.deviation_hz,
        "sample_rate": req.sample_rate,
        "samples_per_symbol": round(config.sample_rate / config.bit_rate, 3),
        "ramp_samples": config.ramp_samples,
        "body_samples": total_samples - 2 * config.ramp_samples,
        "total_samples": total_samples,
        "air_time_us": duration_us,
    }

    return ModulatePreviewResponse(
        total_bits=len(pkt.bits),
        total_bytes=round(len(pkt.bits) / 8, 2),
        duration_us=duration_us,
        sync_word_hex=pkt.sync_word.hex(),
        header_hex=pkt.header.hex() if pkt.header else "",
        payload_hex=pkt.payload.hex(),
        crc_hex=pkt.crc.hex(),
        slices=slices,
        envelope_samples=envelope_samples,
        frequency_samples=frequency_samples,
        rf_budget=rf_budget,
    )

