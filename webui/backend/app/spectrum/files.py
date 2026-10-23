"""Bounded IQ file upload, discovery and streaming replay source."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import uuid

from anyio import CancelScope, open_file
from fastapi import APIRouter, HTTPException, Query, Request

from flrc_modem.io.iq import SAMPLE_BYTES, decode_samples
from ..artifacts.manager import get_artifact_manager
from ..paths import PROJECT_ROOT

router = APIRouter(prefix="/api/v1/spectrum/files")
IQ_SUFFIXES = {".cs8", ".cu8", ".cf32", ".iq", ".bin"}
MAX_UPLOAD_BYTES = 2 * 1024**3


def file_info(path: Path) -> dict:
    metadata = {}
    try:
        companion = get_artifact_manager().validate_safe_path(str(path) + ".json")
        if companion.is_file() and companion.stat().st_size <= 1024 * 1024:
            value = json.loads(companion.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                config = value.get("config")
                metadata = {**config, **value} if isinstance(config, dict) else value
    except (OSError, ValueError, PermissionError):
        pass
    fmt = metadata.get("iq_format", metadata.get("format", path.suffix.lower().lstrip(".")))
    fmt = {"fc32": "cf32", "sc8": "cs8"}.get(fmt, fmt) if isinstance(fmt, str) else "cs8"
    result = {"path": path.relative_to(PROJECT_ROOT).as_posix(), "name": path.name,
              "size_bytes": path.stat().st_size, "iq_format": fmt if fmt in SAMPLE_BYTES else "cs8"}
    for target, keys in {"sample_rate": ("sample_rate",), "center_freq": ("center_frequency", "center_freq", "frequency_hz", "frequency")}.items():
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
                result[target] = value
                break
    return result


@router.get("")
def list_files():
    directory = PROJECT_ROOT / "outputs"
    paths = []
    for path in directory.rglob("*"):
        if path.suffix.lower() not in IQ_SUFFIXES:
            continue
        try:
            safe = get_artifact_manager().validate_safe_path(path)
            if safe.is_file():
                paths.append((safe.stat().st_mtime, safe))
        except (OSError, PermissionError):
            continue
    paths.sort(key=lambda item: item[0], reverse=True)
    result = []
    for _, path in paths[:500]:
        try:
            result.append(file_info(path))
        except OSError:
            continue
    return result


@router.post("")
async def upload_file(request: Request, filename: str = Query(min_length=1, max_length=255)):
    name = Path(filename.replace("\\", "/")).name
    if Path(name).suffix.lower() not in IQ_SUFFIXES:
        raise HTTPException(400, "请选择 cs8、cu8、cf32、iq 或 bin 文件")
    name = re.sub(r"[^\w.\-]", "_", name)[-120:]
    directory = get_artifact_manager().validate_safe_path(PROJECT_ROOT / "outputs" / "spectrum_inputs")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{uuid.uuid4().hex[:12]}_{name}"
    temporary = target.with_name(target.name + ".part")
    size = 0
    try:
        async with await open_file(temporary, "xb") as stream:
            async for block in request.stream():
                size += len(block)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "IQ 文件不能超过 2 GiB")
                await stream.write(block)
        if not size:
            raise HTTPException(400, "IQ 文件为空")
        temporary.replace(target)
        return file_info(target)
    finally:
        with CancelScope(shield=True):
            temporary.unlink(missing_ok=True)


class FileSpectrumSource:
    """Validate before replacing the active session; read at most 32768 samples."""

    def __init__(self, path: str, iq_format: str, n_fft: int):
        self.path = get_artifact_manager().validate_safe_path(path)
        self.iq_format = iq_format
        self.sample_bytes = SAMPLE_BYTES[iq_format]
        if not self.path.is_file():
            raise ValueError("IQ 文件不存在，请重新选择")
        size = self.path.stat().st_size
        if size % self.sample_bytes:
            raise ValueError("IQ 文件含不完整 I/Q 采样对，请检查采样格式")
        if size < n_fft * self.sample_bytes:
            raise ValueError(f"IQ 文件至少需要 {n_fft} 个采样点，请减小 FFT 点数或选择更长的文件")
        with self.path.open("rb") as stream:
            decode_samples(stream.read(32768 * self.sample_bytes), iq_format)
        self.stream = None

    def open(self):
        self.stream = self.path.open("rb")
        return self

    def read(self):
        return decode_samples(self.stream.read(32768 * self.sample_bytes), self.iq_format).tobytes()

    def rewind(self):
        self.stream.seek(0)

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
