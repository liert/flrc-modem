"""交互式/流式 TDD 同步双工收发命令行接口。

后台持续接收 IQ 采样并校准同步，用户可在同步后输入任意载荷数据打出并接收对端回复。
统一支持 --payload-hex、--payload-file、--message-file 与 --stdin-jsonl 传参。
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from math import isfinite
from pathlib import Path
import sys
import time

from .encoding.input import read_message
from .sync import (
    DEFAULT_DK32S_HOPPING_TABLE,
    LiveTDDTransceiver,
    SyncState,
    TDDMode,
    parse_rf_config,
)


def positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("超时时间必须为有限正数（秒）") from None
    if not isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("超时时间必须为有限正数（秒）")
    return seconds


def report_sync_failure(transceiver, timeout: float):
    info = transceiver.sync_diagnostics()
    message = (f"[-] 同步未锁定（等待上限 {timeout:g}s）：状态={info['state']}，"
               f"CRC有效帧={info['decoded_frames']}，地面时间锚点={info['anchor_count']}，"
               f"锚点跨度={info['anchor_span_s']:.3f}s，溢出={info['overflow_count']}")
    counts, rejects = info.get("frame_counts", {}), info.get("rejection_counts", {})
    message += (f"；地面帧={counts.get('ground_to_air', 0)}，天空回传={counts.get('air_to_ground', 0)}，"
                f"解析失败={rejects.get('parser_error', 0)}，时钟观测拒绝={rejects.get('clock_rejected', 0)}，"
                f"累计接受锚点={info.get('accepted_anchors_total', 0)}")
    if info["error"]:
        message += f"；接收错误：{info['error']}"
    if info.get("last_parser_error"):
        message += f"；最近解析错误：{info['last_parser_error']}"
    if info.get("loss_reason"):
        message += f"；最近失锁原因：{info['loss_reason']}"
    print(message, file=sys.stderr)


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
    parser.add_argument("--antenna", default="RX2", help="接收天线端口 (默认 RX2)")
    parser.add_argument("--timed-hopping", action="store_true", help="启用 N310 定时数字跳频跟踪")
    parser.add_argument("--sync", default=None,
                        help="4B 物理差分同步字，十六进制 (未指定时优先从 --rf-config 读取或空口自适应捕获)")
    parser.add_argument("--protocol", "--encoder", dest="protocol", default="dk32s",
                        help="业务层协议编码插件：dk32s 或 Python模块:函数名 (默认 dk32s)")
    parser.add_argument("--cr", dest="coding_rate", choices=["1/2", "3/4", "1"], default="3/4",
                        help="FLRC 卷积纠错编码率 (默认 3/4)")
    parser.add_argument("--rf-config", type=Path,
                        help="包含 hopping_table 的射频配置文件")

    # 统一的数据输入源参数（与 transmit / modulate 完全对齐）
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--payload-hex", help="6..127B 原始物理载荷，十六进制")
    source.add_argument("--payload-file", type=Path, help="原始载荷二进制文件（非 IQ）")
    source.add_argument("--message-file", type=Path, help="协议业务字段 JSON 模板，配合 --protocol")
    source.add_argument("--stdin-jsonl", action="store_true", help="从标准输入逐行读取动态 JSON 报文并在同步时隙连续投递")

    parser.add_argument("--no-wait", action="store_true",
                        help="发送后不等待对端回复")
    parser.add_argument("--timeout", type=float, default=1.0,
                        help="等待对端回复的超时时间 (秒；默认 1.0)")
    parser.add_argument("--sync-timeout", type=positive_seconds, default=5.0,
                        help="等待同步锁定/重新锁定的最长时间，秒 (默认 5；弱信号可设 30)")
    parser.add_argument("--json", action="store_true",
                        help="以结构化 JSON / JSONL 格式输出结果")
    return parser


def main(argv=None) -> int:
    try:
        return _main(argv)
    except (ValueError, TypeError, OSError) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2


def _main(argv=None) -> int:
    parser = make_argument_parser()
    args = parser.parse_args(argv)

    # 跳频表与同步字
    hopping_table = None
    sync_word = args.sync
    if args.rf_config and args.rf_config.is_file():
        try:
            data = json.loads(args.rf_config.read_text(encoding="utf-8-sig"))
            table_from_cfg, sync_from_cfg = parse_rf_config(data)
            if table_from_cfg:
                hopping_table = table_from_cfg
            if not sync_word and sync_from_cfg:
                sync_word = sync_from_cfg
        except Exception as e:
            print(f"[!] 读取射频配置警告: {e}", file=sys.stderr)

    transceiver = LiveTDDTransceiver(
        sdr_type=args.sdr,
        usrp_args=args.args,
        frequency_hz=args.frequency,
        sample_rate=args.sample_rate,
        master_clock_rate=args.master_clock_rate,
        gain=args.gain,
        tx_gain=args.tx_gain,
        channel=args.channel,
        antenna=args.antenna,
        timed_hopping=args.timed_hopping,
        sync_word=sync_word,
        protocol=args.protocol,
        coding_rate=args.coding_rate,
        hopping_table=hopping_table,
    )

    # 1. 单次非交互发送模式 (--payload-hex, --payload-file, --message-file)
    single_payload = None
    if args.payload_hex is not None:
        single_payload = args.payload_hex
    elif args.payload_file is not None:
        if not args.payload_file.is_file():
            print(f"[-] 载荷文件不存在: {args.payload_file}", file=sys.stderr)
            return 2
        with args.payload_file.open("rb") as f:
            single_payload = f.read(128)
    elif args.message_file is not None:
        if not args.message_file.is_file():
            print(f"[-] 业务模板文件不存在: {args.message_file}", file=sys.stderr)
            return 2
        single_payload = read_message(args.message_file)

    if single_payload is not None:
        try:
            with transceiver:
                print(f"[*] 启动后台同步引擎，等待时钟与时隙锁定...", file=sys.stderr)
                if not transceiver.wait_locked(timeout=args.sync_timeout):
                    report_sync_failure(transceiver, args.sync_timeout)
                    return 1

                res = transceiver.send_and_wait_reply(
                    single_payload,
                    wait_reply=not args.no_wait,
                    reply_timeout=args.timeout,
                    sync_timeout=args.sync_timeout,
                )
                if args.json:
                    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
                else:
                    if res.success:
                        print(f"[TX 成功] 硬件时刻 t={res.tx_hw_time:.6f}s | Slot {res.tx_slot} ({res.tx_frequency_hz / 1e6:.2f}MHz) | 载荷: {res.tx_payload.hex()}")
                        if res.rx_reply:
                            rtt = f"{res.rtt_seconds * 1000:.2f}ms" if res.rtt_seconds is not None else "未知"
                            print(f"[RX 回复] 硬件时刻 t={res.rx_reply.time_s:.6f}s (RTT: {rtt}) | 样本功率: {res.rx_reply.sample_power_db:.1f}dB | 载荷: {res.rx_reply.packet.payload.hex()}")
                        elif not args.no_wait:
                            print(f"[RX 回复] {res.error or '未在窗口内收到对端回复'}")
                    else:
                        print(f"[{'TX 完成，交互未完成' if res.tx_completed else 'TX 失败'}] {res.error}")
                if not res.success:
                    return 2
            return 0
        except KeyboardInterrupt:
            return 130

    # 2. 动态标准输入流模式 (--stdin-jsonl)
    if args.stdin_jsonl:
        try:
            with transceiver:
                print(f"[*] 等待同步锁定...", file=sys.stderr)
                if not transceiver.wait_locked(timeout=args.sync_timeout):
                    report_sync_failure(transceiver, args.sync_timeout)
                    return 1
                for line in sys.stdin:
                    line = line.strip()
                    if not line:
                        continue
                    res = transceiver.send_and_wait_reply(
                        line,
                        wait_reply=not args.no_wait,
                        reply_timeout=args.timeout,
                        sync_timeout=args.sync_timeout,
                    )
                    if args.json:
                        print(json.dumps(res.to_dict(), ensure_ascii=False))
                    else:
                        status = "REPLY_OK" if res.rx_reply else "NO_REPLY" if not args.no_wait else "TX_ONLY"
                        print(f"[{status}] tx_time={res.tx_hw_time:.6f}s slot={res.tx_slot} rtt={res.rtt_seconds} payload={res.tx_payload.hex()}")
            return 0
        except KeyboardInterrupt:
            return 130

    # 3. 交互式控制台模式 (REPL，未传入数据源参数时的默认体验)
    print("=" * 72)
    print("  FLRC 交互式 TDD 同步双工收发控制台 (USRP N310)")
    print("  * 后台持续接收 IQ 采样并动态校准 50ms 跳频与硬件时钟同步")
    print("  * 任意时刻输入数据并回车，引擎将在精确同步时隙打出并捕获回复")
    print("=" * 72)
    print(f"[*] 设备参数: {args.args}, 监听频率: {args.frequency / 1e6:.2f} MHz, 编码协议: {args.protocol}")
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
                    print("    输入提示: 直接输入十六进制(如 '01 02 03 04')、JSON(如 '{\"type\":\"ping\"}')、'@文件路径'，输入 'exit' 退出\n")
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

                # 支持从文件载入 (@path)
                data_to_send: bytes | str | dict = line
                if line.startswith("@"):
                    fpath = Path(line[1:].strip())
                    if not fpath.is_file():
                        print(f"  [-] 找不到文件: {fpath}")
                        continue
                    if fpath.suffix.lower() == ".json":
                        data_to_send = read_message(fpath)
                    else:
                        data_to_send = fpath.read_bytes()

                # 发送并等待回复
                print(f"[*] 规划时隙发射载荷中...")
                res = transceiver.send_and_wait_reply(
                    data_to_send,
                    wait_reply=not args.no_wait,
                    reply_timeout=args.timeout,
                    sync_timeout=args.sync_timeout,
                )

                if res.success:
                    print(f"  [TX 打出] 硬件时刻: {res.tx_hw_time:.6f}s | Slot {res.tx_slot} ({res.tx_frequency_hz / 1e6:.2f}MHz) | 载荷: {res.tx_payload.hex()}")
                    if res.rx_reply:
                        rtt = f"{res.rtt_seconds * 1000:.2f}ms" if res.rtt_seconds is not None else "未知"
                        print(f"  [RX 回复] 硬件时刻: {res.rx_reply.time_s:.6f}s (RTT: {rtt}) | 样本功率: {res.rx_reply.sample_power_db:.1f}dB | 载荷: {res.rx_reply.packet.payload.hex()}")
                    elif not args.no_wait:
                        print(f"  [RX 回复] 提示: {res.error or '未在超时时间内收到对端应答'}")
                else:
                    print(f"  [{'TX 完成，交互未完成' if res.tx_completed else 'TX 未能打出'}] {res.error}")

    except KeyboardInterrupt:
        print("\n[*] 正在退出双工收发机...")
        return 130

    print("[*] 双工收发已关闭。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
