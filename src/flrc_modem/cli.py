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
from .io.ui_events import get_emitter
from .parsing.api import load_parser
from .pipeline import DecodeStats, iter_frames
from .cli_options import add_demod_options, add_output_options, demod_overrides


def make_argument_parser():
    parser = argparse.ArgumentParser(
        description="离线 FLRC 解调，仅输出硬件 CRC 校验成功的原始物理载荷",
        epilog="生成信号：python -m flrc_modem modulate --help，或 flrc modulate --help",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--iq", type=Path, required=True, help="交错 I/Q 文件")
    add_demod_options(parser)
    add_output_options(parser)
    return parser


def main(argv=None) -> int:
    argument_parser = make_argument_parser()
    args = argument_parser.parse_args(argv)
    overrides = demod_overrides(args)
    ui_mode = getattr(args, "ui_events", None) == "ndjson"
    emitter = get_emitter(enabled=ui_mode)
    emitter.emit("task.state", payload={"state": "RUNNING", "command": "demod"})

    try:
        with emitter.stage("config", {"iq": str(args.iq)}):
            config = load_config(args.config, overrides)
            payload_parser = load_parser(args.protocol or args.parser_spec)
            if args.output is not None and args.output.resolve() in {
                args.iq.resolve(), args.config.resolve() if args.config is not None else None,
            }:
                raise ValueError("输出文件不能覆盖 IQ 输入或配置文件")

        stats = DecodeStats()
        started = time.perf_counter()

        with emitter.stage("demod_pipeline", {"sample_rate": config.sample_rate, "frequencies": list(config.frequencies)}):
            with ExitStack() as stack:
                output = None
                if args.output is not None:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    output = stack.enter_context(args.output.open("w", encoding="utf-8", newline="\n"))

                file_size = args.iq.stat().st_size if args.iq.exists() else 0
                sample_bytes = 2 if config.iq_format in ("cs8", "cu8") else 8
                total_samples = file_size // sample_bytes if sample_bytes else 0

                for frame in iter_frames(args.iq, config, stats):
                    record = build_record(frame, payload_parser)
                    if output is not None:
                        write_jsonl(output, record)

                    if ui_mode:
                        emitter.metric("demod_pipeline", {
                            "time_s": frame.time_s,
                            "cfo_hz": frame.cfo_hz,
                            "sample_power_db": frame.sample_power_db,
                            "sync_word_hex": frame.packet.sync_word.hex(),
                            "payload_hex": frame.packet.payload.hex(),
                            "payload_length": len(frame.packet.payload),
                            "decoded_frames": stats.decoded_frames,
                            "candidate_groups": stats.candidate_groups,
                            "windows": stats.windows,
                        })
                    elif not args.quiet:
                        if args.jsonl:
                            write_jsonl(sys.stdout, record)
                        else:
                            print(format_frame(frame))
                            if "application" in record:
                                print("  业务解析: " + json.dumps(record["application"], ensure_ascii=False))
                            elif "application_error" in record:
                                print("  业务解析失败: " + record["application_error"])

        elapsed = round(time.perf_counter() - started, 3)
        summary = {**asdict(stats), "elapsed_seconds": elapsed}

        if args.output is not None and args.output.exists():
            emitter.artifact("output", args.output, "frames_jsonl", {
                "size_bytes": args.output.stat().st_size,
                "decoded_frames": stats.decoded_frames,
            })

        emitter.task_completed(True, **summary)
        print(json.dumps(summary, ensure_ascii=False), file=sys.stderr)
        return 0
    except (OSError, ValueError, TypeError, ImportError, AttributeError) as error:
        emitter.emit("task.state", payload={"state": "FAILED", "error": str(error)})
        print(f"错误：{error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        emitter.emit("task.state", payload={"state": "CANCELLED"})
        print("已中止，已经写出的 JSONL 帧仍可使用。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
