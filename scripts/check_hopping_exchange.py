"""真实双 HackRF DK32S 有限跳频测试；保存原始采样、计划及回传候选。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from flrc_modem.config import DemodConfig
from flrc_modem.encoding.input import read_message
from flrc_modem.exchange import ExchangeCapture, classify_record
from flrc_modem.hopping import prepare_hopping_plan
from flrc_modem.io.output import build_record, write_jsonl
from flrc_modem.modulation import ModulationConfig
from flrc_modem.parsing.api import load_parser
from flrc_modem.pipeline import DecodeStats, iter_frames
from flrc_modem.sdr.hackrf import HackRFConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rf-config", type=Path, required=True, help="get_rf_channels.py --json 的当前查询结果")
    parser.add_argument("--message-file", type=Path, required=True)
    parser.add_argument("--tx-serial", required=True)
    parser.add_argument("--rx-serial", required=True)
    parser.add_argument("--rx-frequency", type=int, required=True, help="固定监听的实际载频，只观察该频点的回传")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--packets-per-slot", type=int, default=3, help="默认3包；过短的突发在旧固件上未完成稳定性验证")
    parser.add_argument("--sample-rate", type=float, default=8000000)
    parser.add_argument("--tx-correction-hz", type=float, default=0, help="TX相对目标的校正；本机实验约-18000Hz，不是通用常数")
    parser.add_argument("--tx-gain", type=int, default=24)
    parser.add_argument("--lna-gain", type=int, default=24)
    parser.add_argument("--vga-gain", type=int, default=24)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report, session, created = {}, None, False
    try:
        radio = json.loads(args.rf_config.read_text(encoding="utf-8-sig"))
        modulation = ModulationConfig(sample_rate=args.sample_rate, amplitude=0.7)
        plan = prepare_hopping_plan(read_message(args.message_file), radio, modulation, cycles=args.cycles,
                                    packets_per_slot=args.packets_per_slot)
        if args.rx_frequency not in [b.frequency_hz for b in plan.bursts]:
            raise ValueError("监听频点不在跳频表中")
        rx = HackRFConfig(args.rx_frequency - 60000, sample_rate=args.sample_rate,
                          bandwidth_hz=1750000, lna_gain=args.lna_gain, vga_gain=args.vga_gain)
        tx = HackRFConfig(round(plan.bursts[0].frequency_hz - 60000 + args.tx_correction_hz),
                          sample_rate=args.sample_rate, tx_gain=args.tx_gain)
        session = ExchangeCapture(tx, rx, tx_serial=args.tx_serial, rx_serial=args.rx_serial,
                                  post_roll=1, timeout=args.cycles + 5)
        args.output_dir.mkdir(parents=True, exist_ok=False)
        created = True
        report.update(radio=radio, cycles=args.cycles, rx_frequency_hz=args.rx_frequency,
                      tx_correction_hz=args.tx_correction_hz, packet_count=plan.packet_count,
                      causal_reply_verified=False, slots=[])
        (args.output_dir / "sent-packets.json").write_text(json.dumps(sorted(plan.sent_packets), indent=2)+"\n", encoding="utf-8")
        print(f"预生成 {len(plan.bursts)} 个时隙、{plan.packet_count} 包；RX固定监听 {args.rx_frequency} Hz", flush=True)
        def transmit(device, cancel):
            return plan.transmit(device, cancel, tx_gain=args.tx_gain, correction_hz=args.tx_correction_hz,
                                 slot_stats=report["slots"])
        session.run(None, args.output_dir / "capture.cs8", transmit_operation=transmit)
        config = DemodConfig(sample_rate=rx.sample_rate, center_frequency=rx.frequency_hz,
                             frequencies=(args.rx_frequency,), iq_format="cs8")
        stats, counts = DecodeStats(), Counter()
        frame_parser = load_parser("dk32s")
        with (args.output_dir / "frames.jsonl").open("x", encoding="utf-8") as output:
            for frame in iter_frames(args.output_dir / "capture.cs8", config, stats):
                row = build_record(frame, frame_parser)
                row["exchange_class"] = classify_record(row, plan.sent_packets)
                counts[row["exchange_class"]] += 1
                write_jsonl(output, row)
        report.update(counts=dict(counts), decode=vars(stats),
                      note="仅采集一个频点；回传候选需结合遥控器是否关闭、前后对照和包间时序核实来源")
        print(json.dumps({"output_dir": str(args.output_dir.resolve()), "counts": dict(counts),
                          "planned_slots": len(plan.bursts),
                          "skipped_slots": sum(s["status"] == "skipped_late" for s in report["slots"]),
                          "max_late_ms": max(s["late_ms"] for s in report["slots"]),
                          "capture_status": session.stats["status"]}, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        report["error"] = "用户取消"
        return 130
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        report["error"] = f"{type(error).__name__}: {error}"
        print(report["error"], file=sys.stderr)
        return 2
    finally:
        if session is not None:
            report["capture"] = session.stats
        if created:
            (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
