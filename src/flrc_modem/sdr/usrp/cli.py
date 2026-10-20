"""USRP 命令行入口：支持设备探测、参数读取、有限 IQ 接收与发射。"""
import argparse
import json
from math import ceil, isfinite
from pathlib import Path
import sys

from .config import USRPConfig
from .device import USRPDevice
from .library import UHDError, UHDLibrary
from ...duplex_cli import positive_seconds


def make_argument_parser():
    parser = argparse.ArgumentParser(description="USRP (UHD) 后端：设备信息、IQ 发送和接收")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. info
    info_cmd = subparsers.add_parser("info", help="探测并输出 UHD 库信息及网络/本地 USRP 设备列表")
    info_cmd.add_argument("--dll", type=Path, help="自定义 uhd.dll 路径")
    info_cmd.add_argument("--args", default="", help="UHD 设备过滤参数，如 'addr=192.168.10.2'")

    # 2. probe
    probe_cmd = subparsers.add_parser("probe", help="连接指定设备并输出完整硬件树属性")
    probe_cmd.add_argument("--dll", type=Path, help="自定义 uhd.dll 路径")
    probe_cmd.add_argument("--args", default="", help="UHD 设备参数，如 'addr=192.168.10.2'")

    # 3. rx
    rx_cmd = subparsers.add_parser("rx", help="通过 USRP 接收并保存 IQ 采样数据")
    rx_cmd.add_argument("--dll", type=Path, help="自定义 uhd.dll 路径")
    rx_cmd.add_argument("--args", default="", help="UHD 设备参数，如 'addr=192.168.10.2'")
    rx_cmd.add_argument("--master-clock-rate", "--mcr", type=float, help="主时钟频率 MCR (N310: 125e6, 122.88e6, 153.6e6)")
    rx_cmd.add_argument("--frequency", type=float, required=True, help="中心载波频率，Hz")
    rx_cmd.add_argument("--sample-rate", type=float, default=2_500_000.0, help="采样率，Sps（N310 默认 125MHz 下推荐 2.5MSps）")
    rx_cmd.add_argument("--duration", type=float, required=True, help="采集时长，秒")
    rx_cmd.add_argument("--gain", type=float, default=30.0, help="接收增益，dB（默认 30.0）")
    rx_cmd.add_argument("--channel", type=int, default=0, help="射频通道索引，从 0 起算（默认 0）")
    rx_cmd.add_argument("--antenna", type=str, help="天线端口选择，如 'RX2' 或 'TX/RX'")
    rx_cmd.add_argument("--bandwidth-hz", type=float, help="模拟前端滤波器带宽，Hz")
    rx_cmd.add_argument("--clock-source", type=str, choices=("internal", "external", "gpsdo"), help="参考时钟源")
    rx_cmd.add_argument("--time-source", type=str, choices=("internal", "external", "gpsdo", "sfp0"), help="时间同步源")
    rx_cmd.add_argument("--iq-format", choices=("cs8", "cf32"), default="cs8", help="输出采样格式（默认 cs8）")
    rx_cmd.add_argument("--output", type=Path, required=True, help="输出 IQ 文件路径")

    # 4. tx
    tx_cmd = subparsers.add_parser("tx", help="读取 IQ 文件并通过 USRP 发射")
    tx_cmd.add_argument("--dll", type=Path, help="自定义 uhd.dll 路径")
    tx_cmd.add_argument("--args", default="", help="UHD 设备参数，如 'addr=192.168.10.2'")
    tx_cmd.add_argument("--master-clock-rate", "--mcr", type=float, help="主时钟频率 MCR (N310: 125e6, 122.88e6, 153.6e6)")
    tx_cmd.add_argument("--iq", type=Path, required=True, help="待发射的 IQ 文件路径")
    tx_cmd.add_argument("--frequency", type=float, required=True, help="中心载波频率，Hz")
    tx_cmd.add_argument("--sample-rate", type=float, default=2_500_000.0, help="采样率，Sps（N310 默认 125MHz 下推荐 2.5MSps）")
    tx_cmd.add_argument("--gain", type=float, default=30.0, help="发射增益，dB（默认 30.0）")
    tx_cmd.add_argument("--channel", type=int, default=0, help="射频通道索引，从 0 起算（默认 0）")
    tx_cmd.add_argument("--antenna", type=str, help="天线端口选择，如 'TX/RX'")
    tx_cmd.add_argument("--bandwidth-hz", type=float, help="模拟前端滤波器带宽，Hz")
    tx_cmd.add_argument("--clock-source", type=str, choices=("internal", "external", "gpsdo"), help="参考时钟源")
    tx_cmd.add_argument("--time-source", type=str, choices=("internal", "external", "gpsdo", "sfp0"), help="时间同步源")
    tx_cmd.add_argument("--iq-format", choices=("cs8", "cf32"), default="cs8", help="输入采样格式（默认 cs8）")
    tx_cmd.add_argument("--repeat", type=int, default=1, help="重复发射次数（默认 1）")

    # 5. sync
    sync_cmd = subparsers.add_parser("sync", help="实时流式接入 USRP 执行 FHSS + TDD 硬件时间戳同步分析")
    sync_cmd.add_argument("--dll", type=Path, help="自定义 uhd.dll 路径")
    sync_cmd.add_argument("--args", default="addr=192.168.10.2", help="UHD 设备参数，如 'addr=192.168.10.2'")
    sync_cmd.add_argument("--master-clock-rate", "--mcr", type=float, help="主时钟频率 MCR (N310: 125e6, 122.88e6, 153.6e6)")
    sync_cmd.add_argument("--frequency", type=int, default=2471660000, help="中心载波频率，Hz (默认 2471660000)")
    sync_cmd.add_argument("--sample-rate", type=float, default=2_500_000.0, help="采样率，Sps（N310 推荐 2.5MSps）")
    sync_cmd.add_argument("--gain", type=float, default=30.0, help="接收增益，dB（默认 30.0）")
    sync_cmd.add_argument("--channel", type=int, default=0, help="通道号（默认 0）")
    sync_cmd.add_argument("--sync", default=None, help="目标物理差分同步字 (十六进制；可选，未指定时从 --rf-config 读取或自适应捕获)")
    sync_cmd.add_argument("--protocol", default="dk32s", help="业务协议 (默认 dk32s)")
    sync_cmd.add_argument("--duration", type=float, default=0.0, help="持续时间，秒 (0 为持续运行)")
    sync_cmd.add_argument("--timed-hopping", action="store_true", help="启用 UHD Timed Commands 硬件跳频")
    sync_cmd.add_argument("--output", type=Path, help="保存解调结果 JSONL 路径")
    sync_cmd.add_argument("--output-html", type=Path, default=Path("outputs/live_sync_timeline.html"), help="输出 HTML 时间轴")

    # 6. duplex
    duplex_cmd = subparsers.add_parser("duplex", help="交互式 TDD 同步双工收发机（持续同步校准，按需时隙投递与接收回复）")
    duplex_cmd.add_argument("--args", default="addr=192.168.10.2", help="UHD 设备参数，如 'addr=192.168.10.2'")
    duplex_cmd.add_argument("--master-clock-rate", "--mcr", type=float, help="主时钟频率 MCR")
    duplex_cmd.add_argument("--frequency", type=int, default=2471660000, help="中心载波频率，Hz")
    duplex_cmd.add_argument("--sample-rate", type=float, default=2_500_000.0, help="采样率，Sps (默认 2.5MSps)")
    duplex_cmd.add_argument("--gain", type=float, default=30.0, help="接收增益，dB")
    duplex_cmd.add_argument("--tx-gain", type=float, default=30.0, help="发射增益，dB")
    duplex_cmd.add_argument("--channel", type=int, default=0, help="物理通道索引")
    duplex_cmd.add_argument("--sync", default=None, help="目标物理差分同步字 (十六进制；可选，未指定时从 --rf-config 读取或自适应捕获)")
    duplex_cmd.add_argument("--protocol", "--encoder", dest="protocol", default="dk32s", help="业务协议编码插件 (默认 dk32s)")
    duplex_cmd.add_argument("--cr", dest="coding_rate", choices=["1/2", "3/4", "1"], default="3/4", help="编码率 (默认 3/4)")

    # 统一数据输入源参数
    duplex_source = duplex_cmd.add_mutually_exclusive_group()
    duplex_source.add_argument("--payload-hex", help="6..127B 原始物理载荷，十六进制")
    duplex_source.add_argument("--payload-file", type=Path, help="原始载荷二进制文件（非 IQ）")
    duplex_source.add_argument("--message-file", type=Path, help="协议业务字段 JSON 模板，配合 --protocol")
    duplex_source.add_argument("--stdin-jsonl", action="store_true", help="逐行读取动态 JSON 报文")

    duplex_cmd.add_argument("--no-wait", action="store_true", help="不等待对端回复")
    duplex_cmd.add_argument("--timeout", type=float, default=1.0, help="等待回复超时 (秒)")
    duplex_cmd.add_argument("--sync-timeout", type=positive_seconds, default=5.0,
                            help="等待同步锁定的最长时间，秒 (默认 5)")
    duplex_cmd.add_argument("--json", action="store_true", help="JSON 格式输出")

    return parser


