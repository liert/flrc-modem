"""交互式/流式 TDD 同步双工收发命令行接口。

后台持续接收 IQ 采样并校准同步，用户可在同步后输入任意载荷数据打出并接收对端回复。
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import time

from .sync import (
    DEFAULT_DK32S_HOPPING_TABLE,
    LiveTDDTransceiver,
    SyncState,
    TDDMode,
)


def make_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flrc duplex",
        description="交互式 TDD 同步双工收发机（后台持续接收校准同步，按需精准时隙投递与接收回复）",
    )
    parser.add_argument("--sdr", choices=["usrp"], default="usrp",
                        help="SDR 硬件类型 (当前支持 usrp)")
    parser.add_argument("--args", default="addr=192.168.10.2",
                        help="USRP 设备定位参数 (默认 addr=192.168.10.2)")
    parser.add_argument("--master-clock-rate", "--mcr", type=float, default=None,
                        help="USRP 主时钟 MCR (N310: 125e6 默认, 122.88e6, 153.6e6)")
    parser.add_argument("--frequency", type=int, default=2471660000,
                        help="射频初始监听频率 (默认 2471660000 Hz, 即 DK32S 常用 Slot 17)")
    parser.add_argument("--sample-rate", type=float, default=None,
                        help="采样率，单位 Sps (默认 2500000 即 2.5MSps 50分频整除)")
    parser.add_argument("--gain", type=float, default=30.0,
                        help="接收射频增益，单位 dB (默认 30.0)")
    parser.add_argument("--tx-gain", type=float, default=30.0,
                        help="发射射频增益，单位 dB (默认 30.0)")
    parser.add_argument("--channel", type=int, default=0,
                        help="USRP 物理通道号 (默认 0)")
    parser.add_argument("--sync", default="ecee6698",
                        help="物理差分同步字 (默认 ecee6698)")
    parser.add_argument("--coding-rate", default="3/4", choices=["1/2", "3/4", "1"],
                        help="FLRC 卷积纠错编码率 (默认 3/4)")
    parser.add_argument("--rf-config", type=Path,
                        help="包含 hopping_table 的射频配置文件")
    parser.add_argument("--data", type=str,
                        help="单次发送的十六进制数据（非交互模式，发送完毕后退出）")
    parser.add_argument("--no-wait", action="store_true",
                        help="发送后不等待对端回复")
    parser.add_argument("--timeout", type=float, default=1.0,
                        help="等待对端回复的超时时间 (秒；默认 1.0)")
    parser.add_argument("--json", action="store_true",
                        help="以结构化 JSON 格式输出结果")
    return parser


def main(argv=None) -> int:
    parser = make_argument_parser()
    args = parser.parse_args(argv)

    # 跳频表
    hopping_table = None
    if args.rf_config and args.rf_config.is_file():
        data = json.loads(args.rf_config.read_text(encoding="utf-8-sig"))
        hopping_table = [int(item["frequency_hz"]) for item in data["hopping_table"]]

    transceiver = LiveTDDTransceiver(
        sdr_type=args.sdr,
        usrp_args=args.args,
        frequency_hz=args.frequency,
        sample_rate=args.sample_rate,
        master_clock_rate=args.master_clock_rate,
        gain=args.gain,
        tx_gain=args.tx_gain,
        channel=args.channel,
        sync_word=args.sync,
        coding_rate=args.coding_rate,
        hopping_table=hopping_table,
    )

    # 1. 单次非交互发送模式
    if args.data:
        try:
            with transceiver:
                print(f"[*] 等待同步锁定...", file=sys.stderr)
                if not transceiver.wait_locked(timeout=5.0):
                    print(f"[-] 同步超时未锁定，当前无法进行精准时隙投递", file=sys.stderr)
                    return 1

                res = transceiver.send_and_wait_reply(
                    args.data,
                    wait_reply=not args.no_wait,
                    reply_timeout=args.timeout,
                )
                if args.json:
                    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
                else:
                    if res.success:
                        print(f"[TX 成功] 硬件时刻 t={res.tx_hw_time:.6f}s | Slot {res.tx_slot} ({res.tx_frequency_hz / 1e6:.2f}MHz) | 载荷: {res.tx_payload.hex()}")
                        if res.rx_reply:
                            rtt = f"{res.rtt_seconds * 1000:.2f}ms" if res.rtt_seconds is not None else "未知"
                            print(f"[RX 回复] 硬件时刻 t={res.rx_reply.time_s:.6f}s (RTT: {rtt}) | SNR: {res.rx_reply.sample_power_db:.1f}dB | 载荷: {res.rx_reply.packet.payload.hex()}")
                        elif not args.no_wait:
                            print(f"[RX 回复] {res.error or '未在窗口内收到对端回复'}")
                    else:
                        print(f"[TX 失败] {res.error}")
                        return 2
            return 0
        except KeyboardInterrupt:
            return 130

    # 2. 交互式控制台模式 (REPL)
    print("=" * 72)
    print("  FLRC 交互式 TDD 同步双工收发控制台 (USRP N310)")
    print("  * 后台持续接收 IQ 采样并动态校准 50ms 跳频与硬件时钟同步")
    print("  * 任意时刻输入数据并回车，引擎将在精确同步时隙打出并捕获回复")
    print("=" * 72)
    print(f"[*] 设备参数: {args.args}, 监听频率: {args.frequency / 1e6:.2f} MHz")
    print("[*] 正在启动后台持续接收与同步引擎...", flush=True)

    try:
        with transceiver:
            print("[*] 后台同步线程已运行。正在搜网对齐物理时隙...")
            locked_notified = False

            while True:
                # 状态探测
                snap = transceiver.last_snapshot or transceiver.session.last_snapshot
                state_str = snap.state.value.upper() if snap else "SEARCHING"
                slot_str = str(snap.slot_index) if snap else "?"
                drift_str = f"{snap.clock_drift_ppm:+.2f}ppm" if snap else "0ppm"

                if snap and snap.state in (SyncState.LOCKED, SyncState.HOLDOVER) and not locked_notified:
                    print(f"\n[+] 同步已锁定 (LOCKED)！时钟漂移: {drift_str} | 当前时隙: {slot_str}")
                    print("    输入提示: 直接输入十六进制(如 '01 02 03 04')、'ping' 或 'status'，输入 'exit' 退出\n")
                    locked_notified = True

                prompt = f"[{state_str} Slot {slot_str}] flrc > "
                try:
                    line = input(prompt).strip()
                except EOFError:
                    break

                if not line:
                    continue

                cmd_lower = line.lower()
                if cmd_lower in ("exit", "quit", "q"):
                    break

                if cmd_lower == "status":
                    snap = transceiver.last_snapshot or transceiver.session.last_snapshot
                    if snap:
                        print(f"  状态: {snap.state.value.upper()} (置信度 {snap.confidence * 100:.1f}%)")
                        print(f"  当前时隙: {snap.slot_index} ({snap.frequency_hz / 1e6:.2f} MHz)")
                        print(f"  硬件时刻: {snap.hw_time_s:.6f} s | 漂移: {snap.clock_drift_ppm:+.2f} ppm")
                        print(f"  TDD 阶段: {snap.tdd_phase.value} (窗口剩余 {snap.tdd_window_remaining_s * 1000:.1f} ms)")
                        print(f"  下一跳频: {snap.next_slot_index} ({snap.next_frequency_hz / 1e6:.2f} MHz @ {snap.next_hop_hw_time:.6f}s)")
                    else:
                        print("  尚未获取有效同步快照")
                    continue

                if cmd_lower == "ping":
                    line = "01 02 03 04 05 06"

                # 发送并等待回复
                print(f"[*] 规划时隙发射载荷中...")
                res = transceiver.send_and_wait_reply(
                    line,
                    wait_reply=not args.no_wait,
                    reply_timeout=args.timeout,
                    sync_timeout=2.0,
                )

                if res.success:
                    print(f"  [TX 打出] 硬件时刻: {res.tx_hw_time:.6f}s | Slot {res.tx_slot} ({res.tx_frequency_hz / 1e6:.2f}MHz) | 载荷: {res.tx_payload.hex()}")
                    if res.rx_reply:
                        rtt = f"{res.rtt_seconds * 1000:.2f}ms" if res.rtt_seconds is not None else "未知"
                        print(f"  [RX 回复] 硬件时刻: {res.rx_reply.time_s:.6f}s (RTT: {rtt}) | SNR: {res.rx_reply.sample_power_db:.1f}dB | 载荷: {res.rx_reply.packet.payload.hex()}")
                    elif not args.no_wait:
                        print(f"  [RX 回复] 提示: {res.error or '未在超时时间内收到对端应答'}")
                else:
                    print(f"  [TX 未能打出] {res.error}")

    except KeyboardInterrupt:
        print("\n[*] 正在退出双工收发机...")
        return 130

    print("[*] 双工收发已关闭。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
