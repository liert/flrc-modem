"""命令行实时 FHSS + TDD 同步分析入口：flrc sync。

无需 IQ 文件落盘，实时接入 USRP N310 或 HackRF，在线估计时钟漂移、
跟踪跳频时隙与 TDD 窗口，输出实时状态并生成时间轴可视化。
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time
import traceback

from .io.output import build_record, format_frame, write_jsonl
from .io.ui_events import get_emitter
from .parsing.api import load_parser
from .sync.live import resolve_rf_settings
from .sync import (
    LiveSyncSession,
    SyncSnapshot,
    TDDMode,
    TimelineVisualizer,
    calculate_flrc_packet_duration,
)


@contextmanager
def _report_live_metrics(session, emitter):
    """定期输出吞吐和跳窗信息，不依赖是否解出 CRC 帧。"""
    stopped = threading.Event()
    reported_drops = 0
    reported_config = False
    reported_error = None
    last_health = time.monotonic()

    def log(event, payload):
        print(json.dumps({"event": event, "entry_point": "flrc.sync",
                          "timestamp": datetime.now(timezone.utc).isoformat(),
                          "session_id": session.session_id, **payload}, ensure_ascii=False),
              file=sys.stderr, flush=True)

    log("sync.started", {"sdr": session.sdr_type, "requested_sample_rate": session.requested_sample_rate,
                         "frequency_hz": session.frequency_hz, "gain": session.gain,
                         "requested_master_clock_rate": session.master_clock_rate,
                         "channel": session.channel, "antenna": session.antenna,
                         "timed_hopping": session.timed_hopping, "sync_word": session.sync_word,
                         "queue_policy": session.window_queue.overflow_policy,
                         "window_queue_depth": session._queue_depth})

    def publish(final=False):
        nonlocal reported_drops, reported_config, reported_error, last_health
        metrics = session.realtime_metrics()
        if session._configured.is_set() and not reported_config:
            reported_config = True
            emitter.emit("radio.configured", stage_id="live_sync", payload=metrics)
            log("sync.configured", metrics)
        if metrics["windows_skipped"] > reported_drops:
            skipped_delta = metrics["windows_skipped"] - reported_drops
            reported_drops = metrics["windows_skipped"]
            payload = {"action": "skip_stale_and_reacquire", "skipped_windows_delta": skipped_delta, **metrics}
            emitter.emit("radio.backlog", stage_id="live_sync", payload=payload)
            log("sync.backlog", payload)
        if metrics["error"] and metrics["error"] != reported_error:
            reported_error = metrics["error"]
            log("sync.failed", metrics)
        now = time.monotonic()
        if final or now - last_health >= 5.0:
            last_health = now
            log("sync.stopped" if final else "sync.health", {
                **metrics, "status": session.stats.status,
                "elapsed_seconds": now - session._start_time if session._start_time else 0.0,
            })
        emitter.metric("live_sync", metrics)

    def report():
        while not stopped.wait(0.5):
            try:
                publish()
            except Exception as error:
                log("sync.monitor_failed", {"error": f"{type(error).__name__}: {error}", "error_traceback": traceback.format_exc()})
                return

    thread = threading.Thread(target=report, name="flrc-sync-metrics", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1.0)
        if not thread.is_alive():
            try:
                publish(final=True)
            except Exception as error:
                log("sync.monitor_failed", {"error": f"{type(error).__name__}: {error}", "error_traceback": traceback.format_exc()})
        else:
            log("sync.monitor_timeout", {"error": "状态输出线程未及时停止，输出通道可能阻塞"})


def make_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flrc sync",
        description="实时 FHSS 跳频与 TDD 时分双工硬件时间戳同步引擎（无需 IQ 文件落盘）",
    )
    parser.add_argument("--sdr", choices=["usrp", "hackrf"], default="usrp",
                        help="SDR 设备类型 (默认 usrp)")
    parser.add_argument("--args", default="addr=192.168.10.2",
                        help="USRP 设备定位参数 (默认 addr=192.168.10.2)")
    from .sdr.hackrf.selection_cli import add_device_arguments
    add_device_arguments(parser)
    parser.add_argument("--master-clock-rate", "--mcr", type=float, default=None,
                        help="USRP 主时钟频率 (MCR)，N310 支持: 125e6 (默认), 122.88e6, 153.6e6")
    parser.add_argument("--frequency", type=int,
                        help="射频初始监听频率；留空从射频配置选取，未配置时默认 2471660000 Hz")
    parser.add_argument("--sample-rate", type=float, default=None,
                        help="采样率，单位 Sps (默认: USRP 为 2500000 即 2.5MSps，HackRF 为 2000000 即 2MSps)")
    parser.add_argument("--gain", type=float, default=30.0,
                        help="射频增益，单位 dB (默认 30.0)")
    parser.add_argument("--channel", type=int, default=0,
                        help="USRP 物理通道号 (默认 0)")
    parser.add_argument("--antenna", default="RX2", help="USRP 接收天线端口 (默认 RX2)")
    parser.add_argument("--sync", default=None,
                        help="目标物理差分同步字 (十六进制；可选，未指定时从 --rf-config 读取或空口自适应捕获)")
    from .phy.profile import parse_bt
    parser.add_argument("--bit-rate", type=float, default=325000, help="接收 FLRC 空口码率 (bps)")
    parser.add_argument("--cr", dest="coding_rate", choices=["1/2", "3/4", "1"], default="3/4", help="接收纠错编码率")
    parser.add_argument("--bt", type=parse_bt, default=1.0, help="接收高斯成形 BT：0、0.5 或 1")
    parser.add_argument("--protocol", default="dk32s",
                        help="业务层协议解析插件 (默认 dk32s)")
    parser.add_argument("--rf-config", type=Path,
                        help="包含 hopping_table 的射频配置文件")
    parser.add_argument("--tdd-mode", choices=["reception_triggered", "fixed_periodic"],
                        default="reception_triggered", help="TDD 候选模型 (默认 reception_triggered)")
    parser.add_argument("--timed-hopping", action="store_true",
                        help="启用 USRP N310 UHD Timed Commands 硬件定时跳频调度")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="实时同步持续时间 (秒；0 为持续运行至 Ctrl+C)")
    parser.add_argument("--output", type=Path,
                        help="保存解调帧及同步元数据的 JSONL 文件路径")
    parser.add_argument("--output-html", type=Path, default=Path("outputs/live_sync_timeline.html"),
                        help="输出交互式 HTML 时间轴路径 (默认 outputs/live_sync_timeline.html)")
    parser.add_argument("--jsonl", action="store_true",
                        help="将每帧数据与同步快照直接输出到标准输出 (供管道处理)")
    parser.add_argument("--quiet", action="store_true",
                        help="静默模式，减少控制台输出")
    parser.add_argument("--ui-events", choices=("ndjson",), default=None,
                        help="启用机器事件流输出 (NDJSON)")
    return parser


def main(argv=None) -> int:
    parser = make_argument_parser()
    args = parser.parse_args(argv)
    ui_mode = getattr(args, "ui_events", None) == "ndjson"
    emitter = get_emitter(enabled=ui_mode)
    emitter.emit("task.state", payload={"state": "RUNNING", "command": "sync", "sdr": args.sdr})

    # 加载跳频表与同步字
    try:
        rf = resolve_rf_settings(args.rf_config, args.sync, args.frequency)
    except ValueError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    hopping_table, sync_word, args.frequency = rf['hopping_table'], rf['sync'], rf['frequency']
    for warning in rf['warnings']:
        print(f"[!] {warning}", file=sys.stderr)

    tdd_mode = TDDMode.RECEPTION_TRIGGERED if args.tdd_mode == "reception_triggered" else TDDMode.FIXED_PERIODIC
    frame_parser = load_parser(args.protocol)

    if args.sdr == "usrp":
        from .sdr.usrp.config import DEFAULT_N310_MCR, check_mcr_sample_rate_compatibility
        mcr = args.master_clock_rate or DEFAULT_N310_MCR
        effective_sample_rate = args.sample_rate
        if effective_sample_rate is None:
            if mcr == 122_880_000.0:
                effective_sample_rate = 1_920_000.0
            elif mcr == 153_600_000.0:
                effective_sample_rate = 2_400_000.0
            else:
                effective_sample_rate = 2_500_000.0
        is_int, factor, coerced, msg = check_mcr_sample_rate_compatibility(mcr, effective_sample_rate)
        if not is_int:
            print(f"[!] 提示: {msg}", file=sys.stderr)
            print(f"[*] 同步引擎将自适应对齐硬件实际速率 ({coerced:g} Sps)，防止采样漂移丢包。", file=sys.stderr)
    else:
        effective_sample_rate = args.sample_rate or 2_000_000.0

    emitter.emit("radio.config", payload={
        "sdr": args.sdr, "frequency": args.frequency, "sample_rate": effective_sample_rate,
        "master_clock_rate": args.master_clock_rate, "gain": args.gain, "channel": args.channel,
        "antenna": args.antenna, "sync": sync_word, "bit_rate": args.bit_rate,
        "coding_rate": args.coding_rate, "bt": args.bt, "timed_hopping": args.timed_hopping,
    })
    session = LiveSyncSession(
        sdr_type=args.sdr,
        usrp_args=args.args,
        serial=args.serial, device_index=args.device_index,
        frequency_hz=args.frequency,
        sample_rate=effective_sample_rate,
        master_clock_rate=args.master_clock_rate,
        gain=args.gain,
        channel=args.channel,
        antenna=args.antenna,
        sync_word=sync_word,
        hopping_table=hopping_table,
        tdd_mode=tdd_mode,
        timed_hopping=args.timed_hopping,
        duration=args.duration,
        protocol=args.protocol,
        bit_rate=args.bit_rate, coding_rate=args.coding_rate, bt=args.bt,
    )

    recorded_events = []
    t_first = None
    t_last = None

    with ExitStack() as stack:
        out_file = None
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            out_file = stack.enter_context(args.output.open("w", encoding="utf-8", newline="\n"))

        try:
            with emitter.stage("live_sync", {"sdr": args.sdr, "frequency": args.frequency}):
                with _report_live_metrics(session, emitter), session:
                    for frame, snap in session.events():
                        record = build_record(frame, frame_parser)
                        record["sync"] = snap.to_dict()

                        if t_first is None:
                            t_first = frame.time_s
                        t_last = frame.time_s
                        # HTML 只显示开头 100ms；长期运行不在内存中保留全部帧。
                        if frame.time_s <= t_first + 0.100:
                            recorded_events.append((frame.time_s, frame, snap))

                        if out_file:
                            write_jsonl(out_file, record)
                            out_file.flush()

                        if not ui_mode and not args.quiet:
                            if args.jsonl:
                                write_jsonl(sys.stdout, record)
                                sys.stdout.flush()
                            else:
                                app = record.get("application", {})
                                direction = app.get("direction", "unknown")
                                seq = app.get("sequence", "-")
                                state_badge = snap.state.value.upper()
                                line = (
                                    f"[{state_badge:8s} {snap.confidence*100:4.1f}%] "
                                    f"t={frame.time_s:.6f}s | "
                                    f"Slot {snap.slot_index:2d} ({snap.frequency_hz/1e6:.2f}MHz) | "
                                    f"TDD: {snap.tdd_phase.value:16s} | "
                                    f"Drift: {snap.clock_drift_ppm:+6.2f}ppm | "
                                    f"{direction} (seq={seq})"
                                )
                                print(line, flush=True)

        except KeyboardInterrupt:
            emitter.emit("task.state", payload={"state": "CANCELLED"})

    # 时间轴生成
    if recorded_events and session.state_machine.fhss.anchors and t_first is not None and t_last is not None:
        t_span = min(0.100, max(0.050, t_last - t_first))
        viz = TimelineVisualizer(start_time_s=t_first, end_time_s=t_first + t_span)

        for t, frame, snap in recorded_events:
            if not (t_first <= t <= t_first + t_span):
                continue
            app = build_record(frame, frame_parser).get("application", {})
            d = app.get("direction")
            dur = calculate_flrc_packet_duration(len(frame.packet.payload), bitrate=args.bit_rate, coding_rate=args.coding_rate)
            if d == "ground_to_air":
                viz.add_event(f"Gnd#{app.get('sequence',0)}", "ground_tx", t, t + dur)
                viz.add_event("Preamble", "actual_arrival", t, t)
            elif d == "air_to_ground":
                viz.add_event(f"Air#{app.get('sequence',0)}", "air_rx", t, t + dur)
                viz.add_event("Preamble", "actual_arrival", t, t)

        p0 = session.state_machine.fhss.predict(t_first)
        viz.add_event(f"Slot {p0.slot_index}", "fhss_slot", p0.slot_start_hw_time, p0.slot_end_hw_time,
                      details=f"{p0.frequency_hz/1e6:.1f}MHz")

        if args.output_html:
            args.output_html.parent.mkdir(parents=True, exist_ok=True)
            args.output_html.write_text(viz.render_html("实时 FHSS + TDD 物理同步时间轴"), encoding="utf-8")
            emitter.artifact("timeline", args.output_html, "sync_timeline_html", {})

    if args.output and args.output.exists():
        emitter.artifact("live_sync", args.output, "frames_jsonl", {
            "decoded_frames": session.stats.decoded_frames,
        })

    emitter.task_completed(
        session.stats.error is None,
        decoded_frames=session.stats.decoded_frames,
        overflow_count=session.stats.overflow_count,
        windows_skipped=session.stats.windows_skipped,
        skipped_seconds=session.stats.skipped_samples / session.sample_rate,
    )
    return 2 if session.stats.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
