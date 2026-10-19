"""真实设备不落盘发送复测；仅在显式 --tx 时启动射频。"""
import argparse
import json
from pathlib import Path
import time

from flrc_demod.modulation import ModulationConfig
from flrc_demod.sdr.hackrf import HackRFConfig
from flrc_demod.sdr.hackrf.selection_cli import add_device_arguments
from flrc_demod.transmission import LiveTransmitter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tx", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs/transmit-hardware.json"))
    add_device_arguments(parser)
    args = parser.parse_args()
    modulation = ModulationConfig(sample_rate=2_000_000)
    hardware = HackRFConfig(2_480_000_000, sample_rate=modulation.sample_rate, tx_gain=0)
    report = {"frequency_hz": hardware.frequency_hz, "bit_rate": modulation.bit_rate, "cases": {}}
    # 使用 TEST 同步字和自定义载荷，不发送录制的业务控制报文。
    selection = {"serial": args.serial, "device_index": args.device_index}
    finite = LiveTransmitter(modulation, hardware, b"TEST", **selection)
    finite.submit(bytes(range(6)), repeat=3)
    finite.finish()
    with finite:
        finite.wait()
    report["cases"]["finite"] = finite.stats.to_dict()
    dynamic = LiveTransmitter(modulation, hardware, b"TEST", **selection)
    dynamic.submit(bytes(range(6)))
    with dynamic:
        time.sleep(0.8)
        dynamic.submit(bytes(range(20)), sync_word=b"NEXT", repeat=2)
        dynamic.finish()
        dynamic.wait()
    report["cases"]["dynamic_after_idle"] = dynamic.stats.to_dict()
    duration = LiveTransmitter(modulation, hardware, b"TEST", duration=1, **selection)
    duration.submit(bytes(range(6)), repeat=1_000_000)
    with duration:
        duration.wait()
    report["cases"]["duration_stop"] = duration.stats.to_dict()
    report["success"] = all(case["hardware_shortfalls"] == 0
                            and case["stream"]["device_state"]["active_mode"] == 0
                            and case["status"] in ("completed", "duration_reached")
                            for case in report["cases"].values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
