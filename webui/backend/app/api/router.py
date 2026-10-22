"""FastAPI 统一 API 路由定义 (/api/v1 与 /ws)。"""
from __future__ import annotations
import asyncio

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import uuid
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
    SyncSchedulePreviewRequest,
    SyncSchedulePreviewResponse,
    SyncParseTimelineRequest,
)
from ..storage.db import get_db
from ..storage.db import artifact_path_key
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
        db.rename_run(run_id, req.name.strip())
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在或已删除")
    record = db.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在或已删除")
    await get_event_hub().notify_run_change(run_id, "task.updated", {})
    return record


@api_router.post("/runs/delete")
async def delete_runs(req: DeleteRunsRequest):
    try:
        ids = get_db().delete_runs(req.run_ids)
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
async def get_run(run_id: str, attempt: int | None = Query(None, ge=1)):
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
    run = get_db().get_run(run_id)
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


# ==========================================
# 6. 配置预设 (Configs / Presets)
# ==========================================
@api_router.get("/configs", response_model=list[PresetConfig])
async def list_presets(command_id: str | None = None):
    db = get_db()
    return db.get_presets(command_id=command_id)


@api_router.post("/configs", response_model=PresetConfig)
async def save_preset(req: SavePresetRequest):
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
                await websocket.send_text("pong")
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
                await websocket.send_text("pong")
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
    iq: str | None = None
    sdr: str | None = None
    sample_rate: float = 10_000_000.0
    center_frequency: float | None = None
    start_freq: float = 2_400_000_000.0
    stop_freq: float = 2_483_500_000.0
    step_bw: float = 10_000_000.0
    dwell: float = 0.05
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
    channel: int = 0
    antenna: str | None = None


class ExportPlanRequest(BaseModel):
    frequencies: list[float]
    sample_rate: float = 10_000_000.0
    center_frequency: float | None = None
    bit_rate: float = 325_000.0
    sync_words: list[str] = Field(default_factory=list)
    output_path: str = "configs/scanned_channels.toml"


@api_router.post("/scan/quick")
async def quick_scan(req: QuickScanRequest):
    from flrc_modem.scan.engine import SpectrumScanner
    scanner = SpectrumScanner()
    verify_flrc = not req.no_flrc
    candidate_rates = (req.bit_rate,) if req.bit_rate is not None else (325_000.0, 1_300_000.0, 650_000.0, 260_000.0)

    try:
        if req.iq:
            from pathlib import Path
            safe_path = Path(req.iq)
            if not safe_path.is_file():
                safe_path = Path(__file__).resolve().parent.parent.parent.parent / req.iq
            if not safe_path.is_file():
                raise HTTPException(status_code=404, detail=f"IQ 文件未找到: {req.iq}")

            cf = req.center_frequency or 2_405_000_000.0
            report = await asyncio.to_thread(
                scanner.scan_iq_file,
                path=safe_path,
                sample_rate=req.sample_rate,
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
            }
            report = await asyncio.to_thread(
                scanner.scan_live_sdr,
                sdr=req.sdr,
                start_freq=req.start_freq,
                stop_freq=req.stop_freq,
                step_bandwidth=req.step_bw,
                dwell_seconds=req.dwell,
                gain=req.gain,
                threshold_db=req.threshold_db,
                verify_flrc=verify_flrc,
                candidate_bit_rates=candidate_rates,
                min_snr=req.min_snr,
                sdr_options=sdr_options,
            )
            return report.to_dict()
        else:
            raise HTTPException(status_code=400, detail="必须指定 iq 或 sdr 之一")
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.post("/scan/export-plan")
async def export_scan_plan(req: ExportPlanRequest):
    from pathlib import Path
    out = Path(req.output_path)
    if not out.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        out = repo_root / out
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
# 11. FHSS + TDD 跳频同步引擎 (Sync Workbench)
# ==========================================
@api_router.post("/sync/preview-schedule", response_model=SyncSchedulePreviewResponse)
async def preview_sync_schedule(req: SyncSchedulePreviewRequest):
    """计算理论 FHSS 跳频序列与 TDD 交互时序分解，并生成确定性时序仿真数据。"""
    from flrc_modem.sync.live import DEFAULT_DK32S_HOPPING_TABLE
    from flrc_modem.sync import calculate_flrc_packet_duration

    freqs = [int(f) for f in req.frequencies] if req.frequencies else list(DEFAULT_DK32S_HOPPING_TABLE)
    total_slots = len(freqs)
    dwell_ms = float(req.dwell_ms)
    dwell_s = dwell_ms / 1000.0
    cycle_duration_ms = total_slots * dwell_ms

    ground_tx_s = calculate_flrc_packet_duration(req.payload_bytes, bitrate=req.bit_rate, coding_rate=req.coding_rate)
    turnaround_guard_s = 0.000810  # 810 us 硬件收发转换与空中传播守候
    air_rx_s = calculate_flrc_packet_duration(req.payload_bytes, bitrate=req.bit_rate, coding_rate=req.coding_rate)
    slot_guard_s = max(0.0, dwell_s - ground_tx_s - turnaround_guard_s - air_rx_s)

    timing_breakdown = {
        "ground_tx_ms": round(ground_tx_s * 1000.0, 3),
        "turnaround_guard_ms": round(turnaround_guard_s * 1000.0, 3),
        "air_rx_ms": round(air_rx_s * 1000.0, 3),
        "slot_guard_ms": round(slot_guard_s * 1000.0, 3),
        "dwell_ms": dwell_ms,
    }

    drift_factor = 1.0 + (req.clock_drift_ppm * 1e-6)
    simulated_slots = []
    total_cycles = max(1, min(10, req.cycles))
    cur_hw_time = 0.0

    for cycle in range(total_cycles):
        for idx, freq in enumerate(freqs):
            slot_start = cur_hw_time
            slot_end = cur_hw_time + (dwell_s * drift_factor)
            cur_hw_time = slot_end
            next_idx = (idx + 1) % total_slots
            next_freq = freqs[next_idx]

            global_step = cycle * total_slots + idx
            if global_step == 0:
                state = "searching"
                conf = 0.35
            elif global_step == 1:
                state = "acquiring"
                conf = 0.75
            else:
                state = "locked"
                conf = min(0.99, 0.90 + (global_step * 0.005))

            simulated_slots.append({
                "slot_index": idx,
                "frequency_hz": freq,
                "start_time_s": round(slot_start, 6),
                "end_time_s": round(slot_end, 6),
                "duration_s": round(slot_end - slot_start, 6),
                "next_slot_index": next_idx,
                "next_frequency_hz": next_freq,
                "ground_tx_end_s": round(slot_start + ground_tx_s, 6),
                "turnaround_end_s": round(slot_start + ground_tx_s + turnaround_guard_s, 6),
                "air_rx_end_s": round(slot_start + ground_tx_s + turnaround_guard_s + air_rx_s, 6),
                "state": state,
                "confidence": round(conf, 3),
                "clock_drift_ppm": req.clock_drift_ppm,
            })

    return SyncSchedulePreviewResponse(
        slot_dwell_ms=dwell_ms,
        total_slots=total_slots,
        cycle_duration_ms=cycle_duration_ms,
        frequencies=freqs,
        timing_breakdown=timing_breakdown,
        simulated_slots=simulated_slots,
    )


