"""Pydantic 数据模型定义。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SDRBackend(str, Enum):
    USRP = "usrp"
    HACKRF = "hackrf"
    NONE = "none"


class CommandParameter(BaseModel):
    name: str
    type: Literal["string", "number", "integer", "boolean", "choice", "path", "list_number", "list_string"]
    description: str
    cli_flag: str
    required: bool = False
    default: Any = None
    choices: list[Any] | None = None
    min_value: float | None = None
    max_value: float | None = None
    category: str = "基本参数"


class CommandDefinition(BaseModel):
    id: str
    name: str
    category: str
    description: str
    subcommand: str
    requires_usrp: bool = False
    requires_sdr: bool = False
    pipeline_id: str | None = None
    parameters: list[CommandParameter]
    group_id: str | None = None
    group_name: str | None = None
    group_description: str | None = None
    mode_order: int = 0


class CreateRunRequest(BaseModel):
    command_id: str
    name: str = Field(default="", max_length=120)
    parameters: dict[str, Any] = Field(default_factory=dict)
    enable_ui_events: bool = True


class RunRecord(BaseModel):
    id: str
    attempt: int = 1
    attempt_created_at: str | None = None
    name: str = ""
    command_id: str
    command_args: dict[str, Any]
    cli_argv: list[str]
    status: JobStatus
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    requires_usrp: bool = False
    sdr_type: str | None = None
    pipeline_id: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class RenameRunRequest(BaseModel):
    name: str = Field(max_length=120)


class DeleteRunsRequest(BaseModel):
    run_ids: list[str] = Field(min_length=1, max_length=500)


class DeviceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    ERROR = "error"


class DeviceInfo(BaseModel):
    device_type: Literal["usrp", "hackrf"]
    id: str
    name: str
    details: dict[str, Any] = Field(default_factory=dict)
    status: DeviceStatus
    locked_by_run_id: str | None = None
    error_message: str | None = None


class PipelinePort(BaseModel):
    id: str
    label: str
    data_type: str


class PipelineNode(BaseModel):
    id: str
    type: Literal["source", "processor", "sink"]
    label: str
    description: str
    stage_id: str | None = None
    inputs: list[PipelinePort] = Field(default_factory=list)
    outputs: list[PipelinePort] = Field(default_factory=list)
    position: dict[str, float] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)


class PipelineEdge(BaseModel):
    id: str
    source: str
    target: str
    source_handle: str | None = None
    target_handle: str | None = None
    data_type: str | None = None
    label: str | None = None


class PipelineDefinition(BaseModel):
    id: str
    name: str
    description: str
    command_id: str
    nodes: list[PipelineNode]
    edges: list[PipelineEdge]


class ArtifactItem(BaseModel):
    id: int
    run_id: str
    attempt: int = 1
    stage_id: str | None = None
    path: str
    kind: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    file_state: dict[str, Any] | None = None


class PresetConfig(BaseModel):
    id: str
    name: str
    command_id: str
    values: dict[str, Any]
    updated_at: str


class SavePresetRequest(BaseModel):
    id: str | None = None
    name: str
    command_id: str
    values: dict[str, Any]


class TemplateItem(BaseModel):
    name: str
    path: str
    format: Literal["toml", "json"]
    category: Literal["physical_config", "message_payload", "rf_config"]
    title: str
    description: str
    applicable_commands: list[str]
    raw_content: str
    parsed: dict[str, Any] = Field(default_factory=dict)


class SaveTemplateRequest(BaseModel):
    content: str


class WebSocketEvent(BaseModel):
    type: str
    run_id: str
    seq: int
    stage_id: str | None = None
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SyncSchedulePreviewRequest(BaseModel):
    frequencies: list[int] = Field(default_factory=list)
    dwell_ms: float = 50.0
    tdd_mode: Literal["reception_triggered", "fixed_periodic"] = "reception_triggered"
    payload_bytes: int = 33
    bit_rate: int = 325000
    coding_rate: Literal["1/2", "3/4", "1"] = "3/4"
    clock_drift_ppm: float = 12.5
    cycles: int = 2


class SyncSchedulePreviewResponse(BaseModel):
    slot_dwell_ms: float
    total_slots: int
    cycle_duration_ms: float
    frequencies: list[int]
    timing_breakdown: dict[str, float]
    simulated_slots: list[dict[str, Any]]


class SyncParseTimelineRequest(BaseModel):
    run_id: str | None = None
    file_path: str | None = None

class ModulatePreviewRequest(BaseModel):
    payload_hex: str = Field(default="0102030405060708")
    sync_word: str = Field(default="2bd36a07")
    coding_rate: Literal["1/2", "3/4", "1"] = "3/4"
    header_type: Literal["variable", "fixed"] = "variable"
    crc_bytes: int = 3
    crc_initial: int | None = 0xFFFFFF
    bit_rate: float = 325000.0
    sample_rate: float = 2000000.0
    ramp_time_us: float = 12.0
    bt: float = 1.0
    amplitude: float = 0.7


class FrameSlice(BaseModel):
    name: str
    bits: int
    bytes: float
    color: str
    description: str
    hex_value: str = ""


class ModulatePreviewResponse(BaseModel):
    total_bits: int
    total_bytes: float
    duration_us: float
    sync_word_hex: str
    header_hex: str
    payload_hex: str
    crc_hex: str
    slices: list[FrameSlice]
    envelope_samples: list[float]
    frequency_samples: list[float]
    rf_budget: dict[str, Any]

