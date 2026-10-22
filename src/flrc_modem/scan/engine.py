"""盲扫引擎：支持离线 IQ 样本/文件分析与 HackRF / USRP 步进扫频感知。"""
from __future__ import annotations

import io
from math import ceil, isfinite
from pathlib import Path
from threading import Event
import time
from typing import Any, Callable
import numpy as np

from ..io.iq import SAMPLE_BYTES, decode_samples
from ..log import get_logger, wide_event
from .cancellation import ScanCancelled, check_cancelled
from .flrc_verifier import DEFAULT_BIT_RATES, verify_flrc_candidate
from .models import ChannelCandidate, ScanReport
from .psd import compute_psd_waterfall, detect_spectral_peaks, estimate_adaptive_noise_floor

logger = get_logger("flrc.scan")


class SpectrumScanner:
    """FLRC 2.4GHz 全频段自动盲扫与信道感知引擎。"""

    def __init__(self):
        self.cancel_event = Event()
        self._started = time.monotonic()
        self.progress: dict[str, Any] = {"revision": 0, "stage": "starting", "percent": 0, "report": None}

    def _publish(self, stage: str, percent: float, report: ScanReport | None = None, **details) -> None:
        # Replace the entire serialized snapshot; API readers never see lists
        # being changed by the capture/analysis thread.
        self.progress = {
            "revision": self.progress["revision"] + 1,
            "stage": stage,
            "percent": round(percent, 1),
            "elapsed_seconds": round(time.monotonic() - self._started, 1),
            "report": report.to_dict() if report is not None else self.progress["report"],
            **details,
        }

    def cancel(self) -> None:
        self.cancel_event.set()

    def scan_iq_samples(
        self,
        samples: np.ndarray,
        sample_rate: float,
        center_frequency: float,
        threshold_db: float = 10.0,
        verify_flrc: bool = True,
        candidate_bit_rates: tuple[float, ...] = DEFAULT_BIT_RATES,
        min_snr: float = 6.0,
        n_fft: int = 2048,
        on_progress: Callable[..., None] | None = None,
    ) -> ScanReport:
        """对单段已加载的复数 IQ 样本进行盲扫分析。"""
        check_cancelled(self.cancel_event)
        if not isfinite(sample_rate) or sample_rate <= 0 or not isfinite(center_frequency):
            raise ValueError("采样率必须为有限正数，中心频率必须为有限数")
        if len(samples) == 0:
            raise ValueError("IQ 样本数组不能为空")

        publish = on_progress or self._publish
        publish("analyzing", 5)
        freqs, max_psd, avg_psd, time_slices, rbw = compute_psd_waterfall(
            samples, n_fft=n_fft, sample_rate=sample_rate, center_freq=center_frequency
        )
        check_cancelled(self.cancel_event)
        noise_floor = estimate_adaptive_noise_floor(avg_psd)

        raw_candidates = detect_spectral_peaks(
            freqs,
            max_psd,
            avg_psd,
            noise_floor_dbfs=noise_floor,
            threshold_db=threshold_db,
            dc_center_freq=center_frequency,
            time_slices=time_slices,
        )

        raw_candidates = [raw for raw in raw_candidates if raw["snr_db"] >= min_snr]
        pending = [ChannelCandidate(
            frequency_hz=raw["frequency_hz"], bandwidth_hz=raw["bandwidth_hz"],
            peak_power_dbfs=raw["peak_power_dbfs"], snr_db=raw["snr_db"],
            noise_floor_dbfs=noise_floor, burst_count=raw["burst_count"], duty_cycle=raw["duty_cycle"],
            protocol_hint="待 FLRC 检验" if verify_flrc else "未检验 (仅能量感知)",
        ) for raw in raw_candidates]
        channels: list[ChannelCandidate] = []

        def snapshot() -> ScanReport:
            return ScanReport(
                scan_mode="offline_iq", start_freq_hz=center_frequency - sample_rate / 2.0,
                stop_freq_hz=center_frequency + sample_rate / 2.0, noise_floor_dbfs=noise_floor,
                threshold_db=threshold_db, channels=channels + pending[len(channels):],
                duration_seconds=round(len(samples) / sample_rate, 4),
                sample_rate=sample_rate, center_frequency_hz=center_frequency,
                device_info={"partial": len(channels) < len(pending)},
            )

        if not verify_flrc:
            # Energy-only candidates already contain the final result. Avoid
            # rebuilding/serializing the full channel list once per carrier.
            channels.extend(pending)
            report = snapshot()
            publish("completed", 100, report, candidates_completed=len(channels), candidates_total=len(channels))
            wide_event("scan.completed", mode="offline_iq", channels_found=len(channels),
                       flrc_found=0, noise_floor_dbfs=noise_floor).emit()
            return report

        publish("verifying" if verify_flrc else "analyzing", 10, snapshot(),
                candidates_completed=0, candidates_total=len(pending))
        for raw in raw_candidates:
            check_cancelled(self.cancel_event)

            target_f = raw["frequency_hz"]
            bw = raw["bandwidth_hz"]

            if verify_flrc:
                ver_res = verify_flrc_candidate(
                    samples,
                    sample_rate=sample_rate,
                    center_frequency=center_frequency,
                    target_frequency=target_f,
                    bandwidth_hz=bw,
                    candidate_bit_rates=candidate_bit_rates,
                    cancel_event=self.cancel_event,
                )
                cand = ChannelCandidate(
                    frequency_hz=target_f,
                    bandwidth_hz=bw,
                    peak_power_dbfs=raw["peak_power_dbfs"],
                    snr_db=raw["snr_db"],
                    noise_floor_dbfs=noise_floor,
                    burst_count=max(raw["burst_count"], ver_res.get("burst_count", 1)),
                    duty_cycle=raw["duty_cycle"],
                    is_flrc=ver_res["is_flrc"],
                    flrc_confidence=ver_res["flrc_confidence"],
                    sync_words=ver_res["sync_words"],
                    bit_rate=ver_res["bit_rate"],
                    protocol_hint=ver_res["protocol_hint"],
                    details=ver_res,
                )
            else:
                cand = ChannelCandidate(
                    frequency_hz=target_f,
                    bandwidth_hz=bw,
                    peak_power_dbfs=raw["peak_power_dbfs"],
                    snr_db=raw["snr_db"],
                    noise_floor_dbfs=noise_floor,
                    burst_count=raw["burst_count"],
                    duty_cycle=raw["duty_cycle"],
                    is_flrc=False,
                    flrc_confidence=0.0,
                    sync_words=[],
                    bit_rate=None,
                    protocol_hint="未检验 (仅能量感知)",
                )
            channels.append(cand)
            publish("verifying" if verify_flrc else "analyzing",
                    10 + 90 * len(channels) / len(pending), snapshot(),
                    candidates_completed=len(channels), candidates_total=len(pending))

        check_cancelled(self.cancel_event)
        report = snapshot()
        publish("completed", 100, report,
                candidates_completed=len(channels), candidates_total=len(pending))

        event = wide_event(
            "scan.completed",
            mode="offline_iq",
            channels_found=len(channels),
            flrc_found=len(report.flrc_channels),
            noise_floor_dbfs=noise_floor,
        )
        event.emit()
        return report

    def scan_iq_file(
        self,
        path: Path,
        sample_rate: float,
        center_frequency: float,
        iq_format: str = "cs8",
        duration: float = 0.5,
        threshold_db: float = 10.0,
        verify_flrc: bool = True,
        candidate_bit_rates: tuple[float, ...] = DEFAULT_BIT_RATES,
        min_snr: float = 6.0,
    ) -> ScanReport:
        """从指定离线 IQ 文件读取样本并执行盲扫。"""
        check_cancelled(self.cancel_event)
        if not path.is_file():
            raise FileNotFoundError(f"IQ 文件不存在: {path}")

        sample_bytes = SAMPLE_BYTES.get(iq_format)
        if sample_bytes is None:
            raise ValueError(f"不支持的 IQ 格式: {iq_format}")

        file_size = path.stat().st_size
        if file_size == 0:
            raise ValueError("IQ 文件为空")

        total_avail_samples = file_size // sample_bytes
        requested_samples = int(duration * sample_rate) if duration > 0 else total_avail_samples
        load_samples = min(total_avail_samples, requested_samples)

        bytes_to_read = load_samples * sample_bytes
        self._publish("reading", 0)
        with path.open("rb") as stream:
            raw = stream.read(bytes_to_read)

        check_cancelled(self.cancel_event)
        samples = decode_samples(raw, iq_format)
        return self.scan_iq_samples(
            samples,
            sample_rate=sample_rate,
            center_frequency=center_frequency,
            threshold_db=threshold_db,
            verify_flrc=verify_flrc,
            candidate_bit_rates=candidate_bit_rates,
            min_snr=min_snr,
        )

    def scan_live_sdr(
        self,
        sdr: str,
        start_freq: float = 2_400_000_000.0,
        stop_freq: float = 2_483_500_000.0,
        step_bandwidth: float = 8_000_000.0,
        dwell_seconds: float = 0.05,
        gain: float = 30.0,
        threshold_db: float = 10.0,
        verify_flrc: bool = True,
        candidate_bit_rates: tuple[float, ...] = DEFAULT_BIT_RATES,
        min_snr: float = 6.0,
        sdr_options: dict[str, Any] | None = None,
    ) -> ScanReport:
        """使用 HackRF 或 USRP 步进扫频执行全频段感知。"""
        check_cancelled(self.cancel_event)
        if not all(isfinite(value) for value in (start_freq, stop_freq, step_bandwidth, dwell_seconds, gain)):
            raise ValueError("扫频参数必须为有限数")
        if start_freq >= stop_freq:
            raise ValueError("start_freq 必须小于 stop_freq")
        if dwell_seconds <= 0:
            raise ValueError("dwell_seconds 必须为正数")
        if step_bandwidth <= 0:
            raise ValueError("step_bandwidth 必须为正数")

        opts = sdr_options or {}
        backend = sdr.lower().strip()
        sample_count = max(4096, ceil(dwell_seconds * step_bandwidth))

        # 计算步进跳频中心列表（预留 20% 重叠滤波保护带）
        hop_step = step_bandwidth * 0.8
        # Match the usable passband below so the two outer guard bands do not
        # leave the requested start/end frequencies unscanned.
        first_hop = start_freq + step_bandwidth * 0.42
        last_hop = stop_freq - step_bandwidth * 0.42
        if (last_hop - first_hop) / hop_step > 10_000:
            raise ValueError("扫频频点超过 10000 个，请增大步进带宽或缩小频段")
        if first_hop > last_hop:
            hops = [(start_freq + stop_freq) / 2.0]
        else:
            hops = []
            f = first_hop
            while f < last_hop:
                hops.append(f)
                f += hop_step
            hops.append(last_hop)

        raw_candidates_all: list[ChannelCandidate] = []
        noise_floors: list[float] = []

        def live_report(hop_report: ScanReport | None = None, hop_center: float = 0) -> ScanReport:
            extra = [c for c in hop_report.channels if abs(c.frequency_hz - hop_center) <= step_bandwidth * 0.42] if hop_report else []
            floors = noise_floors + ([hop_report.noise_floor_dbfs] if hop_report else [])
            return ScanReport(
                scan_mode=f"live_sdr_{backend}", start_freq_hz=start_freq, stop_freq_hz=stop_freq,
                noise_floor_dbfs=round(float(np.mean(floors)), 1) if floors else -70.0,
                threshold_db=threshold_db, channels=_merge_adjacent_channels(raw_candidates_all + extra),
                duration_seconds=round(len(hops) * dwell_seconds, 3), sample_rate=step_bandwidth,
                center_frequency_hz=(start_freq + stop_freq) / 2.0,
                device_info={"sdr": backend, "hops": len(hops), "dwell_s": dwell_seconds,
                             "hops_completed": len(noise_floors), "partial": len(noise_floors) < len(hops)},
            )

        def capture_progress(index: int, center: float) -> None:
            self._publish("capturing", 100 * index / len(hops),
                          hops_completed=index, hops_total=len(hops), frequency_mhz=center / 1e6)

        def analyze_hop(samples: np.ndarray, index: int, center: float) -> None:
            def publish(stage, percent, report=None, **details):
                self._publish("hop_completed" if stage == "completed" else stage,
                              100 * (index + percent / 100) / len(hops),
                              live_report(report, center) if report else None,
                              hops_completed=index + (stage == "completed"), hops_total=len(hops),
                              frequency_mhz=center / 1e6, **details)

            rep = self.scan_iq_samples(
                samples, sample_rate=step_bandwidth, center_frequency=center,
                threshold_db=threshold_db, verify_flrc=verify_flrc,
                candidate_bit_rates=candidate_bit_rates, min_snr=min_snr, on_progress=publish,
            )
            noise_floors.append(rep.noise_floor_dbfs)
            raw_candidates_all.extend(c for c in rep.channels if abs(c.frequency_hz - center) <= step_bandwidth * 0.42)

        if backend == "hackrf":
            from ..sdr.hackrf.config import HackRFConfig
            from ..sdr.hackrf.device import HackRFDevice
            from ..sdr.hackrf.library import HackRFLibrary
            from ..sdr.hackrf.streaming import StreamCancelled

            lna = opts.get("lna_gain")
            vga = opts.get("vga_gain")
            if lna is None or vga is None:
                lna = min(40, max(0, int(round(gain / 2) // 8 * 8)))
                vga = min(62, max(0, int(round(gain - lna) // 2 * 2)))

            dll_path = opts.get("dll")
            serial = opts.get("serial")
            device_index = opts.get("device_index")

            with HackRFDevice(HackRFLibrary(dll_path), serial=serial, device_index=device_index) as dev:
                for hop_index, hop_center in enumerate(hops):
                    check_cancelled(self.cancel_event)
                    capture_progress(hop_index, hop_center)
                    cfg = HackRFConfig(
                        frequency_hz=round(hop_center),
                        sample_rate=step_bandwidth,
                        amp=opts.get("amp", False),
                        bias_tee=opts.get("bias_tee", False),
                        lna_gain=lna,
                        vga_gain=vga,
                    )
                    sink = io.BytesIO()
                    try:
                        dev.receive(sink, cfg, sample_count=sample_count, cancel_event=self.cancel_event)
                    except StreamCancelled as err:
                        raise ScanCancelled("扫描已停止") from err
                    check_cancelled(self.cancel_event)
                    raw_bytes = sink.getvalue()
                    samples = decode_samples(raw_bytes, "cs8")

                    analyze_hop(samples, hop_index, hop_center)

        elif backend == "usrp":
            from ..sdr.usrp.config import USRPConfig
            from ..sdr.usrp.device import USRPDevice
            from ..sdr.usrp.library import UHDLibrary

            device_args = opts.get("args") or ""
            dll_path = opts.get("dll")
            master_clock_rate = opts.get("master_clock_rate")
            library = UHDLibrary(dll_path)

            with USRPDevice(library, device_args=device_args, master_clock_rate=master_clock_rate) as dev:
                for hop_index, hop_center in enumerate(hops):
                    check_cancelled(self.cancel_event)
                    capture_progress(hop_index, hop_center)
                    cfg = USRPConfig(
                        frequency_hz=hop_center,
                        sample_rate=step_bandwidth,
                        gain=gain,
                        channel=opts.get("channel", 0),
                        antenna=opts.get("antenna"),
                        bandwidth_hz=opts.get("bandwidth_hz"),
                        clock_source=opts.get("clock_source"),
                        time_source=opts.get("time_source"),
                        master_clock_rate=master_clock_rate,
                        cpu_format="cs8",
                    )
                    sink = io.BytesIO()
                    dev.receive(sink, cfg, sample_count=sample_count, cancel_event=self.cancel_event)
                    check_cancelled(self.cancel_event)
                    raw_bytes = sink.getvalue()
                    samples = decode_samples(raw_bytes, "cs8")

                    analyze_hop(samples, hop_index, hop_center)
        else:
            raise ValueError(f"不支持的 SDR 硬件类型: {sdr}")

        # 去重合并邻近 hop 交叉检测到的同频载波 (100kHz 窗口内)
        check_cancelled(self.cancel_event)
        report = live_report()
        self._publish("completed", 100, report, hops_completed=len(hops), hops_total=len(hops))
        return report


def _merge_adjacent_channels(channels: list[ChannelCandidate], tol_hz: float = 120_000.0) -> list[ChannelCandidate]:
    """去重并合并不同 hop 覆盖重叠频段内检测到的重复信道。"""
    if not channels:
        return []

    sorted_chs = sorted(channels, key=lambda c: c.frequency_hz)
    merged: list[ChannelCandidate] = []

    for c in sorted_chs:
        if not merged:
            merged.append(c)
            continue

        prev = merged[-1]
        if abs(c.frequency_hz - prev.frequency_hz) <= tol_hz:
            # 合并：保留更高 SNR，并汇总突发数与同步字
            better = c if c.snr_db > prev.snr_db else prev
            combined_bursts = prev.burst_count + c.burst_count
            combined_syncs = list(dict.fromkeys(prev.sync_words + c.sync_words))
            is_flrc = prev.is_flrc or c.is_flrc
            confidence = max(prev.flrc_confidence, c.flrc_confidence)
            proto_hint = better.protocol_hint if better.is_flrc else (
                prev.protocol_hint if prev.is_flrc else c.protocol_hint
            )

            merged[-1] = ChannelCandidate(
                frequency_hz=better.frequency_hz,
                bandwidth_hz=max(prev.bandwidth_hz, c.bandwidth_hz),
                peak_power_dbfs=better.peak_power_dbfs,
                snr_db=better.snr_db,
                noise_floor_dbfs=better.noise_floor_dbfs,
                burst_count=combined_bursts,
                duty_cycle=round((prev.duty_cycle + c.duty_cycle) / 2.0, 3),
                is_flrc=is_flrc,
                flrc_confidence=confidence,
                sync_words=combined_syncs,
                bit_rate=better.bit_rate or prev.bit_rate or c.bit_rate,
                protocol_hint=proto_hint,
                details=better.details,
            )
        else:
            merged.append(c)

    return merged
