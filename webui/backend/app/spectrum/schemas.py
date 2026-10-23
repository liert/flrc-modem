"""SDR 实时频谱请求、响应与数据流模型定义。"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SpectrumStartRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    sdr: Literal["usrp", "hackrf", "file", "sim"] = Field(
        default="hackrf", description="SDR 设备类型或 IQ 文件输入"
    )
    iq_path: str | None = Field(default=None, max_length=1024, description="IQ 文件路径")
    iq_format: Literal["cs8", "cu8", "cf32"] = Field(default="cs8", description="文件采样格式")
    center_freq: float = Field(
        default=2480000000.0, description="中心频率 (Hz)", gt=0
    )
    sample_rate: float = Field(
        default=8000000.0, description="采样率 (Sps)", gt=0
    )
    n_fft: int = Field(default=1024, description="FFT 点数", ge=256, le=8192)
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
        default=100.0, description="显示动态范围 (dB)", ge=20.0, le=140.0
    )
    ref_level_dbfs: float = Field(
        default=0.0, description="显示参考电平 (dBFS)", ge=-240.0, le=20.0
    )

    # USRP 专用参数
    args: str | None = Field(default=None, description="USRP 设备定位参数 (如 addr=192.168.10.2)")
    master_clock_rate: float | None = Field(default=None, gt=0, description="USRP 主时钟 (MCR, Hz)")
    gain: float | None = Field(default=30.0, description="USRP 接收模拟增益 (dB)")
    antenna: str | None = Field(default="TX/RX", description="USRP 天线端口")
    channel: int = Field(default=0, description="USRP 通道号", ge=0)

    # HackRF 专用参数
    serial: str | None = Field(default=None, description="HackRF 设备序列号")
    device_index: int | None = Field(default=None, ge=0, description="HackRF 设备索引")
    lna_gain: int | None = Field(default=16, description="HackRF LNA 增益 (0..40, 步进 8)")
    vga_gain: int | None = Field(default=20, description="HackRF VGA 增益 (0..62, 步进 2)")
    amp: bool = Field(default=False, description="HackRF 射频前置放大器 (+14dB)")
    bias_tee: bool = Field(default=False, description="HackRF 天线馈电 (3.3V)")

    # 内置实时解调参数
    demod_enabled: bool = Field(default=False, description="是否启用内置实时解调通道")
    physical_template: str | None = Field(default=None, max_length=512, description="templates 内包含 [demod] 的物理模板名称")
    demod_center_freq: float | None = Field(default=None, description="解调中心频率 (Hz)，缺省为 SDR 当前中心频率")
    bit_rate: float | None = Field(default=None, description="FLRC 空中波特率 (bps)")
    coding_rate: Literal["1/2", "3/4", "1"] | None = Field(default=None, description="FEC 编码率")
    sync_word: str | None = Field(default=None, description="同步字 (16进制，如 b42b2bb4)")
    crc_initial: str | None = Field(default=None, description="CRC 初始值 (16进制，如 0xFFFFFF)")
    protocol: str = Field(default="auto", description="应用层协议解析器 (auto, dk32s, datalink, raw)")

    @field_validator("n_fft")
    @classmethod
    def validate_fft(cls, value):
        if value & (value - 1):
            raise ValueError("FFT 点数必须为 256..8192 的 2 次幂")
        return value

    @model_validator(mode="after")
    def validate_hardware(self):
        if self.sdr == "file" and not (self.iq_path and self.iq_path.strip()):
            raise ValueError("File 输入需要选择 IQ 文件")
        if self.serial and self.device_index is not None:
            raise ValueError("serial 与 device_index 互斥")
        if self.sdr == "hackrf":
            from flrc_modem.sdr.hackrf.config import HackRFConfig
            if not self.center_freq.is_integer():
                raise ValueError("HackRF 中心频率必须为整数 Hz")
            HackRFConfig(int(self.center_freq), sample_rate=self.sample_rate,
                         lna_gain=16 if self.lna_gain is None else self.lna_gain,
                         vga_gain=20 if self.vga_gain is None else self.vga_gain,
                         amp=self.amp, bias_tee=self.bias_tee)
        return self


class SpectrumUpdateRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    center_freq: float | None = Field(default=None, gt=0, description="动态修改中心频率 (Hz)")
    gain: float | None = Field(default=None, description="动态修改 USRP 增益 (dB)")
    lna_gain: int | None = Field(default=None, description="动态修改 HackRF LNA 增益")
    vga_gain: int | None = Field(default=None, description="动态修改 HackRF VGA 增益")
    amp: bool | None = Field(default=None, description="动态修改 HackRF 放大器开关")
    fft_average: int | None = Field(default=None, ge=1, le=32, description="动态修改平均次数")
    fps_limit: float | None = Field(default=None, ge=1.0, le=30.0, description="动态修改刷新率上限")
    dynamic_range_db: float | None = Field(default=None, ge=20, le=140, description="动态修改显示范围")
    ref_level_dbfs: float | None = Field(default=None, ge=-240, le=20, description="动态修改参考电平")
    demod_enabled: bool | None = Field(default=None, description="动态切换实时解调")
    physical_template: str | None = Field(default=None, max_length=512, description="动态应用物理模板；null 恢复默认解调配置")
    demod_center_freq: float | None = Field(default=None, description="动态更新解调中心频率 (Hz)")
    bit_rate: float | None = Field(default=None, description="动态修改波特率 (bps)")
    coding_rate: Literal["1/2", "3/4", "1"] | None = Field(default=None, description="动态修改 FEC 编码率")
    sync_word: str | None = Field(default=None, description="动态修改同步字 (16进制)")
    crc_initial: str | None = Field(default=None, description="动态修改 CRC 初始值 (16进制)")
    protocol: str | None = Field(default=None, description="动态切换应用层协议解析器")


class SpectrumRecordStartRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    duration_s: float | None = Field(default=2.0, ge=0.1, le=60.0, description="录制时长 (秒)，None 为手动启停")
    filename_prefix: str | None = Field(default="spectrum_capture", description="自定义文件名前缀")


class SpectrumRecordStatus(BaseModel):
    is_recording: bool = False
    duration_s: float | None = None
    elapsed_s: float = 0.0
    recorded_bytes: int = 0
    filepath: str | None = None
    run_id: str | None = None
    iq_format: str | None = None
    sample_rate: float | None = None
    center_freq: float | None = None
    samples_count: int = 0
    completed: bool = False
    error: str | None = None


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
    demod_enabled: bool = False
    demod_physical_template: str | None = None
    demod_center_freq: float | None = None
    demod_protocol: str | None = None
    demod_packets_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
