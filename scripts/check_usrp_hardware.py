"""真实 USRP (UHD) 设备硬件自检脚本，验证网络连接、配置与收发流。"""
import argparse
from datetime import datetime, timezone
import json
from math import ceil
from pathlib import Path

from flrc_modem.sdr.usrp import UHDLibrary, USRPConfig, USRPDevice, discover_usrp_devices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args", default="addr=192.168.10.2", help="UHD 设备参数（默认 addr=192.168.10.2）")
    parser.add_argument("--dll", type=Path, help="自定义 uhd.dll 路径")
    parser.add_argument("--frequency", type=float, default=2_480_000_000, help="中心载波频率（默认 2.48GHz）")
    parser.add_argument("--duration", type=float, default=0.2, help="每个速率采集时长，秒（默认 0.2s）")
    parser.add_argument("--rates", type=float, nargs="+", default=[2_000_000, 10_000_000], help="测试的采样率列表")
    parser.add_argument("--tx", action="store_true", help="是否执行低增益短时发射自检")
    parser.add_argument("--output-dir", type=Path, help="输出报告目录")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    out_dir = args.output_dir or Path("outputs") / ("usrp-check-" + now.strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "started_utc": now.isoformat(),
        "device_args": args.args,
        "frequency_hz": args.frequency,
        "cases": [],
    }

    lib = UHDLibrary(args.dll)
    report["library_version"] = lib.get_version()
    report["discovered_devices"] = discover_usrp_devices(lib, args=args.args)

    print(f"[*] UHD 库版本: {report['library_version']}")
    print(f"[*] 探测到 {len(report['discovered_devices'])} 台设备")

    with USRPDevice(lib, device_args=args.args) as dev:
        print(f"[*] 已连接到设备: {dev.device_info}")
        report["device_info"] = dev.device_info

        # 1. RX 检查
        for rate in args.rates:
            cfg = USRPConfig(
                frequency_hz=args.frequency,
                sample_rate=rate,
                gain=30.0,
                device_args=args.args,
                cpu_format="cs8",
            )
            samps = ceil(args.duration * rate)
            rx_file = out_dir / f"capture_{int(rate / 1e6)}M.cs8"
            print(f"[*] 开始采集: {rate / 1e6} MSps, 时长: {args.duration}s, 预期采样: {samps}...")

            with rx_file.open("xb", buffering=0) as sink:
                stats = dev.receive(sink, cfg, samps)

            case_info = {
                "name": f"rx_{int(rate / 1e6)}M",
                "rate": rate,
                "file": str(rx_file),
                "file_size": rx_file.stat().st_size,
                **stats.to_dict(),
            }
            report["cases"].append(case_info)
            print(f"[+] 采集完成: {case_info['sample_count']} 采样, 溢出: {case_info['overflow_count']}")

        # 2. TX 检查（如果显式开启）
        if args.tx:
            tx_rate = 2_000_000.0
            tx_cfg = USRPConfig(
                frequency_hz=args.frequency,
                sample_rate=tx_rate,
                gain=10.0,
                device_args=args.args,
                cpu_format="cs8",
            )
            # 发射 2000 个 0 采样 (4000 字节)
            dummy_tx_bytes = b"\x00" * 4000
            print("[*] 正在执行测试发射 (4000 字节)...")
            tx_stats = dev.transmit(dummy_tx_bytes, tx_cfg)
            tx_case = {
                "name": "tx_2M_smoke",
                **tx_stats.to_dict(),
            }
            report["cases"].append(tx_case)
            print(f"[+] 发射完成: {tx_case['sample_count']} 采样")

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[*] 自检报告已保存至: {report_path}")


if __name__ == "__main__":
    main()
