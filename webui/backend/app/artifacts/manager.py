"""产物管理与信号分析引擎：安全文件访问、元数据提取、FFT 频谱与星座图计算。"""
from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from typing import Any
import numpy as np

from ..storage.db import get_db
from ..paths import PROJECT_ROOT
from flrc_modem.io.iq import decode_samples, SAMPLE_BYTES

ALLOWED_DIRS = [
    PROJECT_ROOT / "outputs",
    PROJECT_ROOT / "templates",
]


class ArtifactManager:
    """管理分析产物、防止路径穿越并提供高频信号降采样计算。"""

    def __init__(self):
        for directory in ALLOWED_DIRS:
            directory.mkdir(parents=True, exist_ok=True)

    def validate_safe_path(self, raw_path: str | Path) -> Path:
        """严格校验文件访问路径，防止目录穿越攻击。"""
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        else:
            candidate = candidate.resolve()

        # 检查是否在允许的目录白名单内
        is_safe = any(
            candidate == allowed or allowed in candidate.parents
            for allowed in ALLOWED_DIRS
        )
        if not is_safe:
            raise PermissionError(f"文件访问受限，不允许访问安全目录之外的路径: {candidate}")

        return candidate

    def get_artifact_details(self, path: Path) -> dict[str, Any]:
        """获取文件基本属性与解析元数据。"""
        safe_path = self.validate_safe_path(path)
        if not safe_path.exists():
            return {"exists": False, "path": str(safe_path)}

        stat = safe_path.stat()
        info: dict[str, Any] = {
            "exists": True,
            "path": str(safe_path.relative_to(PROJECT_ROOT)),
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
            "suffix": safe_path.suffix.lower(),
        }

        # 关联的 .json 元数据
        meta_companion = Path(str(safe_path) + ".json")
        if meta_companion.exists() and meta_companion.is_file():
            try:
                info["companion_metadata"] = json.loads(meta_companion.read_text(encoding="utf-8"))
            except Exception:
                pass

        return info

    def read_jsonl_frames(self, path: Path, limit: int = 500) -> list[dict[str, Any]]:
        """安全读取解调帧 JSONL 记录。"""
        safe_path = self.validate_safe_path(path)
        if not safe_path.is_file():
            return []

        if not 1 <= limit <= 5000:
            raise ValueError("limit 必须为 1..5000")
        frames = []
        with safe_path.open("r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                if idx >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if (isinstance(record, dict) and record.get("crc_ok") is True
                            and all(isinstance(record.get(key), (int, float))
                                    and not isinstance(record[key], bool) and isfinite(record[key])
                                    for key in ("time_s", "frequency_hz"))
                            and isinstance(record.get("payload_hex"), str)):
                        frames.append(record)
                except Exception:
                    pass
        return frames

    def read_iq_samples(
        self,
        path: Path,
        iq_format: str = "cs8",
        max_samples: int = 65536,
        offset_samples: int = 0,
    ) -> np.ndarray:
        """从本地 IQ 文件读取复数采样点数组 (numpy.complex64)。"""
        safe_path = self.validate_safe_path(path)
        if not safe_path.is_file():
            raise FileNotFoundError(f"IQ 文件未找到: {safe_path}")

        if iq_format not in SAMPLE_BYTES:
            raise ValueError(f"不支持的 IQ 格式: {iq_format}")
        if not 1 <= max_samples <= 1048576 or offset_samples < 0:
            raise ValueError("采样数必须为 1..1048576，偏移必须非负")
        sample_bytes = SAMPLE_BYTES[iq_format]
        file_size = safe_path.stat().st_size
        if file_size % sample_bytes:
            raise ValueError("IQ 文件含不完整 I/Q 采样对")
        total_samples = file_size // sample_bytes
        if offset_samples >= total_samples:
            return np.empty(0, dtype=np.complex64)

        read_count = min(max_samples, total_samples - offset_samples)

        with safe_path.open("rb") as f:
            f.seek(offset_samples * sample_bytes)
            raw = f.read(read_count * sample_bytes)
        return decode_samples(raw, iq_format)

    def compute_spectrum(
        self,
        path: Path,
        iq_format: str = "cs8",
        n_fft: int = 1024,
        sample_rate: float = 2500000.0,
        center_freq: float = 2480000000.0,
        offset_samples: int = 0,
    ) -> dict[str, Any]:
        """计算功率谱密度 (PSD) 及峰值标定。"""
        if type(n_fft) is not int or not 16 <= n_fft <= 65536 or n_fft & (n_fft - 1):
            raise ValueError("n_fft 必须为 16..65536 的 2 的幂")
        if not isfinite(sample_rate) or sample_rate <= 0 or not isfinite(center_freq):
            raise ValueError("采样率必须为有限正数，中心频率必须有限")
        samples = self.read_iq_samples(path, iq_format, max_samples=n_fft * 4, offset_samples=offset_samples)
        if len(samples) < n_fft:
            return {"error": "样本数量不足以计算 FFT", "points": []}

        # 汉宁窗加窗与 FFT
        window = np.hanning(n_fft)
        segment = samples[:n_fft] * window
        spectrum = np.fft.fftshift(np.fft.fft(segment, n_fft))
        power = np.abs(spectrum) ** 2 / (sample_rate * np.sum(window**2))
        power_db = 10 * np.log10(np.maximum(power, 1e-12))

        # 频率坐标 (以实际载频为基准)
        freq_axis = np.fft.fftshift(np.fft.fftfreq(n_fft, 1.0 / sample_rate)) + center_freq

        # 寻找峰值
        peak_idx = int(np.argmax(power_db))
        peak_freq = float(freq_axis[peak_idx])
        peak_power = float(power_db[peak_idx])

        points = [[round(float(f), 1), round(float(p), 2)] for f, p in zip(freq_axis, power_db)]
        return {
            "center_frequency": center_freq,
            "sample_rate": sample_rate,
            "n_fft": n_fft,
            "peak_frequency_hz": peak_freq,
            "peak_power_db": peak_power,
            "points": points,
        }

    def compute_constellation(
        self,
        path: Path,
        iq_format: str = "cs8",
        max_points: int = 2000,
        offset_samples: int = 0,
    ) -> dict[str, Any]:
        """获取降采样星座点集 (I, Q) 及信号幅度包络指标。"""
        if type(max_points) is not int or not 1 <= max_points <= 10000:
            raise ValueError("max_points 必须为 1..10000")
        samples = self.read_iq_samples(path, iq_format, max_samples=max_points * 4, offset_samples=offset_samples)
        if len(samples) == 0:
            return {"points": [], "evm_estimate_percent": None, "rendered_points": 0,
                    "total_samples": 0, "mean_amplitude": None}

        # 均匀抽样至 max_points 以内
        step = max(1, len(samples) // max_points)
        subset = samples[::step][:max_points]

        points = [[round(float(s.real), 4), round(float(s.imag), 4)] for s in subset]

        # 估算平均功率与 EVM (归一化误差向量幅度)
        amplitudes = np.abs(subset)
        mean_amp = float(np.mean(amplitudes)) if len(amplitudes) > 0 else 0.0
        evm_est = float(np.std(amplitudes) / (mean_amp + 1e-6) * 100) if mean_amp > 0 else 0.0

        return {
            "total_samples": len(samples),
            "rendered_points": len(points),
            "mean_amplitude": round(mean_amp, 4),
            "evm_estimate_percent": round(evm_est, 2),
            "points": points,
        }


_ARTIFACT_MGR: ArtifactManager | None = None


def get_artifact_manager() -> ArtifactManager:
    global _ARTIFACT_MGR
    if _ARTIFACT_MGR is None:
        _ARTIFACT_MGR = ArtifactManager()
    return _ARTIFACT_MGR
