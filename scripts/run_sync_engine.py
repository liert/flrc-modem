"""FHSS + TDD 硬件时间戳同步引擎运行与时间轴可视化生成脚本。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from flrc_modem.models import DecodedFrame, PhysicalPacket
from flrc_modem.sync import (
    AD9361TuningBudget,
    SyncState,
    SyncStateMachine,
    TDDMode,
    TimelineVisualizer,
    calculate_flrc_packet_duration,
)
from flrc_modem.sync.live import resolve_rf_settings


def load_hopping_table(rf_config_path: Path) -> list[int]:
    return resolve_rf_settings(rf_config_path)["hopping_table"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=Path, default=Path("outputs/passive-current-20261020.jsonl"),
                        help="包含 FLRC 解调结果的 JSONL 文件")
    parser.add_argument("--rf-config", type=Path, default=None,
                        help="包含 hopping_table 的射频配置文件")
    parser.add_argument("--sync", default=None, help="目标物理差分同步字 (4B Hex；默认从 --rf-config 读取或自适应捕获)")
    parser.add_argument("--tdd-mode", choices=["reception_triggered", "fixed_periodic"],
                        default="reception_triggered", help="TDD 候选模型 (默认 reception_triggered)")
    parser.add_argument("--output-html", type=Path, default=Path("outputs/sync_timeline.html"),
                        help="输出交互式 HTML 时间轴路径")
    parser.add_argument("--ascii-width", type=int, default=80, help="终端 ASCII 字符画宽度")
    args = parser.parse_args(argv)

    if not args.frames.is_file():
        print(f"[-] 找不到输入帧文件: {args.frames}", file=sys.stderr)
        return 1

    try:
        settings = resolve_rf_settings(args.rf_config, args.sync)
    except (OSError, ValueError) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2
    table, sync_word = settings["hopping_table"], settings["sync"]
    for warning in settings["warnings"]:
        print(f"警告: {warning}", file=sys.stderr)

    tdd_mode = TDDMode.RECEPTION_TRIGGERED if args.tdd_mode == "reception_triggered" else TDDMode.FIXED_PERIODIC
    sm = SyncStateMachine(table, sync_word=sync_word, tdd_mode=tdd_mode)

    records = [json.loads(line) for line in args.frames.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        print("错误: 输入帧文件没有物理解调记录", file=sys.stderr)
        return 2
    print(f"[*] 读取到 {len(records)} 条物理解调记录，开始灌入 FHSS + TDD 同步引擎...")

    matched_pairs = []
    last_ground_frame = None

    for r in records:
        app = r.get("application", {})
        f = DecodedFrame(
            time_s=r["time_s"],
            frequency_hz=r["frequency_hz"],
            cfo_hz=r.get("cfo_hz", 0.0),
            sample_power_db=r.get("sample_power_db", 0.0),
            correlation_score=r.get("correlation_score", 1.0),
            preamble_errors=0,
            packet=PhysicalPacket(
                sync_word=bytes.fromhex(r["sync_word_hex"]),
                header=bytes.fromhex(r.get("hardware_header_hex", "0021")),
                payload=bytes.fromhex(r["payload_hex"]),
                crc=bytes.fromhex(r["crc_hex"]),
                fec_path_metric=0.0,
            ),
        )
        object.__setattr__(f, "application", app)

        accepted = sm.on_frame(r["time_s"], f)

        d = app.get("direction")
        if d == "ground_to_air":
            last_ground_frame = (r["time_s"], r)
        elif d == "air_to_ground" and last_ground_frame is not None:
            dt = r["time_s"] - last_ground_frame[0]
            if 0 < dt < 0.005:
                matched_pairs.append((last_ground_frame[1], r, dt))

    snap = sm.predict(records[-1]["time_s"])
    print(f"[+] 状态机状态: {snap.state.value.upper()} (置信度: {snap.confidence*100:.1f}%)")
    print(f"[+] 估计频偏漂移: {snap.clock_drift_ppm:+.2f} ppm")
    print(f"[+] 估计 TDD 收发转换保护时间: {snap.turnaround_guard_s*1e6:.1f} us")
    print(f"[+] 当前时隙索引: Slot {snap.slot_index} (载频: {snap.frequency_hz / 1e6:.2f} MHz)")
    print(f"[+] 下一跳频点: Slot {snap.next_slot_index} (载频: {snap.next_frequency_hz / 1e6:.2f} MHz)")

    # 构建时间轴可视化
    # 取前两个完整时隙 (约 100ms) 进行高精度切片
    t_start = records[0]["time_s"]
    t_end = t_start + 0.100  # 100ms
    viz = TimelineVisualizer(start_time_s=t_start, end_time_s=t_end)

    # 填充时隙
    p0 = sm.fhss.predict(t_start)
    s0_start = p0.slot_start_hw_time
    s0_end = p0.slot_end_hw_time
    viz.add_event(f"Slot {p0.slot_index}", "fhss_slot", s0_start, s0_end, details=f"{p0.frequency_hz/1e6:.1f}MHz")

    p1 = sm.fhss.predict(s0_end + 0.001)
    viz.add_event(f"Slot {p1.slot_index}", "fhss_slot", p1.slot_start_hw_time, p1.slot_end_hw_time, details=f"{p1.frequency_hz/1e6:.1f}MHz")

    # 填充槽尾守候期
    viz.add_event("Guard", "slot_guard", s0_end - 0.011, s0_end)
    viz.add_event("Guard", "slot_guard", p1.slot_end_hw_time - 0.011, p1.slot_end_hw_time)

    # 填充数据包事件
    for r in records:
        t = r["time_s"]
        if not (t_start <= t <= t_end):
            continue
        app = r.get("application", {})
        d = app.get("direction")
        l = r["payload_length"]
        dur = calculate_flrc_packet_duration(l)

        if d == "ground_to_air":
            viz.add_event(f"Gnd#{app.get('sequence',0)}", "ground_tx", t, t + dur)
            viz.add_event("Preamble", "actual_arrival", t, t)
            # 记录预测到达
            pred_t = sm.fhss._estimate_hw(app["timestamp_ticks"] / 10000.0)
            err_us = (pred_t - t) * 1e6
            viz.add_event("Pred", "predicted_arrival", pred_t, pred_t, details=f"Seq {app.get('sequence')}", error_us=err_us)
        elif d == "air_to_ground":
            viz.add_event(f"Air#{app.get('sequence',0)}", "air_rx", t, t + dur)
            viz.add_event("Preamble", "actual_arrival", t, t)

    # 渲染 ASCII
    print("\n" + viz.render_ascii(width=args.ascii_width))

    # 输出 HTML
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    html_content = viz.render_html(title="FLRC FHSS + TDD 硬件时间轴实测与预测")
    args.output_html.write_text(html_content, encoding="utf-8")
    print(f"\n[*] 交互式时间轴可视化已保存至: {args.output_html.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
