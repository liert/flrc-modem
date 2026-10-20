"""FastAPI 统一 API 路由定义 (/api/v1 与 /ws)。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import uuid
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from ..artifacts.manager import get_artifact_manager
from ..commands.registry import get_command_registry
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
    SavePresetRequest,
    SaveTemplateRequest,
    TemplateItem,
)
from ..storage.db import get_db
from ..templates.manager import get_template_manager

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
        )
        return record
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.get("/runs", response_model=list[RunRecord])
def list_runs(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    db = get_db()
    return db.get_runs(limit=limit, offset=offset)


@api_router.get("/runs/{run_id}", response_model=RunRecord)
async def get_run(run_id: str):
    db = get_db()
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"任务未找到: {run_id}")
    return run


@api_router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str):
    job_mgr = get_job_manager()
    success = await job_mgr.stop_run(run_id)
    return {"run_id": run_id, "stopped": success}


@api_router.get("/runs/{run_id}/logs")
def get_run_logs(run_id: str, limit: int = Query(2000, ge=1, le=10000)):
    db = get_db()
    return db.get_logs(run_id, limit=limit)


@api_router.get("/runs/{run_id}/events")
def get_run_events(run_id: str, limit: int = Query(2000, ge=1, le=10000)):
    db = get_db()
    return db.get_events(run_id, limit=limit)


@api_router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactItem])
async def get_run_artifacts(run_id: str):
    db = get_db()
    return db.get_artifacts(run_id=run_id)


# ==========================================
# 4. 产物管理与信号分析 (Artifacts)
# ==========================================
@api_router.get("/artifacts", response_model=list[ArtifactItem])
async def list_all_artifacts():
    db = get_db()
    return db.get_artifacts()


@api_router.get("/artifacts/{artifact_id}")
def get_artifact_content(artifact_id: int):
    db = get_db()
    target = db.get_artifact(artifact_id)
    if not target:
        raise HTTPException(status_code=404, detail="产物记录未找到")

    mgr = get_artifact_manager()
    try:
        safe_path = mgr.validate_safe_path(target["path"])
        if not safe_path.is_file():
            raise HTTPException(status_code=404, detail="文件在磁盘上已不存在")
        return FileResponse(safe_path, filename=safe_path.name)
    except PermissionError as err:
        raise HTTPException(status_code=403, detail=str(err))


@api_router.get("/analysis/spectrum")
def get_signal_spectrum(
    path: str,
    iq_format: str = "cs8",
    sample_rate: float = Query(2500000.0, gt=0, allow_inf_nan=False),
    center_freq: float = Query(2480000000.0, allow_inf_nan=False),
    n_fft: int = Query(1024, ge=16, le=65536),
    offset_samples: int = Query(0, ge=0),
):
    mgr = get_artifact_manager()
    try:
        safe_path = mgr.validate_safe_path(path)
        return mgr.compute_spectrum(
            safe_path,
            iq_format=iq_format,
            sample_rate=sample_rate,
            center_freq=center_freq,
            n_fft=n_fft,
            offset_samples=offset_samples,
        )
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.get("/analysis/constellation")
def get_signal_constellation(
    path: str,
    iq_format: str = "cs8",
    max_points: int = Query(2000, ge=1, le=10000),
    offset_samples: int = Query(0, ge=0),
):
    mgr = get_artifact_manager()
    try:
        safe_path = mgr.validate_safe_path(path)
        return mgr.compute_constellation(
            safe_path,
            iq_format=iq_format,
            max_points=max_points,
            offset_samples=offset_samples,
        )
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@api_router.get("/analysis/frames")
def get_decoded_frames(path: str, limit: int = Query(500, ge=1, le=5000)):
    mgr = get_artifact_manager()
    try:
        safe_path = mgr.validate_safe_path(path)
        frames = mgr.read_jsonl_frames(safe_path, limit=limit)
        return {"total": len(frames), "frames": frames}
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


@api_router.get("/templates/{name}", response_model=TemplateItem)
def get_template(name: str):
    mgr = get_template_manager()
    try:
        tpl = mgr.get_template(name)
    except (OSError, ValueError, TypeError) as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    if not tpl:
        raise HTTPException(status_code=404, detail=f"模板未找到: {name}")
    return tpl


@api_router.post("/templates/{name}", response_model=TemplateItem)
def save_template(name: str, req: SaveTemplateRequest):
    mgr = get_template_manager()
    try:
        return mgr.save_template(name, req.content)
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


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
