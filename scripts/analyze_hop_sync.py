"""离线验证连续IQ采样时间上的稀疏跳频时钟估计，不执行射频发送。"""
import argparse
import json
from pathlib import Path

from flrc_modem.hop_clock import HopClockTracker


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=Path, required=True, help="包含dk32s application字段的JSONL")
    parser.add_argument("--sync", required=True)
    parser.add_argument("--keep-every", type=int, default=1, help="每N个地面候选仅保留1个，用于模拟漏解帧")
    parser.add_argument("--end-time", type=float, help="同一采样段内的预测时刻，单位秒")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.keep_every < 1:
        parser.error("keep-every 必须为正整数")
    tracker = HopClockTracker(sync_word=args.sync)
    candidates = omitted = 0
    end_time = 0.0
    with args.frames.open(encoding="utf-8-sig") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            end_time = max(end_time, record["time_s"])
            app = record.get("application", {})
            eligible = (record.get("exchange_class") != "tx_echo" and app.get("direction") == "ground_to_air"
                        and app.get("command") in (0x11, 0x16) and record.get("crc_ok") is True
                        and app.get("sum8_ok") is True and record.get("sync_word_hex", "").lower() == tracker.sync_word)
            if eligible:
                candidates += 1
                if (candidates - 1) % args.keep_every:
                    omitted += 1
                    continue
            tracker.observe_record(record)
    prediction = tracker.predict(end_time if args.end_time is None else args.end_time)
    report = {"clock": tracker.snapshot(), "prediction": prediction.to_dict() if prediction else None,
              "deliberately_omitted_anchors": omitted,
              "note": "离线采样时间模型；未建立采样时间到TX硬件时间的映射，不代表实时发射已同步"}
    if args.output is not None:
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if tracker.locked else 2


if __name__ == "__main__":
    raise SystemExit(main())
