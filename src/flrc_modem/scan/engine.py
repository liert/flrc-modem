"""盲扫引擎：支持离线 IQ 样本/文件分析与 HackRF / USRP 步进扫频感知。"""
from __future__ import annotations

import ctypes as C
from dataclasses import replace
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
        if not np.all(np.isfinite(samples)):
            raise ValueError("IQ 样本不能含 NaN/Inf")
        if not isfinite(threshold_db) or threshold_db < 0 or not isfinite(min_snr) or min_snr < 0:
            raise ValueError("检测门限和最小 SNR 必须为有限非负数")

        publish = on_progress or self._publish
        publish("analyzing", 5)
        freqs, max_psd, avg_psd, time_slices, rbw = compute_psd_waterfall(
            samples, n_fft=n_fft, sample_rate=sample_rate, center_freq=center_frequency,
            check_cancelled=lambda: check_cancelled(self.cancel_event),
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
            samples=samples, check_cancelled=lambda: check_cancelled(self.cancel_event),
        )

        raw_candidates = [raw for raw in raw_candidates if raw["snr_db"] >= min_snr]
        pending = [ChannelCandidate(
            frequency_hz=raw["frequency_hz"], bandwidth_hz=raw["bandwidth_hz"],
            peak_power_dbfs=raw["peak_power_dbfs"], snr_db=raw["snr_db"],
            noise_floor_dbfs=raw.get("noise_floor_dbfs", noise_floor), burst_count=raw["burst_count"], duty_cycle=raw["duty_cycle"],
            active_slices=raw.get("active_slices", 0), total_slices=raw.get("total_slices", 0),
            details={key: raw[key] for key in ("frequency_resolution_hz", "frequency_method") if key in raw},
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
                    frequency_hz=ver_res.get("frequency_hz", target_f),
                    bandwidth_hz=bw,
                    peak_power_dbfs=raw["peak_power_dbfs"],
                    snr_db=raw["snr_db"],
                    noise_floor_dbfs=raw.get("noise_floor_dbfs", noise_floor),
                    burst_count=ver_res["burst_count"] if ver_res.get("burst_count", 0) else raw["burst_count"],
                    active_slices=raw.get("active_slices", 0), total_slices=raw.get("total_slices", 0),
                    verification_status=ver_res.get("verification_status", "crc" if ver_res["is_flrc"] else "energy"),
                    crc_valid_frames=ver_res.get("crc_valid_frames", 0),
                    duty_cycle=raw["duty_cycle"],
                    is_flrc=ver_res["is_flrc"],
                    flrc_confidence=ver_res["flrc_confidence"],
                    sync_words=ver_res["sync_words"],
                    bit_rate=ver_res["bit_rate"],
                    protocol_hint=ver_res["protocol_hint"],
                    details={"frequency_resolution_hz": rbw, "frequency_method": "burst_centroid", **ver_res},
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
        sample_rate: float | None = None,
        passes: int = 2,
        refine_dwell: float = .25,
        max_refine_candidates: int = 32,
        settle_seconds: float = .01,
        discard_seconds: float = .002,
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
        if not isfinite(threshold_db) or threshold_db < 0 or not isfinite(min_snr) or min_snr < 0:
            raise ValueError("检测门限和最小 SNR 必须为有限非负数")

        if type(passes) is not int or not 1 <= passes <= 20:
            raise ValueError("扫描轮数必须为 1..20 的整数")
        if type(max_refine_candidates) is not int or not 0 <= max_refine_candidates <= 256:
            raise ValueError("精扫候选上限必须为 0..256 的整数")
        if any(not isfinite(value) or value < 0 for value in (refine_dwell, settle_seconds, discard_seconds)):
            raise ValueError("精扫、调谐稳定和丢弃时间必须为有限非负数")

        opts = sdr_options or {}
        backend = sdr.lower().strip()
        requested_rate = step_bandwidth if sample_rate is None else sample_rate
        if not isfinite(requested_rate) or requested_rate <= 0:
            raise ValueError("sample_rate 必须为正有限数")
        actual_rate = requested_rate
        effective_bandwidth = min(step_bandwidth, actual_rate)
        hops = _plan_hops(start_freq, stop_freq, effective_bandwidth)
        device_details: dict[str, Any] = {
            "requested_sample_rate": requested_rate,
            "requested_master_clock_rate": opts.get("master_clock_rate"),
            "overflow_count": 0, "short_capture_hops": 0,
            "passes": passes, "refine_dwell_s": refine_dwell, "refined_candidates": 0,
            "settle_seconds": settle_seconds if backend == "usrp" else 0,
            "discard_seconds": discard_seconds if backend == "usrp" else 0,
        }

        raw_candidates_all: list[ChannelCandidate] = []
        noise_floors: list[float] = []
        captured_seconds = 0.0
        refining = False
        coarse_percent = 90 if verify_flrc and refine_dwell > 0 and max_refine_candidates else 100

        def scan_plan():
            # 交替方向加循环平移，减少固定扫描顺序与跳频周期反复对齐。
            for pass_index in range(passes):
                order = hops if pass_index % 2 == 0 else list(reversed(hops))
                shift = (pass_index // 2 * max(1, len(hops) // 3)) % len(hops)
                yield from order[shift:] + order[:shift]

        def refine_targets():
            if not verify_flrc or refine_dwell <= 0 or max_refine_candidates == 0:
                return []
            candidates = _merge_adjacent_channels(raw_candidates_all)
            pending = [candidate for candidate in candidates if not candidate.is_flrc]
            pending.sort(key=lambda item: (item.verification_status == "preamble", item.observation_count, item.snr_db), reverse=True)
            device_details["refine_candidates_skipped"] = max(0, len(pending) - max_refine_candidates)
            return pending[:max_refine_candidates]

        def record_refinement(candidate, samples, center):
            nonlocal captured_seconds
            check_cancelled(self.cancel_event)
            result = verify_flrc_candidate(samples, sample_rate=actual_rate, center_frequency=center,
                                           target_frequency=candidate.frequency_hz, bandwidth_hz=candidate.bandwidth_hz,
                                           candidate_bit_rates=candidate_bit_rates, cancel_event=self.cancel_event)
            captured_seconds += len(samples) / actual_rate
            device_details["refined_candidates"] += 1
            if result.get("burst_count", 0):
                # 精扫与粗扫有明确关联，不再依赖粗扫频率误差恰好落入合并门限。
                low = candidate.details.get("frequency_min_hz", candidate.frequency_hz)
                high = candidate.details.get("frequency_max_hz", candidate.frequency_hz)
                raw_candidates_all[:] = [item for item in raw_candidates_all if not low - 1 <= item.frequency_hz <= high + 1]
                details = {key: value for key, value in candidate.details.items()
                           if key not in ("frequency_min_hz", "frequency_max_hz")}
                raw_candidates_all.append(replace(
                    candidate, frequency_hz=result.get("frequency_hz", candidate.frequency_hz),
                    burst_count=candidate.burst_count + result["burst_count"], crc_valid_frames=result["crc_valid_frames"],
                    verification_status=result["verification_status"], is_flrc=result["is_flrc"],
                    sync_words=list(dict.fromkeys(result["sync_words"] if result["is_flrc"]
                                                  else candidate.sync_words + result["sync_words"])), bit_rate=result["bit_rate"],
                    flrc_confidence=result["flrc_confidence"], protocol_hint=result["protocol_hint"],
                    observation_count=candidate.observation_count + 1,
                    details={**details, **result, "refined": True, "coarse_frequency_hz": candidate.frequency_hz},
                ))

        def in_passband(candidate: ChannelCandidate, center: float) -> bool:
            return (start_freq <= candidate.frequency_hz <= stop_freq
                    and abs(candidate.frequency_hz - center) <= effective_bandwidth * 0.42)

        def live_report(hop_report: ScanReport | None = None, hop_center: float = 0) -> ScanReport:
            extra = [c for c in hop_report.channels if in_passband(c, hop_center)] if hop_report else []
            floors = noise_floors + ([hop_report.noise_floor_dbfs] if hop_report else [])
            return ScanReport(
                scan_mode=f"live_sdr_{backend}", start_freq_hz=start_freq, stop_freq_hz=stop_freq,
                noise_floor_dbfs=round(float(np.mean(floors)), 1) if floors else -70.0,
                threshold_db=threshold_db, channels=_merge_adjacent_channels(raw_candidates_all + extra),
                duration_seconds=round(captured_seconds, 3), sample_rate=actual_rate,
                center_frequency_hz=(start_freq + stop_freq) / 2.0,
                device_info={**device_details, "sdr": backend, "hops": len(hops) * passes, "hops_per_pass": len(hops), "dwell_s": dwell_seconds,
                             "actual_sample_rate": actual_rate, "effective_bandwidth_hz": effective_bandwidth,
                             "hop_step_hz": effective_bandwidth * 0.8,
                             "hops_completed": len(noise_floors), "partial": refining or len(noise_floors) < len(hops) * passes},
            )

        def capture_progress(index: int, center: float) -> None:
            self._publish("capturing", coarse_percent * index / (len(hops) * passes), live_report(),
                          hops_completed=index, hops_total=len(hops) * passes, frequency_mhz=center / 1e6,
                          pass_index=index // len(hops) + 1, passes=passes)

        def analyze_hop(samples: np.ndarray, index: int, center: float) -> None:
            nonlocal captured_seconds
            captured_seconds += len(samples) / actual_rate
            def publish(stage, percent, report=None, **details):
                self._publish("hop_completed" if stage == "completed" else stage,
                              coarse_percent * (index + percent / 100) / (len(hops) * passes),
                              live_report(report, center) if report else None,
                              hops_completed=index + (stage == "completed"), hops_total=len(hops) * passes,
                              frequency_mhz=center / 1e6, **details)

            rep = self.scan_iq_samples(
                samples, sample_rate=actual_rate, center_frequency=center,
                threshold_db=threshold_db, verify_flrc=verify_flrc,
                candidate_bit_rates=candidate_bit_rates, min_snr=min_snr, on_progress=publish,
            )
            noise_floors.append(rep.noise_floor_dbfs)
            raw_candidates_all.extend(c for c in rep.channels if in_passband(c, center))

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
                def capture(center, duration):
                    check_cancelled(self.cancel_event)
                    cfg = HackRFConfig(round(center), sample_rate=requested_rate,
                                       amp=opts.get("amp", False), bias_tee=opts.get("bias_tee", False),
                                       lna_gain=lna, vga_gain=vga)
                    sink = io.BytesIO()
                    try:
                        dev.receive(sink, cfg, sample_count=max(4096, ceil(duration * actual_rate)), cancel_event=self.cancel_event)
                    except StreamCancelled as error:
                        raise ScanCancelled("扫描已停止") from error
                    check_cancelled(self.cancel_event)
                    return decode_samples(sink.getvalue(), "cs8")

                for hop_index, hop_center in enumerate(scan_plan()):
                    capture_progress(hop_index, hop_center)
                    analyze_hop(capture(hop_center, dwell_seconds), hop_index, hop_center)

                targets = refine_targets()
                refining = bool(targets)
                for index, candidate in enumerate(targets):
                    check_cancelled(self.cancel_event)
                    self._publish("refining", 90 + 10 * index / len(targets), live_report(),
                                  candidates_completed=index, candidates_total=len(targets))
                    record_refinement(candidate, capture(candidate.frequency_hz, refine_dwell), candidate.frequency_hz)
                refining = False

        elif backend == "usrp":
            from ..sdr.usrp.config import USRPConfig
            from ..sdr.usrp.device import USRPDevice
            from ..sdr.usrp.library import UHDLibrary

            device_args = opts.get("args") or ""
            dll_path = opts.get("dll")
            master_clock_rate = opts.get("master_clock_rate")
            library = UHDLibrary(dll_path)

            with USRPDevice(library, device_args=device_args, master_clock_rate=master_clock_rate) as dev:
                base_config = USRPConfig(
                    frequency_hz=(start_freq + stop_freq) / 2,
                    sample_rate=requested_rate, gain=gain,
                    channel=opts.get("channel", 0), antenna=opts.get("antenna"),
                    bandwidth_hz=opts.get("bandwidth_hz"),
                    clock_source=opts.get("clock_source"), time_source=opts.get("time_source"),
                    master_clock_rate=master_clock_rate, cpu_format="fc32",
                )
                # UHD may coerce the requested rate. Plan coverage only after
                # reading it back; otherwise narrower actual bands leave holes.
                actual_rate = dev.configure_rx(base_config)
                if not isfinite(actual_rate) or actual_rate <= 0:
                    raise ValueError("UHD 返回了无效的实际采样率")
                effective_bandwidth = min(step_bandwidth, actual_rate, opts.get("bandwidth_hz") or actual_rate)
                supported_rates = [rate for rate in candidate_bit_rates if 0 < 4 * rate <= actual_rate]
                # Reserve the widest supported demodulator's filter at each edge;
                # energy coverage alone would still leave gaps in FLRC verification.
                if verify_flrc and supported_rates:
                    cutoff = max(max(150_000.0, rate * 350.0 / 325.0) for rate in supported_rates)
                    effective_bandwidth = min(effective_bandwidth, (actual_rate / 2 - cutoff) / 0.42)
                device_details["unsupported_bit_rates"] = (
                    [rate for rate in candidate_bit_rates if rate not in supported_rates] if verify_flrc else []
                )
                hops = _plan_hops(start_freq, stop_freq, effective_bandwidth)
                device_details.update(actual_master_clock_rate=dev.get_master_clock_rate(), iq_format="cf32")
                def capture(center, duration):
                    check_cancelled(self.cancel_event)
                    cfg = replace(base_config, frequency_hz=center)
                    captured_center = center

                    def configured(rate):
                        nonlocal captured_center
                        if not isfinite(rate) or abs(rate - actual_rate) > 1:
                            raise ValueError("扫描中实际采样率发生变化，请重新扫描以保证频段覆盖")
                        frequency = C.c_double()
                        dev.api.check(dev.api.dll.uhd_usrp_get_rx_freq(
                            dev.handle, C.c_size_t(cfg.channel), C.byref(frequency)), "get_rx_freq")
                        captured_center = frequency.value
                        if not isfinite(captured_center) or abs(captured_center - center) > effective_bandwidth * .01:
                            raise ValueError("USRP 实际中心频率偏离扫描计划，无法保证频段覆盖")

                    sink = io.BytesIO()
                    stats = dev.receive(sink, cfg, sample_count=max(4096, ceil(duration * actual_rate)),
                                        duration_s=max(duration, 4096 / actual_rate),
                                        cancel_event=self.cancel_event, rate_callback=configured,
                                        settle_seconds=settle_seconds, discard_seconds=discard_seconds)
                    check_cancelled(self.cancel_event)
                    samples = decode_samples(sink.getvalue(), "cf32")
                    device_details["overflow_count"] += stats.overflow_count
                    if len(samples) < round(duration * actual_rate):
                        device_details["short_capture_hops"] += 1
                    return samples, captured_center

                for hop_index, hop_center in enumerate(scan_plan()):
                    capture_progress(hop_index, hop_center)
                    samples, captured_center = capture(hop_center, dwell_seconds)
                    analyze_hop(samples, hop_index, captured_center)
                targets = refine_targets()
                refining = bool(targets)
                for index, candidate in enumerate(targets):
                    check_cancelled(self.cancel_event)
                    self._publish("refining", 90 + 10 * index / len(targets), live_report(),
                                  candidates_completed=index, candidates_total=len(targets))
                    samples, captured_center = capture(candidate.frequency_hz, refine_dwell)
                    record_refinement(candidate, samples, captured_center)
                refining = False

        else:
            raise ValueError(f"不支持的 SDR 硬件类型: {sdr}")

        # 按分辨率、带宽及 CRC 证据合并跨轮次观测。
        check_cancelled(self.cancel_event)
        report = live_report()
        self._publish("completed", 100, report, hops_completed=len(hops) * passes, hops_total=len(hops) * passes)
        return report


def _plan_hops(start_freq: float, stop_freq: float, bandwidth: float) -> list[float]:
    """Cover both endpoints using the actual usable passband, with overlap."""
    hop_step = bandwidth * 0.8
    first = start_freq + bandwidth * 0.42
    last = stop_freq - bandwidth * 0.42
    if first >= last:
        return [(start_freq + stop_freq) / 2]
    count = ceil((last - first) / hop_step)
    if count + 1 > 10_000:
        raise ValueError("扫频频点超过 10000 个，请增大带宽或缩小频段")
    return [first + index * hop_step for index in range(count)] + [last]


def _merge_adjacent_channels(channels: list[ChannelCandidate], tol_hz: float = 120_000.0) -> list[ChannelCandidate]:
    """以分辨率和信号带宽约束跨采集去重；优先保留 CRC 证据和频率标定。"""
    merged = []
    rank = {"energy": 0, "preamble": 1, "crc": 2}
    for candidate in sorted(channels, key=lambda item: item.frequency_hz):
        if not merged:
            merged.append(candidate)
            continue
        previous = merged[-1]
        resolution = max(previous.details.get("frequency_resolution_hz", 5000),
                         candidate.details.get("frequency_resolution_hz", 5000))
        tolerance = min(tol_hz, max(3 * resolution, .15 * min(previous.bandwidth_hz, candidate.bandwidth_hz)))
        low = min(previous.details.get("frequency_min_hz", previous.frequency_hz), candidate.frequency_hz)
        high = max(previous.details.get("frequency_max_hz", previous.frequency_hz), candidate.frequency_hz)
        if high - low > tolerance:
            merged.append(candidate)
            continue
        better = max((previous, candidate), key=lambda item: (item.is_flrc, rank.get(item.verification_status, 0), item.snr_db))
        active = previous.active_slices + candidate.active_slices
        total = previous.total_slices + candidate.total_slices
        confirmed = [item for item in (previous, candidate) if item.is_flrc]
        selected = confirmed or [previous, candidate]
        weights = [max(1, item.crc_valid_frames if item.is_flrc else item.observation_count) for item in selected]
        frequency = float(np.average([item.frequency_hz for item in selected], weights=weights))
        details = {**better.details, "frequency_min_hz": low, "frequency_max_hz": high}
        crc_frames = previous.crc_valid_frames + candidate.crc_valid_frames
        details.update(crc_valid_frames=crc_frames)
        merged[-1] = replace(
            better, frequency_hz=frequency, bandwidth_hz=max(previous.bandwidth_hz, candidate.bandwidth_hz),
            burst_count=previous.burst_count + candidate.burst_count,
            sync_words=list(dict.fromkeys(word for item in selected for word in item.sync_words)),
            crc_valid_frames=crc_frames, active_slices=active, total_slices=total,
            observation_count=previous.observation_count + candidate.observation_count,
            duty_cycle=round(active / total, 3) if total else (previous.duty_cycle + candidate.duty_cycle) / 2,
            flrc_confidence=max(previous.flrc_confidence, candidate.flrc_confidence), details=details,
        )
    return merged
