"""统一 IQ 采样采集命令行接口，支持 USRP 与 HackRF 后端。"""
from __future__ import annotations

import argparse
import json
from math import ceil, isfinite
from pathlib import Path
import sys

from .io.ui_events import get_emitter
from .sdr.hackrf.config import BASEBAND_BANDWIDTHS, HackRFConfig
from .sdr.hackrf.device import HackRFDevice
from .sdr.hackrf.library import HackRFError, HackRFLibrary
from .sdr.hackrf.selection_cli import report_selected_device

from .sdr.usrp.config import USRPConfig
from .sdr.usrp.device import USRPDevice
from .sdr.usrp.library import UHDError, UHDLibrary


def make_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flrc rx",
        description="统一 IQ 采样采集：支持 USRP 与 HackRF 后端，采集指定时长并保存为原始 IQ 文件",
    )
    # 后端选择
    parser.add_argument(
        "--sdr",
        choices=["usrp", "hackrf"],
        default=None,
        help="SDR 硬件后端 (默认: 若提供 --args/--mcr 则为 usrp，若提供 --serial 为 hackrf，缺省为 usrp)",
    )
    # 核心通用射频参数
    parser.add_argument("--frequency", type=float, required=True, help="中心载波频率，Hz")
    parser.add_argument(
        "--sample-rate",
        type=float,
        default=None,
        help="采样率，Sps (USRP 默认 2.5MSps；HackRF 默认 10MSps 或 2MSps)",
    )
    parser.add_argument("--gain", type=float, default=None, help="通用接收增益，dB")
    parser.add_argument("--duration", type=float, required=True, help="采集时长，秒")
    parser.add_argument("--output", type=Path, required=True, help="输出 IQ 文件路径 (如 outputs/rx.cs8)")
    parser.add_argument("--iq-format", choices=("cs8", "cu8", "cf32"), default="cs8", help="输出采样格式 (默认 cs8)")
    parser.add_argument("--ui-events", choices=("ndjson",), default=None, help="启用机器事件流输出 (NDJSON)")

    # USRP 专用选项
    usrp_group = parser.add_argument_group("USRP 选项")
    usrp_group.add_argument("--args", default=None, help="UHD 设备定位参数，如 'addr=192.168.10.2'")
    usrp_group.add_argument("--master-clock-rate", "--mcr", type=float, help="主时钟频率 MCR (N310: 125e6, 122.88e6, 153.6e6)")
    usrp_group.add_argument("--channel", type=int, default=0, help="USRP 物理通道索引 (默认 0)")
    usrp_group.add_argument("--antenna", type=str, help="天线端口选择，如 'RX2' 或 'TX/RX'")
    usrp_group.add_argument("--bandwidth-hz", type=float, help="模拟前端滤波器带宽，Hz")
    usrp_group.add_argument("--clock-source", choices=("internal", "external", "gpsdo"), help="参考时钟源")
    usrp_group.add_argument("--time-source", choices=("internal", "external", "gpsdo", "sfp0"), help="时间同步源")
    usrp_group.add_argument("--dll", type=Path, help="自定义 uhd.dll / libhackrf.dll 路径")

    # HackRF 专用选项
    hackrf_group = parser.add_argument_group("HackRF 选项")
    hackrf_group.add_argument("--serial", help="HackRF 设备序列号")
    hackrf_group.add_argument("--device-index", type=int, help="HackRF 设备索引")
    hackrf_group.add_argument("--lna-gain", type=int, default=16, help="HackRF LNA 增益 (0..40 dB, 默认 16)")
    hackrf_group.add_argument("--vga-gain", type=int, default=16, help="HackRF VGA 增益 (0..62 dB, 默认 16)")
    hackrf_group.add_argument("--baseband-bandwidth-hz", type=int, choices=BASEBAND_BANDWIDTHS, help="HackRF 模拟基带带宽")
    hackrf_group.add_argument("--amp", action="store_true", help="启用 HackRF 射频放大器 (+14 dB)")
    hackrf_group.add_argument("--bias-tee", action="store_true", help="启用天线口供电")
    hackrf_group.add_argument("--queue-depth", type=int, default=8, help="缓冲区深度")
    hackrf_group.add_argument("--block-bytes", type=int, help="每次传输字节数")
    hackrf_group.add_argument("--timeout", type=float, help="采集超时时间，秒")
    hackrf_group.add_argument("--allow-device-shortfalls", action="store_true", help="允许固件欠载/溢出时保留统计")

    return parser


def resolve_sdr_backend(args) -> str:
    """自动推断或返回选定的 SDR 后端。"""
    if args.sdr:
        return args.sdr.lower()
    if args.serial is not None or args.device_index is not None or args.amp or args.bias_tee:
        return "hackrf"
    if args.args is not None or args.master_clock_rate is not None or (args.antenna and "RX" in args.antenna):
        return "usrp"
    return "usrp"


