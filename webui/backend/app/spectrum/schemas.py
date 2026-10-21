"""SDR 实时频谱请求、响应与数据流模型定义。"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class SpectrumStartRequest(BaseModel):
    sdr: Literal["usrp", "hackrf", "sim"] = Field(
        default="hackrf", description="SDR 设备类型或纯软件仿真"
    )
    center_freq: float = Field(
        default=2480000000.0, description="中心频率 (Hz)", gt=0
    )
    sample_rate: float = Field(
        default=8000000.0, description="采样率 (Sps)", gt=0
    )
    n_fft: int = Field(default=1024, description="FFT 点数")
    window: Literal["hann", "hamming", "blackman", "rectangular"] = Field(
        default="hann", description="窗函数类型"
    )
    fft_average: int = Field(
        default=4, description="频谱平滑平均点数 (1 代表不平均)", ge=1, le=32
    )
    fps_limit: float = Field(
        default=15.0, description="显示刷新率上限 (FPS)", ge=1.0, le=30.0
    )
    dynamic_range_db: float = Field(
        default=80.0, description="显示动态范围 (dB)", ge=20.0, le=140.0
    )
    ref_level_dbfs: float = Field(
        default=0.0, description="参考参考电平基准 (dBFS)", ge=-60.0, le=20.0
    )

    # USRP 专用参数
    args: str | None = Field(default=None, description="USRP 设备定位参数 (如 addr=192.168.10.2)")
    master_clock_rate: float | None = Field(default=None, description="USRP 主时钟 (MCR, Hz)")
    gain: float | None = Field(default=30.0, description="USRP 接收模拟增益 (dB)")
    antenna: str | None = Field(default="TX/RX", description="USRP 天线端口")
    channel: int = Field(default=0, description="USRP 通道号", ge=0)

    # HackRF 专用参数
    serial: str | None = Field(default=None, description="HackRF 设备序列号")
    device_index: int | None = Field(default=None, description="HackRF 设备索引")
    lna_gain: int | None = Field(default=16, description="HackRF LNA 增益 (0..40, 步进 8)")
    vga_gain: int | None = Field(default=20, description="HackRF VGA 增益 (0..62, 步进 2)")
    amp: bool = Field(default=False, description="HackRF 射频前置放大器 (+14dB)")
    bias_tee: bool = Field(default=False, description="HackRF 天线馈电 (3.3V)")


class SpectrumUpdateRequest(BaseModel):
    center_freq: float | None = Field(default=None, description="动态修改中心频率 (Hz)")
    gain: float | None = Field(default=None, description="动态修改 USRP 增益 (dB)")
    lna_gain: int | None = Field(default=None, description="动态修改 HackRF LNA 增益")
    vga_gain: int | None = Field(default=None, description="动态修改 HackRF VGA 增益")
    amp: bool | None = Field(default=None, description="动态修改 HackRF 放大器开关")
    fft_average: int | None = Field(default=None, ge=1, le=32, description="动态修改平均次数")
    fps_limit: float | None = Field(default=None, ge=1.0, le=30.0, description="动态修改刷新率上限")
    dynamic_range_db: float | None = Field(default=None, description="动态修改显示范围")
    ref_level_dbfs: float | None = Field(default=None, description="动态修改参考电平")


class SpectrumStatusResponse(BaseModel):
    session_id: str | None = None
    state: Literal["idle", "running", "paused", "stopped", "error"] = "idle"
    device_type: str | None = None
    device_id: str | None = None
    device_name: str | None = None
    actual_center_freq: float | None = None
    actual_sample_rate: float | None = None
    actual_master_clock: float | None = None
    n_fft: int = 1024
    rbw_hz: float | None = None
    fps: float = 0.0
    hw_overflows: int = 0
    dsp_dropped_frames: int = 0
    client_dropped_frames: int = 0
    error_code: str | None = None
    error_message: str | None = None
