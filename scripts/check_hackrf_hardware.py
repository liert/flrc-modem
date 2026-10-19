"""真实设备验证；默认 RX，显式 --tx 才执行低增益短时 TX 和取消测试。"""
import argparse
from datetime import datetime, timezone
import io
import json
from math import isfinite
from pathlib import Path
import threading

from flrc_demod.sdr.hackrf import HackRFConfig, HackRFDevice, HackRFLibrary, StreamCancelled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--serial")
    parser.add_argument("--frequency", type=int, default=2_480_000_000)
    parser.add_argument("--duration", type=float, default=0.25)
    parser.add_argument("--rates", type=int, nargs="+", default=[2_000_000, 10_000_000, 20_000_000])
    parser.add_argument("--tx", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not isfinite(args.duration) or args.duration <= 0:
        parser.error("duration 必须为有限正数")
    now = datetime.now(timezone.utc)
    output = args.output or Path("outputs") / ("hackrf-check-" + now.strftime("%Y%m%d-%H%M%S-%f"))
    output.mkdir(parents=True, exist_ok=False)
    report = {"started_utc": now.isoformat(), "frequency_hz": args.frequency,
              "tx_enabled": args.tx, "tx_gain": 0, "amp": False, "cases": []}
    def save():
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    def record(name, stats, expected_bytes=None):
        entry = {"name": name, **stats.to_dict()}
        if expected_bytes is not None:
            actual = stats.source_bytes if stats.mode == "tx" else stats.written_bytes
            if actual != expected_bytes:
                raise RuntimeError(f"{name} 字节数错误：{actual} != {expected_bytes}")
        state = stats.device_state
        entry["success"] = bool(state is not None and not state["num_shortfalls"]
                                 and not state["error"] and not state["active_mode"])
        if stats.mode == "tx" and not stats.flush_complete:
            raise RuntimeError(f"{name} 未完成 flush")
        report["cases"].append(entry)
        save()
        print(json.dumps(entry, ensure_ascii=False))
    try:
        api = HackRFLibrary(args.dll)
        report["library"] = api.info()
        with HackRFDevice(api, args.serial) as device:
            report["device"] = device.info()
            # 同一个句柄连续切换 RX/TX，验证回调注销和停止后的重用。
            for rate in args.rates:
                config = HackRFConfig(args.frequency, sample_rate=rate)
                count = round(rate * args.duration)
                with (output / f"rx-{rate}.cs8").open("xb", buffering=0) as sink:
                    record(f"rx-{rate}", device.receive(sink, config, count, timeout=args.duration + 5,
                                                       allow_device_shortfalls=True), count * 2)
                if args.tx:
                    # 全零 IQ 用于 USB 吞吐/固件欠载测试，不混入设备业务帧。
                    data = bytes(count * 2 + 2)
                    record(f"tx-{rate}", device.transmit(io.BytesIO(data), config, timeout=args.duration + 5,
                                                        allow_device_shortfalls=True), len(data))
            config = HackRFConfig(args.frequency, sample_rate=10_000_000)
            if args.tx:
                for count in (2, 510, 512, 514, 262142, 262144, 262146, 1048578):
                    record(f"tx-tail-{count}", device.transmit(io.BytesIO(bytes(count)), config, timeout=5), count)
            # 正常采集在所有 TX 之后仍应工作。
            with (output / "rx-after-tx.cs8").open("xb", buffering=0) as sink:
                record("rx-after-tx", device.receive(sink, config, 500_000, timeout=5), 1_000_000)
            for mode in (("rx", "tx") if args.tx else ("rx",)):
                cancel = threading.Event()
                timer = threading.Timer(0.05, cancel.set)
                timer.start()
                try:
                    try:
                        if mode == "tx":
                            device.transmit(io.BytesIO(bytes(20_000_000)), config, cancel_event=cancel, timeout=5)
                        else:
                            with (output / "rx-cancelled.cs8").open("xb", buffering=0) as sink:
                                device.receive(sink, config, 10_000_000, cancel_event=cancel, timeout=5)
                        raise RuntimeError(f"{mode} 未按预期被取消")
                    except StreamCancelled:
                        state = device.state()
                        if state["active_mode"]:
                            raise RuntimeError(f"取消 {mode} 后固件仍在运行：{state}")
                        report["cases"].append({"name": f"{mode}-cancel", "cancelled": True, "success": True,
                                                "stats": device.last_stats.to_dict(), "device_state": state})
                        save()
                finally:
                    timer.cancel()
        report["success"] = all(case["success"] for case in report["cases"])
        return 0 if report["success"] else 2
    except Exception as error:
        report["success"], report["error"] = False, repr(error)
        print(f"硬件验证失败：{error}")
        return 2
    finally:
        save()
        print(f"报告：{output / 'report.json'}")


if __name__ == "__main__":
    raise SystemExit(main())