def main(argv=None) -> int:
    parser = make_argument_parser()
    args = parser.parse_args(argv)
    sdr = resolve_sdr_backend(args)
    ui_mode = getattr(args, "ui_events", None) == "ndjson"
    emitter = get_emitter(enabled=ui_mode)
    emitter.emit("task.state", payload={"state": "RUNNING", "command": "rx", "sdr": sdr})

    if not isfinite(args.duration) or args.duration <= 0:
        err_msg = "duration 必须为有限正数"
        emitter.emit("task.state", payload={"state": "FAILED", "error": err_msg})
        print(f"错误: {err_msg}", file=sys.stderr)
        return 2

    try:
        if sdr == "usrp":
            with emitter.stage("device_init", {"sdr": "usrp"}):
                library = UHDLibrary(args.dll)
                device_args = args.args if args.args is not None else "addr=192.168.10.2"
                sample_rate = args.sample_rate if args.sample_rate is not None else 2_500_000.0
                gain = args.gain if args.gain is not None else 30.0

                config = USRPConfig(
                    frequency_hz=args.frequency,
                    sample_rate=sample_rate,
                    gain=gain,
                    channel=args.channel,
                    antenna=args.antenna,
                    bandwidth_hz=args.bandwidth_hz,
                    device_args=device_args,
                    clock_source=args.clock_source,
                    time_source=args.time_source,
                    master_clock_rate=args.master_clock_rate,
                    cpu_format=args.iq_format,
                )
                sample_count = ceil(args.duration * config.sample_rate)
                args.output.parent.mkdir(parents=True, exist_ok=True)

            with emitter.stage("stream_capture", {"sample_count": sample_count, "duration": args.duration}):
                with args.output.open("xb", buffering=0) as sink:
                    with USRPDevice(library, device_args=device_args) as dev:
                        stats = dev.receive(sink, config, sample_count)
                        emitter.metric("stream_capture", stats.to_dict())

            emitter.artifact("stream_capture", args.output, "captured_iq", {
                "format": args.iq_format,
                "sample_count": stats.sample_count,
                "overflow_count": stats.overflow_count,
            })
            emitter.task_completed(True, **stats.to_dict())
            if not ui_mode:
                print(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2))
            return 0

        elif sdr == "hackrf":
            with emitter.stage("device_init", {"sdr": "hackrf"}):
                sample_rate = args.sample_rate if args.sample_rate is not None else 10_000_000.0
                lna = args.lna_gain
                vga = args.vga_gain
                if args.gain is not None:
                    lna = min(40, max(0, int(round(args.gain / 2) // 8 * 8)))
                    vga = min(62, max(0, int(round(args.gain - lna) // 2 * 2)))

                config = HackRFConfig(
                    frequency_hz=round(args.frequency),
                    sample_rate=sample_rate,
                    bandwidth_hz=args.baseband_bandwidth_hz,
                    amp=args.amp,
                    bias_tee=args.bias_tee,
                    lna_gain=lna,
                    vga_gain=vga,
                )
                options = {
                    "queue_depth": args.queue_depth,
                    "block_bytes": args.block_bytes,
                    "allow_device_shortfalls": args.allow_device_shortfalls,
                    "timeout": args.timeout if args.timeout is not None else args.duration + 10,
                }
                sample_count = ceil(args.duration * config.sample_rate)
                args.output.parent.mkdir(parents=True, exist_ok=True)

            with emitter.stage("stream_capture", {"sample_count": sample_count, "duration": args.duration}):
                with args.output.open("xb", buffering=0) as sink:
                    with HackRFDevice(HackRFLibrary(args.dll), args.serial, device_index=args.device_index) as device:
                        report_selected_device(device.selected_device)
                        stats = device.receive(sink, config, sample_count, **options)
                        emitter.metric("stream_capture", stats.to_dict())

            sample_cnt = getattr(stats, "sample_count", getattr(stats, "written_bytes", 0) // 2)
            emitter.artifact("stream_capture", args.output, "captured_iq", {
                "format": "cs8",
                "sample_count": sample_cnt,
            })
            emitter.task_completed(True, **stats.to_dict())
            if not ui_mode:
                print(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2))
            return 0
        else:
            err_msg = f"不支持的 SDR 后端: {sdr}"
            emitter.emit("task.state", payload={"state": "FAILED", "error": err_msg})
            print(f"错误: {err_msg}", file=sys.stderr)
            return 2

    except (UHDError, HackRFError, OSError, ValueError, TypeError) as error:
        emitter.emit("task.state", payload={"state": "FAILED", "error": str(error)})
        print(f"错误: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        emitter.emit("task.state", payload={"state": "CANCELLED"})
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
