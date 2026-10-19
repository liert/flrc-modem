"""HackRF 边接收边解调，沿用离线帧格式和协议插件。"""
import argparse
from contextlib import ExitStack
import json
from math import ceil, isfinite
from pathlib import Path
import sys

from .cli_options import add_demod_options, add_output_options, demod_overrides
from .config import load_config
from .io.output import build_record, format_frame, write_jsonl
from .parsing.api import load_parser
from .realtime import LiveReceiver, RealtimeBacklogError, RealtimeDecodeError
from .sdr.hackrf import HackRFConfig, HackRFError
from .sdr.hackrf.config import BASEBAND_BANDWIDTHS
from .sdr.hackrf.selection_cli import add_device_arguments, report_selected_device


def make_argument_parser():
    parser = argparse.ArgumentParser(description="HackRF 实时 FLRC 解调，仅输出 CRC24 通过的帧")
    add_demod_options(parser, file_format=False)
    add_output_options(parser)
    parser.add_argument("--dll", type=Path)
    add_device_arguments(parser)
    parser.add_argument("--duration", type=float, help="接收时长，秒；0 持续接收至 Ctrl+C")
    parser.add_argument("--rx-queue-depth", type=int, default=32, help="HackRF 原始缓冲数量，默认 32")
    parser.add_argument("--window-queue-depth", type=int, default=8, help="等待解调的窗口数量，默认 8")
    parser.add_argument("--block-bytes", type=int)
    parser.add_argument("--dsp-mode", choices=("process", "thread"), default="process",
                        help="process 独立 DSP 进程（默认）；thread 用于嵌入/诊断")
    parser.add_argument("--health-interval", type=float, default=1.0, help="固件丢样检查间隔，秒")
    parser.add_argument("--allow-device-shortfalls", action="store_true")
    parser.add_argument("--baseband-bandwidth-hz", type=int, choices=BASEBAND_BANDWIDTHS)
    parser.add_argument("--lna-gain", type=int, default=16)
    parser.add_argument("--vga-gain", type=int, default=16)
    parser.add_argument("--amp", action="store_true")
    parser.add_argument("--bias-tee", action="store_true")
    return parser


def main(argv=None):
    args = make_argument_parser().parse_args(argv)
    session = None
    try:
        overrides = demod_overrides(args)
        overrides["iq_format"] = "cs8"
        if args.config is None:
            bit_rate = args.bit_rate if args.bit_rate is not None else 325_000
            if not isfinite(bit_rate) or bit_rate <= 0:
                raise ValueError("bit_rate 必须为有限正数")
            overrides["filter_cutoff"] = bit_rate * 350 / 325
            if args.sample_rate is None:
                center = args.center_frequency if args.center_frequency is not None else 2_480_000_000
                frequencies = args.frequencies or [2_480_060_000]
                span = max(abs(frequency - center) for frequency in frequencies)
                minimum_rate = max(2_000_000, 4 * bit_rate,
                                   2.1 * (span + overrides["filter_cutoff"]))
                # 按 2MSps 向上取整；325kb/s 单信道自动选到 2MSps。
                overrides["sample_rate"] = ceil(minimum_rate / 2_000_000) * 2_000_000
        config = load_config(args.config, overrides)
        if int(config.center_frequency) != config.center_frequency:
            raise ValueError("HackRF 中心频率必须为整数 Hz")
        if args.duration is not None and args.max_seconds is not None:
            raise ValueError("--duration 与 --max-seconds 选择一项即可")
        duration = config.max_seconds if args.duration is None else args.duration
        hardware = HackRFConfig(int(config.center_frequency), sample_rate=config.sample_rate,
                                bandwidth_hz=args.baseband_bandwidth_hz, lna_gain=args.lna_gain,
                                vga_gain=args.vga_gain, amp=args.amp, bias_tee=args.bias_tee)
        parser = load_parser(args.protocol or args.parser_spec)
        with ExitStack() as stack:
            output = None
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                # 每次实时会话使用新文件，避免覆盖旧记录、配置或 DLL。
                output = stack.enter_context(args.output.open("x", encoding="utf-8", newline="\n"))
            session = LiveReceiver(config, hardware, duration=duration, dll_path=args.dll, serial=args.serial,
                                   window_queue_depth=args.window_queue_depth, rx_queue_depth=args.rx_queue_depth,
                                   block_bytes=args.block_bytes, health_interval=args.health_interval,
                                   allow_device_shortfalls=args.allow_device_shortfalls,
                                   dsp_process=args.dsp_mode == "process", device_index=args.device_index,
                                   on_device_open=report_selected_device)
            with session:
                for frame in session.frames():
                    record = build_record(frame, parser)
                    if output is not None:
                        write_jsonl(output, record)
                        output.flush()
                    if not args.quiet:
                        if args.jsonl:
                            write_jsonl(sys.stdout, record)
                            sys.stdout.flush()
                        else:
                            print(format_frame(frame), flush=True)
                            if "application" in record:
                                print("  业务解析: " + json.dumps(record["application"], ensure_ascii=False), flush=True)
                            elif "application_error" in record:
                                print("  业务解析失败: " + record["application_error"], flush=True)
        return 0
    except KeyboardInterrupt:
        print("实时接收已停止，已写出的帧已保留。", file=sys.stderr)
        return 130
    except (HackRFError, RealtimeBacklogError, RealtimeDecodeError, OSError, ValueError, TypeError, ImportError, AttributeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    finally:
        if session is not None:
            print(json.dumps(session.stats.to_dict(), ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