@api_router.post("/sync/parse-timeline")
async def parse_sync_timeline(req: SyncParseTimelineRequest):
    """解析已完成的 sync 任务输出文件 (JSONL)，提取时钟漂移曲线、TDD 窗口统计与跳频轨迹。"""
    from pathlib import Path
    import json

    target_file = None
    if req.file_path:
        target_file = Path(req.file_path)
    elif req.run_id:
        db = get_db()
        run = db.get_run(req.run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"未找到运行记录: {req.run_id}")
        cmd_args = run.get("command_args", {})
        if cmd_args.get("output"):
            target_file = Path(cmd_args["output"])
        else:
            target_file = Path("outputs/sync_frames.jsonl")

    if not target_file:
        target_file = Path("outputs/sync_frames.jsonl")

    if not target_file.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        target_file = repo_root / target_file

    if not target_file.exists():
        raise HTTPException(status_code=404, detail=f"同步输出文件不存在: {target_file}")

    frames = []
    drift_series = []
    state_counts: dict[str, int] = {}
    tdd_counts: dict[str, int] = {}
    slot_counts: dict[int, int] = {}
    latest_snap = None

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue

                snap = data.get("sync")
                if snap:
                    latest_snap = snap
                    st = snap.get("state", "unknown")
                    state_counts[st] = state_counts.get(st, 0) + 1
                    phase = snap.get("tdd_phase", "unknown")
                    tdd_counts[phase] = tdd_counts.get(phase, 0) + 1
                    s_idx = snap.get("slot_index", 0)
                    slot_counts[s_idx] = slot_counts.get(s_idx, 0) + 1

                    t_s = data.get("time_s", snap.get("hw_time_s", 0.0))
                    drift_series.append({
                        "time_s": t_s,
                        "drift_ppm": snap.get("clock_drift_ppm", 0.0),
                        "confidence": snap.get("confidence", 0.0),
                        "state": st,
                        "slot_index": s_idx,
                        "frequency_hz": snap.get("frequency_hz", 0),
                    })

                frames.append({
                    "time_s": data.get("time_s", 0.0),
                    "cfo_hz": data.get("cfo_hz", 0.0),
                    "sample_power_db": data.get("sample_power_db", 0.0),
                    "sync": snap,
                    "application": data.get("application"),
                })
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"读取解析失败: {err}")

    return {
        "file_path": str(target_file),
        "total_frames": len(frames),
        "state_counts": state_counts,
        "tdd_counts": tdd_counts,
        "slot_counts": slot_counts,
        "drift_series": drift_series[-200:],
        "frames": frames[-100:],
        "latest_snapshot": latest_snap,
    }

