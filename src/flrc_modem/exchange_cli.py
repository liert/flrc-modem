"""先接收、后有限发送、保留尾部采样并离线解调的双设备诊断命令。"""
import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile

from .config import DemodConfig
from .encoding import load_encoder
from .encoding.input import read_message
from .exchange import ExchangeCapture, classify_record
from .io.output import build_record, write_jsonl
from .modulation import ModulationConfig
from .parsing.api import load_parser
from .phy.profile import CODING_RATES, FLRC_RATE_PROFILES, parse_bt
from .pipeline import DecodeStats, iter_frames
from .sdr.hackrf import HackRFConfig, IQFileSource
from .sdr.hackrf.config import BASEBAND_BANDWIDTHS
from .transmission.source import PacketSource


def make_argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tx-serial", required=True)
    parser.add_argument("--rx-serial", required=True)
    parser.add_argument("--frequency", type=int, required=True, help="TX 中心频率，Hz")
    parser.add_argument("--rx-center-frequency", type=int, help="RX 中心频率，默认同 TX")
    parser.add_argument("--rx-frequency", type=float, action="append", help="RX 解调载频，可重复；默认 TX 实际载频")
    parser.add_argument("--offset-hz", type=float, default=0)
    parser.add_argument("--sample-rate", type=float, default=8000000)
    parser.add_argument("--rx-sample-rate", type=float, help="RX 采样率，默认同 TX")
    parser.add_argument("--bit-rate", type=float, choices=tuple(FLRC_RATE_PROFILES), default=325000)
    parser.add_argument("--cr", dest="coding_rate", choices=CODING_RATES, default="3/4")
    parser.add_argument("--bt", type=parse_bt, default=1)
    parser.add_argument("--sync", required=True, help="4B 同步字，十六进制")
    parser.add_argument("--rx-sync", help="可选回复同步字；默认盲恢复")
    parser.add_argument("--crc-initial", type=lambda v: int(v, 0), default=0xFFFFFF)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload-hex")
    source.add_argument("--message-file", type=Path)
    parser.add_argument("--protocol", default="dk32s", help="业务解析器；模板同时使用该编码器")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--gap-ms", type=float, default=10)
    parser.add_argument("--amplitude", type=float, default=0.25)
    parser.add_argument("--tx-gain", type=int, default=0)
    parser.add_argument("--lna-gain", type=int, default=16)
    parser.add_argument("--vga-gain", type=int, default=16)
    parser.add_argument("--rx-bandwidth-hz", type=int, choices=BASEBAND_BANDWIDTHS)
    parser.add_argument("--tx-bandwidth-hz", type=int, choices=BASEBAND_BANDWIDTHS)
    parser.add_argument("--pre-roll", type=float, default=0.2)
    parser.add_argument("--post-roll", type=float, default=0.3)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output-dir", type=Path, required=True, help="新建结果目录，拒绝覆盖")
    parser.add_argument("--dll", type=Path)
    return parser


def prepare_source(args, modulation, stream):
    """射频启动前完成调制和量化，降低同时运行两路 USB 时的 Python 负载。"""
    source = PacketSource(modulation, bytes.fromhex(args.sync), args.crc_initial)
    if args.message_file is not None:
        source.submit_message(read_message(args.message_file), load_encoder(args.protocol), repeat=args.repeat)
    else:
        source.submit(bytes.fromhex(args.payload_hex), repeat=args.repeat)
    source.finish()
    block = bytearray(262144)
    size = 0
    while count := source.readinto(memoryview(block)):
        size += count
        if size > args.timeout * modulation.sample_rate * 2:
            raise ValueError("发送波形时长超过 timeout，请减少 repeat/gap 或增加 timeout")
        stream.write(memoryview(block)[:count])
    stream.flush()
    stream.seek(0)
    return size