def main(argv=None) -> int:
    args = make_argument_parser().parse_args(argv)
    try:
        library = UHDLibrary(getattr(args, "dll", None))

        if args.command == "info":
            info = library.info(args=args.args)
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0

        if args.command == "probe":
            with USRPDevice(library, device_args=args.args) as dev:
                print(dev.get_pp_string())
            return 0

        if args.command == "rx":
            if not isfinite(args.duration) or args.duration <= 0:
                raise ValueError("duration 必须为有限正数")

            config = USRPConfig(
                frequency_hz=args.frequency,
                sample_rate=args.sample_rate,
                gain=args.gain,
                channel=args.channel,
                antenna=args.antenna,
                bandwidth_hz=args.bandwidth_hz,
                device_args=args.args,
                clock_source=args.clock_source,
                time_source=args.time_source,
                master_clock_rate=args.master_clock_rate,
                cpu_format=args.iq_format,
            )
            sample_count = ceil(args.duration * config.sample_rate)
            args.output.parent.mkdir(parents=True, exist_ok=True)

            with args.output.open("xb", buffering=0) as sink:
                with USRPDevice(library, device_args=args.args) as dev:
                    stats = dev.receive(sink, config, sample_count)
            print(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "tx":
            if not args.iq.is_file():
                raise FileNotFoundError(f"IQ 文件不存在: {args.iq}")
            if args.repeat < 1:
                raise ValueError("repeat 必须大于或等于 1")

            config = USRPConfig(
                frequency_hz=args.frequency,
                sample_rate=args.sample_rate,
                gain=args.gain,
                channel=args.channel,
                antenna=args.antenna,
                bandwidth_hz=args.bandwidth_hz,
                device_args=args.args,
                clock_source=args.clock_source,
                time_source=args.time_source,
                master_clock_rate=args.master_clock_rate,
                cpu_format=args.iq_format,
            )

            with args.iq.open("rb") as f:
                content = f.read()

            full_payload = content * args.repeat
            with USRPDevice(library, device_args=args.args) as dev:
                stats = dev.transmit(full_payload, config)
            print(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2))
            return 0

        elif args.command == "sync":
            from ...sync_cli import main as run_sync
            forward_args = [
                "--sdr", "usrp",
                "--args", args.args,
                "--frequency", str(args.frequency),
                "--sample-rate", str(args.sample_rate),
                "--gain", str(args.gain),
                "--channel", str(args.channel),
                "--protocol", args.protocol,
                "--duration", str(args.duration),
            ]
            if args.sync:
                forward_args.extend(["--sync", args.sync])
            if args.master_clock_rate:
                forward_args.extend(["--master-clock-rate", str(args.master_clock_rate)])
            if args.timed_hopping:
                forward_args.append("--timed-hopping")
            if args.output:
                forward_args.extend(["--output", str(args.output)])
            if args.output_html:
                forward_args.extend(["--output-html", str(args.output_html)])
            return run_sync(forward_args)

        elif args.command == "duplex":
            from ...duplex_cli import main as run_duplex
            forward_args = [
                "--sdr", "usrp",
                "--args", args.args,
                "--frequency", str(args.frequency),
                "--sample-rate", str(args.sample_rate),
                "--gain", str(args.gain),
                "--tx-gain", str(args.tx_gain),
                "--channel", str(args.channel),
                "--protocol", args.protocol,
                "--cr", args.coding_rate,
                "--timeout", str(args.timeout),
                "--sync-timeout", str(args.sync_timeout),
            ]
            if args.sync:
                forward_args.extend(["--sync", args.sync])
            if args.master_clock_rate:
                forward_args.extend(["--master-clock-rate", str(args.master_clock_rate)])
            if args.payload_hex:
                forward_args.extend(["--payload-hex", args.payload_hex])
            elif args.payload_file:
                forward_args.extend(["--payload-file", str(args.payload_file)])
            elif args.message_file:
                forward_args.extend(["--message-file", str(args.message_file)])
            elif args.stdin_jsonl:
                forward_args.append("--stdin-jsonl")
            if args.no_wait:
                forward_args.append("--no-wait")
            if args.json:
                forward_args.append("--json")
            return run_duplex(forward_args)

        return 0
    except (UHDError, OSError, ValueError) as err:
        print(f"错误: {err}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
