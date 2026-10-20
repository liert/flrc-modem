"""命令行实时 FHSS + TDD 同步分析入口：flrc sync。

无需 IQ 文件落盘，实时接入 USRP N310 或 HackRF，在线估计时钟漂移、
跟踪跳频时隙与 TDD 窗口，输出实时状态并生成时间轴可视化。
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import time

from .io.output import build_record, format_frame, write_jsonl
from .parsing.api import load_parser
from .sync import (
    DEFAULT_DK32S_HOPPING_TABLE,
    LiveSyncSession,
    SyncSnapshot,
    TDDMode,
    TimelineVisualizer,
    calculate_flrc_packet_duration,
)


def make_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flrc sync",
        description="实时 FHSS 跳频与 TDD 时分双工硬件时间戳同步引擎（无需 IQ 文件落盘）",
    )
    parser.add_argument("--sdr", choices=["usrp", "hackrf"], default="usrp",
                        help="SDR 设备类型 (默认 usrp)")
    parser.add_argument("--args", default="addr=192.168.10.2",
                        help="USRP 设备定位参数 (默认 addr=192.168.10.2)")
    parser.add_argument("--serial", help="HackRF 设备序列号")
    parser.add_argument("--frequency", type=int, default=2471660000,
                        help="射频初始监听频率 (默认 2471660000 Hz, 即 DK32S 常用 Slot 17)")
    parser.add_argument("--sample-rate", type=float, default=2000000.0,
                        help="采样率，单位 Sps (默认 2000000)")
    parser.add_argument("--gain", type=float, default=30.0,
                        help="射频增益，单位 dB (默认 30.0)")
    parser.add_argument("--channel", type=int, default=0,
                        help="USRP 物理通道号 (默认 0)")
    parser.add_argument("--sync", default="ecee6698",
                        help="目标物理差分同步字 (默认 ecee6698)")
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
    return parser


def main(argv=None) -> int:
    parser = make_argument_parser()
    args = parser.parse_args(argv)

    # 加载跳频表
    if args.rf_config and args.rf_config.is_file():
        data = json.loads(args.rf_config.read_text(encoding="utf-8-sig"))
        hopping_table = [int(item["frequency_hz"]) for item in data["hopping_table"]]
    else:
        hopping_table = list(DEFAULT_DK32S_HOPPING_TABLE)

    tdd_mode = TDDMode.RECEPTION_TRIGGERED if args.tdd_mode == "reception_triggered" else TDDMode.FIXED_PERIODIC
    frame_parser = load_parser(args.protocol)

    print(f"[*] 启动实时 FHSS + TDD 同步引擎 (SDR: {args.sdr.upper()})...", file=sys.stderr)
    if args.sdr == "usrp":
        print(f"[*] USRP 设备参数: {args.args}, 频率: {args.frequency / 1e6:.2f} MHz, 通道: {args.channel}", file=sys.stderr)
    else:
        print(f"[*] HackRF 监听频率: {args.frequency / 1e6:.2f} MHz", file=sys.stderr)

    session = LiveSyncSession(
        sdr_type=args.sdr,
        usrp_args=args.args,
        frequency_hz=args.frequency,
        sample_rate=args.sample_rate,
        gain=args.gain,
        channel=args.channel,
        sync_word=args.sync,
        hopping_table=hopping_table,
        tdd_mode=tdd_mode,
        timed_hopping=args.timed_hopping,
        duration=args.duration,
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
            with session:
                print(f"[*] 监听已就绪，正在实时捕获物理帧与计算时钟同步 (按 Ctrl+C 停止)...", file=sys.stderr)
                for frame, snap in session.events():
                    record = build_record(frame, frame_parser)
                    record["sync"] = snap.to_dict()

                    if t_first is None:
                        t_first = frame.time_s
                    t_last = frame.time_s
                    recorded_events.append((frame.time_s, frame, snap))

                    if out_file:
                        write_jsonl(out_file, record)
                        out_file.flush()

                    if not args.quiet:
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
            print("\n[*] 接收到用户中断信号，正在优雅停止会话...", file=sys.stderr)

    # 打印最终统计与生成时间轴
    print("\n" + "=" * 60, file=sys.stderr)
    print(f"[*] 实时同步会话总结:", file=sys.stderr)
    print(f"  * 运行状态: {session.stats.status}", file=sys.stderr)
    print(f"  * 解调物理帧数: {session.stats.decoded_frames}", file=sys.stderr)
    print(f"  * 硬件溢出次数: {session.stats.overflow_count}", file=sys.stderr)
    if session.last_snapshot:
        snap = session.last_snapshot
        print(f"  * 最终同步状态: {snap.state.value.upper()} (置信度: {snap.confidence*100:.1f}%)", file=sys.stderr)
        print(f"  * 时钟漂移估计: {snap.clock_drift_ppm:+.2f} ppm", file=sys.stderr)
        print(f"  * TDD 转换保护: {snap.turnaround_guard_s*1e6:.1f} us", file=sys.stderr)

    if recorded_events and t_first is not None and t_last is not None:
        t_span = min(0.100, max(0.050, t_last - t_first))
        viz = TimelineVisualizer(start_time_s=t_first, end_time_s=t_first + t_span)

        # 填入前导与事件
        for t, frame, snap in recorded_events:
            if not (t_first <= t <= t_first + t_span):
                continue
            app = getattr(frame, "application", {}) or {}
            d = app.get("direction")
            dur = calculate_flrc_packet_duration(len(frame.packet.payload))
            if d == "ground_to_air":
                viz.add_event(f"Gnd#{app.get('sequence',0)}", "ground_tx", t, t + dur)
                viz.add_event("Preamble", "actual_arrival", t, t)
            elif d == "air_to_ground":
                viz.add_event(f"Air#{app.get('sequence',0)}", "air_rx", t, t + dur)
                viz.add_event("Preamble", "actual_arrival", t, t)

        p0 = session.state_machine.fhss.predict(t_first)
        viz.add_event(f"Slot {p0.slot_index}", "fhss_slot", p0.slot_start_hw_time, p0.slot_end_hw_time,
                      details=f"{p0.frequency_hz/1e6:.1f}MHz")

        print("\n" + viz.render_ascii(width=80), file=sys.stderr)

        if args.output_html:
            args.output_html.parent.mkdir(parents=True, exist_ok=True)
            args.output_html.write_text(viz.render_html("实时 FHSS + TDD 物理同步时间轴"), encoding="utf-8")
            print(f"[*] 交互式时间轴视图已保存至: {args.output_html.resolve()}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