def main(argv=None):
    args = make_argument_parser().parse_args(argv)
    session = None
    report = {"capture": None, "decode": None, "counts": {}, "causal_reply_verified": False}
    directory_created = False
    try:
        modulation = ModulationConfig(sample_rate=args.sample_rate, bit_rate=args.bit_rate,
                                      offset_hz=args.offset_hz, coding_rate=args.coding_rate,
                                      bt=args.bt, amplitude=args.amplitude, gap_seconds=args.gap_ms / 1000)
        if args.repeat < 1:
            raise ValueError("exchange 只接受有限正整数 repeat")
        tx = HackRFConfig(args.frequency, sample_rate=args.sample_rate, tx_gain=args.tx_gain,
                          bandwidth_hz=args.tx_bandwidth_hz)
        rx = HackRFConfig(args.rx_center_frequency or args.frequency,
                          sample_rate=args.rx_sample_rate or args.sample_rate,
                          lna_gain=args.lna_gain, vga_gain=args.vga_gain,
                          bandwidth_hz=args.rx_bandwidth_hz)
        config = DemodConfig(sample_rate=rx.sample_rate, center_frequency=rx.frequency_hz,
                             frequencies=tuple(args.rx_frequency or [tx.frequency_hz + args.offset_hz]),
                             bit_rate=args.bit_rate, coding_rate=args.coding_rate, bt=args.bt,
                             filter_cutoff=args.bit_rate * 350 / 325, iq_format="cs8",
                             crc_initial=args.crc_initial,
                             sync_word=bytes.fromhex(args.rx_sync) if args.rx_sync else None)
        parser = load_parser(args.protocol)
        session = ExchangeCapture(tx, rx, tx_serial=args.tx_serial, rx_serial=args.rx_serial,
                                  pre_roll=args.pre_roll, post_roll=args.post_roll,
                                  timeout=args.timeout, dll_path=args.dll)
        args.output_dir.mkdir(parents=True, exist_ok=False)
        directory_created = True
        # 临时 TX 文件仅用于降低运行期负载，诊断原始 RX 文件永久保留。
        with tempfile.TemporaryDirectory(prefix="flrc-exchange-") as temporary:
            tx_path = Path(temporary) / "tx.cs8"
            with tx_path.open("w+b") as stream:
                tx_bytes = prepare_source(args, modulation, stream)
            # 从实际生成的 TX 波形回读，记录模板逐包更新后的全部 payload。
            tx_demod = DemodConfig(sample_rate=args.sample_rate, center_frequency=args.frequency,
                                   frequencies=(args.frequency + args.offset_hz,), bit_rate=args.bit_rate,
                                   coding_rate=args.coding_rate, bt=args.bt, crc_initial=args.crc_initial,
                                   filter_cutoff=args.bit_rate * 350 / 325, iq_format="cs8")
            sent_packets = set()
            generated = 0
            with (args.output_dir / "sent.jsonl").open("x", encoding="utf-8") as output:
                for frame in iter_frames(tx_path, tx_demod):
                    record = build_record(frame, parser)
                    sent_packets.add((record["sync_word_hex"], record["payload_hex"]))
                    generated += 1
                    write_jsonl(output, record)
            if generated != args.repeat:
                raise ValueError(f"发送前波形自检失败：{generated}/{args.repeat} 帧")
            demod_values = asdict(config)
            demod_values["sync_word"] = config.sync_word.hex() if config.sync_word else None
            report.update(modulation=asdict(modulation), demod=demod_values,
                          tx_bytes=tx_bytes, generated_frames=generated)
            print(f"RX={args.rx_serial} 提前采集；TX={args.tx_serial}；实际载频="
                  f"{args.frequency + args.offset_hz:g} Hz；{generated} 帧", file=sys.stderr, flush=True)
            with IQFileSource(tx_path) as source:
                session.run(source, args.output_dir / "capture.cs8")
        stats, counts = DecodeStats(), Counter()
        with (args.output_dir / "frames.jsonl").open("x", encoding="utf-8") as output:
            for frame in iter_frames(args.output_dir / "capture.cs8", config, stats):
                record = build_record(frame, parser)
                record["exchange_class"] = classify_record(record, sent_packets,
                                                            (args.rx_sync.lower(),) if args.rx_sync else ())
                counts[record["exchange_class"]] += 1
                write_jsonl(output, record)
        report.update(decode=asdict(stats), counts=dict(counts),
                      link_verified=counts["tx_echo"] > 0,
                      note="天空回传仅为候选；遥控器同时工作时不能仅凭 ACK/序号/邻近时间证明由本次发送触发")
        return 0
    except KeyboardInterrupt:
        report["error"] = "用户取消"
        return 130
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        report["error"] = f"{type(error).__name__}: {error}"
        print(report["error"], file=sys.stderr)
        return 2
    finally:
        if session is not None:
            report["capture"] = session.stats
        if directory_created:
            with (args.output_dir / "report.json").open("x", encoding="utf-8") as output:
                json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    raise SystemExit(main())
