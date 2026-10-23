"""独立 HackRF 命令；只有显式 tx/rx 子命令才启动射频流。"""
import argparse
import json
from pathlib import Path
import sys

from .config import BASEBAND_BANDWIDTHS
from .library import HackRFError, HackRFLibrary
from .selection_cli import add_device_arguments
from ..cli import forward_iq_command


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
    if args.command != "info":
        return forward_iq_command(args, "hackrf", library_factory=HackRFLibrary)
    try:
        print(json.dumps(HackRFLibrary(args.dll).info(), ensure_ascii=False, indent=2))
        return 0
    except (HackRFError, OSError, ValueError, TypeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
