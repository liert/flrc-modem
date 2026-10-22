"""信道候选与盲扫报告数据结构。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from typing import Any


@dataclass
class ChannelCandidate:
    """单个检测到的载波/信道候选。"""
    frequency_hz: float
    bandwidth_hz: float
    peak_power_dbfs: float
    snr_db: float
    noise_floor_dbfs: float
    burst_count: int = 1
    duty_cycle: float = 0.0
    is_flrc: bool = False
    flrc_confidence: float = 0.0
    sync_words: list[str] = field(default_factory=list)
    bit_rate: float | None = None
    protocol_hint: str = "未知能量"
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def frequency_mhz(self) -> float:
        return round(self.frequency_hz / 1e6, 4)

    @property
    def bandwidth_khz(self) -> float:
        return round(self.bandwidth_hz / 1e3, 1)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["frequency_mhz"] = self.frequency_mhz
        data["bandwidth_khz"] = self.bandwidth_khz
        return data


@dataclass
class ScanReport:
    """全频段或单文件盲扫汇总报告。"""
    scan_mode: str  # "offline_iq" | "live_sdr"
    start_freq_hz: float
    stop_freq_hz: float
    noise_floor_dbfs: float
    threshold_db: float
    channels: list[ChannelCandidate] = field(default_factory=list)
    duration_seconds: float = 0.0
    sample_rate: float = 0.0
    center_frequency_hz: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    device_info: dict[str, Any] = field(default_factory=dict)

    @property
    def flrc_channels(self) -> list[ChannelCandidate]:
        return [c for c in self.channels if c.is_flrc]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "scan_mode": self.scan_mode,
            "start_freq_mhz": round(self.start_freq_hz / 1e6, 3),
            "stop_freq_mhz": round(self.stop_freq_hz / 1e6, 3),
            "sample_rate_sps": self.sample_rate,
            "center_frequency_mhz": round(self.center_frequency_hz / 1e6, 3),
            "noise_floor_dbfs": round(self.noise_floor_dbfs, 2),
            "threshold_db": round(self.threshold_db, 2),
            "total_channels": len(self.channels),
            "flrc_channels_count": len(self.flrc_channels),
            "channels": [c.to_dict() for c in self.channels],
            "device_info": self.device_info,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_toml_config(
        self,
        title: str = "自动盲扫生成的 FLRC 信道配置",
        sample_rate: float | None = None,
        center_frequency: float | None = None,
        only_flrc: bool = True,
    ) -> str:
        """导出为 flrc demod / rx 可直接使用的 TOML 物理层配置文件。"""
        candidates = self.flrc_channels if only_flrc and self.flrc_channels else self.channels
        freqs = [c.frequency_hz for c in candidates]
        if not freqs:
            freqs = [self.center_frequency_hz or 2480000000.0]

        sr = sample_rate if sample_rate is not None else (self.sample_rate or 10000000.0)
        cf = center_frequency if center_frequency is not None else (
            self.center_frequency_hz if self.center_frequency_hz > 0 else (min(freqs) + max(freqs)) / 2.0
        )

        # 确定主调制比特率
        bit_rate = 325000.0
        for c in candidates:
            if c.is_flrc and c.bit_rate:
                bit_rate = c.bit_rate
                break

        # 收集有效同步字候选
        sync_words: list[str] = []
        for c in candidates:
            for sw in c.sync_words:
                if sw not in sync_words:
                    sync_words.append(sw)

        lines = [
            "# 由 flrc scan 自动感知生成的信道规划配置",
            f"# 生成时间: {self.timestamp}",
            "",
            "[template]",
            'schema_version = 1',
            'kind = "physical_config"',
            f'title = "{title}"',
            f'description = "盲扫频段: {self.start_freq_hz / 1e6:.1f} - {self.stop_freq_hz / 1e6:.1f} MHz，共 {len(candidates)} 个候选信道"',
            "",
            "[demod]",
            f"sample_rate = {sr:.1f}",
            f"center_frequency = {cf:.1f}",
            "frequencies = [",
        ]
        for f in freqs:
            lines.append(f"    {f:.1f},")
        lines.append("]")
        lines.append(f"bit_rate = {bit_rate:.1f}")
        lines.append(f"filter_cutoff = {bit_rate * 350 / 325:.1f}")

        if sync_words:
            lines.append(f'sync_word = "{sync_words[0]}"')
            if len(sync_words) > 1:
                sw_str = ", ".join(f'"{sw}"' for sw in sync_words[1:])
                lines.append(f"sync_words = [{sw_str}]")

        lines.append("")
        return "\n".join(lines)

    def format_table(self, use_color: bool = True) -> str:
        """格式化输出专业易读的终端表格报告。"""
        reset = "\033[0m" if use_color else ""
        bold = "\033[1m" if use_color else ""
        green = "\033[32m" if use_color else ""
        yellow = "\033[33m" if use_color else ""
        cyan = "\033[36m" if use_color else ""
        dim = "\033[2m" if use_color else ""

        mode_desc = "离线 IQ 采样文件" if self.scan_mode == "offline_iq" else f"SDR 实时扫频 ({self.scan_mode})"
        width = 96
        sep_double = "=" * width
        sep_single = "-" * width

        out = [
            f"{bold}{sep_double}{reset}",
            f"{bold}FLRC 盲扫与信道感知报告{reset}",
            f"扫描模式: {mode_desc} | 频段: {self.start_freq_hz / 1e6:.3f} - {self.stop_freq_hz / 1e6:.3f} MHz",
            f"底噪基准: {self.noise_floor_dbfs:.1f} dBFS | 判决门限: +{self.threshold_db:.1f} dB (绝对门限: {self.noise_floor_dbfs + self.threshold_db:.1f} dBFS)",
            f"{sep_double}",
            f"{bold}{'CH':<4} {'中心频率 (MHz)':<15} {'带宽 (kHz)':<12} {'峰值 (dBFS)':<13} {'SNR (dB)':<10} {'突发数':<8} {'协议 / 调制类型':<18} {'同步字 (Sync Word)':<16}{reset}",
            sep_single,
        ]

        if not self.channels:
            out.append(f"{dim}  (未检测到高于判决门限的载波信道){reset}")
        else:
            for idx, ch in enumerate(self.channels, 1):
                sync_str = ", ".join(ch.sync_words[:2]) if ch.sync_words else "--"
                if len(ch.sync_words) > 2:
                    sync_str += "..."
                type_style = green if ch.is_flrc else (yellow if "宽带" in ch.protocol_hint else cyan)
                out.append(
                    f"{idx:02d}   "
                    f"{ch.frequency_mhz:<15.4f} "
                    f"{ch.bandwidth_khz:<12.1f} "
                    f"{ch.peak_power_dbfs:<13.1f} "
                    f"{ch.snr_db:<10.1f} "
                    f"{ch.burst_count:<8d} "
                    f"{type_style}{ch.protocol_hint:<18}{reset} "
                    f"{sync_str:<16}"
                )

        out.append(sep_single)
        flrc_cnt = len(self.flrc_channels)
        out.append(f"汇总统计: 共检测到 {len(self.channels)} 个载波信道，其中 {bold}{flrc_cnt}{reset} 个符合 FLRC 调制特征。")
        out.append(f"{sep_double}")
        return "\n".join(out)
