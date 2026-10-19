"""独立 HackRF 命令；只有显式 tx/rx 子命令才启动射频流。"""
import argparse
import json
from math import ceil, isfinite
from pathlib import Path
import sys

from .config import BASEBAND_BANDWIDTHS, HackRFConfig
from .device import HackRFDevice
from .library import HackRFError, HackRFLibrary
from .sources import IQFileSource
from .selection_cli import add_device_arguments, report_selected_device


def make_argument_parser():
    parser = argparse.ArgumentParser(description="项目内 libhackrf.dll 后端：设备信息、IQ 发送和采集")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("info", "tx", "rx"):
        command = subparsers.add_parser(name)
        command.add_argument("--dll", type=Path, help="自定义 DLL；默认使用项目内 Windows x64 DLL")
        if name == "info":
            continue
        add_device_arguments(command)
        command.add_argument("--frequency", type=int, required=True, help="HackRF 射频中心频率，Hz")
        command.add_argument("--sample-rate", type=float, default=10_000_000)
        command.add_argument("--baseband-bandwidth-hz", type=int, choices=BASEBAND_BANDWIDTHS,
                             help="HackRF 模拟基带滤波带宽，和 FLRC 标称 BW 分开设置")
        command.add_argument("--amp", action="store_true", help="启用射频放大器")
        command.add_argument("--bias-tee", action="store_true", help="启用天线口供电")
        command.add_argument("--queue-depth", type=int, default=8)
        command.add_argument("--block-bytes", type=int, help="生产/消费缓冲字节数；默认取 DLL USB 缓冲大小")
        command.add_argument("--timeout", type=float, help="秒；默认数据时长加 10 秒")
        command.add_argument("--allow-device-shortfalls", action="store_true",
                             help="诊断模式：固件报告欠载/溢出时保留统计并允许完成")
        if name == "tx":
            command.add_argument("--iq", type=Path, required=True)
            command.add_argument("--iq-format", choices=("cs8", "cu8", "cf32"), default="cs8")
            command.add_argument("--repeat", type=int, default=1)
            command.add_argument("--tx-gain", type=int, default=0)
            command.add_argument("--prefill-blocks", type=int, help="启动前至少准备的块数；默认 queue-depth")
            command.add_argument("--underrun-policy", choices=("stop", "zeros"), default="stop")
        else:
            command.add_argument("--output", type=Path, required=True, help="cs8 IQ 输出")
            command.add_argument("--duration", type=float, required=True, help="有限采集时长，秒")
            command.add_argument("--lna-gain", type=int, default=16)
            command.add_argument("--vga-gain", type=int, default=16)
    return parser


def main(argv=None):
    args = make_argument_parser().parse_args(argv)
    device = None
    try:
        if args.command == "info":
            print(json.dumps(HackRFLibrary(args.dll).info(), ensure_ascii=False, indent=2))
            return 0
        config = HackRFConfig(frequency_hz=args.frequency, sample_rate=args.sample_rate,
                              bandwidth_hz=args.baseband_bandwidth_hz, amp=args.amp, bias_tee=args.bias_tee,
                              tx_gain=getattr(args, "tx_gain", 0), lna_gain=getattr(args, "lna_gain", 16),
                              vga_gain=getattr(args, "vga_gain", 16))
        options = {"queue_depth": args.queue_depth, "block_bytes": args.block_bytes,
                   "allow_device_shortfalls": args.allow_device_shortfalls}
        if args.command == "tx":
            # 校验文件长度/格式后才打开设备，cs8 走不转换的快速路径。
            with IQFileSource(args.iq, args.iq_format, args.repeat) as source:
                options["timeout"] = args.timeout if args.timeout is not None else source.sample_count / config.sample_rate + 10
                with HackRFDevice(HackRFLibrary(args.dll), args.serial, device_index=args.device_index) as device:
                    report_selected_device(device.selected_device)
                    stats = device.transmit(source, config, prefill_blocks=args.prefill_blocks,
                                            underrun_policy=args.underrun_policy, **options)
        else:
            if not isfinite(args.duration) or args.duration <= 0:
                raise ValueError("duration 必须为有限正数")
            sample_count = ceil(args.duration * config.sample_rate)
            options["timeout"] = args.timeout if args.timeout is not None else args.duration + 10
            # 采集文件用 xb，已有文件不会被意外覆盖。
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("xb", buffering=0) as sink:
                with HackRFDevice(HackRFLibrary(args.dll), args.serial, device_index=args.device_index) as device:
                    report_selected_device(device.selected_device)
                    stats = device.receive(sink, config, sample_count, **options)
        print(json.dumps(stats.to_dict(), ensure_ascii=False))
        return 0
    except (HackRFError, OSError, ValueError, TypeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        if device is not None and device.last_stats is not None:
            print(json.dumps(device.last_stats.to_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
