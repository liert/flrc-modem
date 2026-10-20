"""原始载荷到 IQ 的命令行入口，不依赖厂商协议模块。"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from .io.iq import write_iq
from .modulation import ModulationConfig, iter_signal
from .phy.encoder import encode_packet
from .phy.profile import CODING_RATES, FLRC_RATE_PROFILES, RAMP_TIME_PARAMS, parse_bt


def make_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将原始 FLRC 物理载荷离线调制成 IQ 文件")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload-hex", help="完整原始物理载荷的十六进制字节")
    source.add_argument("--payload-file", type=Path, help="原始载荷二进制文件，6..127B")
    parser.add_argument("--sync", required=True, help="4B 同步字，例如 b42b2bb4")
    parser.add_argument("--crc-initial", type=lambda value: int(value, 0), default=0xFFFFFF)
    parser.add_argument("--output", type=Path, required=True, help="输出 IQ 文件")
    parser.add_argument("--metadata", type=Path, help="元数据 JSON，默认 <IQ文件名>.json")
    parser.add_argument("--sample-rate", type=float, default=10_000_000)
    parser.add_argument("--bit-rate", type=float, choices=tuple(FLRC_RATE_PROFILES), default=325_000,
                        help="SX1280 FLRC 空口码率，bit/s；自动选择绑定的 BW")
    parser.add_argument("--bandwidth-hz", "--bandwidth", dest="bandwidth_hz", type=float,
                        help="可选芯片标称双边 BW，Hz；必须匹配所选 bitrate")
    parser.add_argument("--ramp-time-us", type=float, choices=tuple(RAMP_TIME_PARAMS), default=12,
                        help="IQ 幅度爬升及下降时间，µs；默认 12")
    parser.add_argument("--offset-hz", type=float, default=0, help="相对 IQ 中心的频率偏移，Hz")
    parser.add_argument("--cr", dest="coding_rate", choices=CODING_RATES, default="3/4", help="编码率，默认 3/4")
    parser.add_argument("--bt", type=parse_bt, default=1.0, help="0 关闭高斯成形，或 0.5、1")
    parser.add_argument("--amplitude", type=float, default=0.7, help="归一化幅度，(0, 1]")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--gap-ms", type=float, default=10, help="相邻突发间的静默时间，ms")
    parser.add_argument("--guard-ms", type=float, default=10, help="文件首尾各自的静默时间，ms")
    parser.add_argument("--iq-format", choices=("cs8", "cu8", "cf32"), default="cs8")
    parser.add_argument("--verify", action="store_true", help="落盘后回读解调，逐帧检查载荷和 CRC")
    return parser


def verify_signal(path: Path, config: ModulationConfig, packet, crc_initial: int = 0xFFFFFF) -> int:
    """复用接收链路回读，要求帧数、同步字、硬件头、载荷和 CRC 全部一致。"""
    from .config import DemodConfig
    from .pipeline import iter_frames
    demod = DemodConfig(
        sample_rate=config.sample_rate, center_frequency=2_480_000_000,
        frequencies=(2_480_000_000 + config.offset_hz,), bit_rate=config.bit_rate,
        iq_format=config.iq_format, filter_cutoff=config.bit_rate * 350 / 325,
        crc_initial=crc_initial,
        coding_rate=config.coding_rate, bt=config.bt,
    )
    count = 0
    for frame in iter_frames(path, demod):
        received = frame.packet
        if (received.sync_word, received.header, received.payload, received.crc) != (
            packet.sync_word, packet.header, packet.payload, packet.crc,
        ):
            raise ValueError("回读出现与输入不同的物理帧")
        count += 1
    if count != config.repeat:
        raise ValueError(f"回读失败：期望 {config.repeat} 帧，实际 {count} 帧；请检查参数及突发间隙")
    return count


def main(argv=None) -> int:
    args = make_argument_parser().parse_args(argv)
    try:
        config = ModulationConfig(
            sample_rate=args.sample_rate, bit_rate=args.bit_rate, offset_hz=args.offset_hz,
            bandwidth_hz=args.bandwidth_hz, ramp_time_us=args.ramp_time_us,
            bt=args.bt, coding_rate=args.coding_rate, amplitude=args.amplitude, repeat=args.repeat,
            gap_seconds=args.gap_ms / 1000, guard_seconds=args.guard_ms / 1000,
            iq_format=args.iq_format,
        )
        metadata_path = args.metadata or Path(str(args.output) + ".json")
        paths = [args.output.resolve(), metadata_path.resolve()]
        if len(set(paths)) != 2:
            raise ValueError("IQ 输出和元数据输出不能是同一个文件")
        if args.payload_file is not None and args.payload_file.resolve() in paths:
            raise ValueError("输出不能覆盖载荷输入文件")
        if args.payload_file is not None:
            # 限长读取，避免误把大体积 IQ 当载荷读入内存。
            with args.payload_file.open("rb") as stream:
                payload = stream.read(128)
        else:
            payload = bytes.fromhex(args.payload_hex)
        packet = encode_packet(payload, bytes.fromhex(args.sync), args.crc_initial, config.coding_rate)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        count = write_iq(args.output, iter_signal(packet.bits, config), config.iq_format)
        metadata = {
            "schema_version": 1, "kind": "flrc_generated_iq",
            "config": asdict(config), "modulation_index": 0.5,
            "deviation_hz": config.deviation_hz,
            "sx1280_bit_rate_bandwidth_param": f"0x{FLRC_RATE_PROFILES[config.bit_rate][1]:02x}",
            "sx1280_ramp_time_param": f"0x{RAMP_TIME_PARAMS[config.ramp_time_us]:02x}",
            "ramp_samples": config.ramp_samples,
            "effective_ramp_time_us": config.ramp_samples / config.sample_rate * 1_000_000,
            "preamble_offset_samples": config.ramp_samples,
            "envelope_model": "linear",
            "sample_count": count, "duration_s": count / config.sample_rate,
            "sync_word_hex": packet.sync_word.hex(), "hardware_header_hex": packet.header.hex(),
            "payload_hex": packet.payload.hex(), "crc_hex": packet.crc.hex(),
            "crc_initial": args.crc_initial, "bits_per_packet": len(packet.bits),
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已生成 {args.output}：{count} 个复数样本，{count / config.sample_rate:.6f}s，{config.iq_format}")
        print(f"元数据：{metadata_path}")
        if args.verify:
            verified = verify_signal(args.output, config, packet, args.crc_initial)
            print(f"回读成功：{verified}/{config.repeat} 帧，载荷与 CRC24 一致")
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
