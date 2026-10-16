"""命令行入口只负责参数、输出和退出状态，DSP 实现在独立模块。"""
import argparse
from contextlib import ExitStack
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

from . import __version__
from .config import load_config
from .io.output import build_record, format_frame, write_jsonl
from .parsing.api import load_parser
from .parsing.protocols import BUILTIN_PARSERS
from .pipeline import DecodeStats, iter_frames
from .phy.profile import CODING_RATES, parse_bt


def make_argument_parser():
    parser = argparse.ArgumentParser(
        description="离线 FLRC 解调，仅输出 CRC24 校验成功的原始物理载荷",
        epilog="生成信号：python -m flrc_demod modulate --help，或 flrc-modulate --help",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--iq", type=Path, required=True, help="交错 I/Q 文件")
    parser.add_argument("--config", type=Path, help="可选 TOML 物理层参数文件")
    parser.add_argument("--sample-rate", type=float, help="输入采样率，Hz")
    parser.add_argument("--center-frequency", type=float, help="采集中心频率，Hz")
    parser.add_argument("--frequency", dest="frequencies", type=float, action="append", help="目标实际载频，Hz；可重复")
    parser.add_argument("--bit-rate", type=float, help="FLRC 原始比特率，bit/s")
    parser.add_argument("--cr", dest="coding_rate", choices=CODING_RATES, help="编码率 1/2、3/4 或 1；默认 3/4")
    parser.add_argument("--bt", type=parse_bt, help="no、0.5 或 1；用于前导搜索门限，默认 1")
    parser.add_argument("--iq-format", choices=("cs8", "cu8", "cf32"), help="cs8 有符号8位 / cu8 无符号8位 / cf32 小端浮点")
    parser.add_argument("--chunk-seconds", type=float, help="处理窗口步进，秒")
    parser.add_argument("--overlap-ms", type=float, help="窗口重叠，毫秒")
    parser.add_argument("--max-seconds", type=float, help="最多处理秒数，0 表示全部")
    parser.add_argument("--sync", dest="sync_word", help="可选 4B 同步字十六进制，按芯片寄存器字节顺序")
    parser.add_argument("--crc-initial", type=lambda s: int(s, 0), help="CRC24 初值，例如 0xFFFFFF")
    parser.add_argument("--output", type=Path, help="成功帧 JSONL 输出文件")
    parser.add_argument("--jsonl", action="store_true", help="stdout 只输出 JSONL；统计写入 stderr")
    parser.add_argument("--quiet", action="store_true", help="不打印逐帧结果；可配合 --output")
    parsing = parser.add_mutually_exclusive_group()
    parsing.add_argument("--protocol", choices=tuple(BUILTIN_PARSERS), help="内置协议；默认只输出原始物理载荷")
    parsing.add_argument("--parser", dest="parser_spec", help="可选业务插件：Python模块:函数名")
    return parser


def main(argv=None) -> int:
    argument_parser = make_argument_parser()
    args = argument_parser.parse_args(argv)
    overrides = {name: getattr(args, name) for name in (
        "sample_rate", "center_frequency", "frequencies", "bit_rate", "iq_format",
        "chunk_seconds", "max_seconds", "sync_word", "crc_initial", "coding_rate", "bt",
    )}
    if args.overlap_ms is not None:
        overrides["overlap_seconds"] = args.overlap_ms / 1000
    try:
        config = load_config(args.config, overrides)
        payload_parser = load_parser(args.protocol or args.parser_spec)
        if args.output is not None and args.output.resolve() in {
            args.iq.resolve(), args.config.resolve() if args.config is not None else None,
        }:
            raise ValueError("输出文件不能覆盖 IQ 输入或配置文件")
        stats = DecodeStats()
        started = time.perf_counter()
        with ExitStack() as stack:
            output = None
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                output = stack.enter_context(args.output.open("w", encoding="utf-8", newline="\n"))
            for frame in iter_frames(args.iq, config, stats):
                record = build_record(frame, payload_parser)
                if output is not None:
                    write_jsonl(output, record)
                if not args.quiet:
                    if args.jsonl:
                        write_jsonl(sys.stdout, record)
                    else:
                        print(format_frame(frame))
                        if "application" in record:
                            print("  业务解析: " + json.dumps(record["application"], ensure_ascii=False))
                        elif "application_error" in record:
                            print("  业务解析失败: " + record["application_error"])
        print(json.dumps({**asdict(stats), "elapsed_seconds": round(time.perf_counter() - started, 3)},
                         ensure_ascii=False), file=sys.stderr)
        return 0
    except (OSError, ValueError, TypeError, ImportError, AttributeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中止，已经写出的 JSONL 帧仍可使用。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
