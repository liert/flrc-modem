"""FLRC 2.4GHz 全频段自动盲扫与信道感知命令行接口。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .io.ui_events import get_emitter
from .scan.engine import SpectrumScanner
from .scan.flrc_verifier import DEFAULT_BIT_RATES


def make_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flrc scan",
        description="2.4GHz 全频段自动盲扫与信道感知：检测活跃能量峰、估计 SNR/带宽、验证 FLRC 前导并提取同步字，导出信道配置",
    )
    # 输入源（离线 IQ 文件或 SDR 实时设备）
    src_group = parser.add_argument_group("输入源选择 (二选一)")
    src_group.add_argument("--iq", type=Path, help="待盲扫分析的离线原始 IQ 文件路径")
    src_group.add_argument("--sdr", choices=["hackrf", "usrp"], help="SDR 硬件实时全频段步进扫频后端")

    # 射频与扫频参数
    rf_group = parser.add_argument_group("射频与扫频参数")
    rf_group.add_argument("--sample-rate", type=float, help="采样率，Hz (IQ 文件默认 10MSps；SDR 留空时使用 --step-bw，独立于扫频步长)")
    rf_group.add_argument("--center-frequency", type=float, help="中心载波频率，Hz (离线 IQ 模式必填；SDR 模式下作为参考)")
    rf_group.add_argument("--start-freq", type=float, default=2_400_000_000.0, help="SDR 扫频起始频率，Hz (默认 2400.0 MHz)")
    rf_group.add_argument("--stop-freq", type=float, default=2_483_500_000.0, help="SDR 扫频截止频率，Hz (默认 2483.5 MHz)")
    rf_group.add_argument("--step-bw", type=float, default=10_000_000.0, help="SDR 扫频规划带宽上限，Hz；实际步长为有效带宽的 80% (默认 10.0 MHz)")
    rf_group.add_argument("--dwell", type=float, default=0.05, help="SDR 每个频点驻留采集时长，秒 (默认 0.05s)")
    rf_group.add_argument("--duration", type=float, default=0.5, help="离线 IQ 文件加载与分析时长，秒 (默认 0.5s)")
    rf_group.add_argument("--iq-format", choices=("cs8", "cu8", "cf32"), default="cs8", help="离线 IQ 文件采样格式 (默认 cs8)")
    rf_group.add_argument("--gain", type=float, default=30.0, help="SDR 通用接收增益，dB (默认 30 dB)")

    # 算法判决门限
    algo_group = parser.add_argument_group("感知与判定算法参数")
    algo_group.add_argument("--threshold-db", type=float, default=10.0, help="CFAR 能量检测高于自适应底噪的判定门限，dB (默认 10.0 dB)")
    algo_group.add_argument("--min-snr", type=float, default=6.0, help="信道候选输出的最小信噪比门限，dB (默认 6.0 dB)")
    algo_group.add_argument("--bit-rate", type=float, help="指定验证的单一 FLRC 目标比特率 (默认盲验 325k / 1.3M / 650k / 260k)")
    algo_group.add_argument("--no-flrc", action="store_true", help="仅进行能量检测与谱感知，跳过 FLRC 前导检验")

    # SDR 硬件定位参数
    sdr_group = parser.add_argument_group("SDR 硬件选项")
    sdr_group.add_argument("--serial", help="HackRF 设备序列号")
    sdr_group.add_argument("--device-index", type=int, help="HackRF 设备索引")
    sdr_group.add_argument("--amp", action="store_true", help="开启 HackRF 射频放大器 (+14 dB)")
    sdr_group.add_argument("--bias-tee", action="store_true", help="开启天线口直流偏置供电")
    sdr_group.add_argument("--args", help="USRP UHD 设备定位参数 (如 'addr=192.168.10.2')")
    sdr_group.add_argument("--master-clock-rate", type=float, help="USRP 主时钟，Hz；创建设备时生效 (N310 常用 125e6 / 122.88e6 / 153.6e6)")
    sdr_group.add_argument("--channel", type=int, default=0, help="USRP 接收物理通道")
    sdr_group.add_argument("--antenna", help="USRP 接收天线端口 (如 'RX2')")
    sdr_group.add_argument("--dll", type=Path, help="自定义 SDR 动态库路径 (libhackrf.dll / uhd.dll)")

    # 导出与展示选项
    out_group = parser.add_argument_group("输出与配置导出选项")
    out_group.add_argument("--output-config", type=Path, help="将感知检测到的信道参数导出为 demod / rx 物理层 TOML 配置文件")
    out_group.add_argument("--json", action="store_true", help="以机器可读 JSON 格式输出至标准输出")
    out_group.add_argument("--ui-events", choices=("ndjson",), default=None, help="启用机器事件流输出 (NDJSON)")
    out_group.add_argument("--no-color", action="store_true", help="禁用终端彩色文本高亮")

    return parser


def main(argv=None) -> int:
    parser = make_argument_parser()
    args = parser.parse_args(argv)

    ui_mode = getattr(args, "ui_events", None) == "ndjson"
    emitter = get_emitter(enabled=ui_mode)
    emitter.emit("task.state", payload={"state": "RUNNING", "command": "scan"})

    if not args.iq and not args.sdr:
        err_msg = "必须指定 --iq (离线采样文件) 或 --sdr (hackrf/usrp 实时硬件扫频) 之一。"
        emitter.emit("task.state", payload={"state": "FAILED", "error": err_msg})
        print(f"错误: {err_msg}", file=sys.stderr)
        parser.print_usage(sys.stderr)
        return 2

    scanner = SpectrumScanner()
    verify_flrc = not args.no_flrc
    candidate_rates = (args.bit_rate,) if args.bit_rate is not None else DEFAULT_BIT_RATES

    try:
        if args.iq:
            sample_rate = args.sample_rate if args.sample_rate is not None else 10_000_000.0
            if args.center_frequency is None:
                err_msg = "离线 IQ 文件盲扫必须指定 --center-frequency (如 --center-frequency 2480000000)。"
                emitter.emit("task.state", payload={"state": "FAILED", "error": err_msg})
                print(f"错误: {err_msg}", file=sys.stderr)
                return 2

            with emitter.stage("scanning_iq", {"file": str(args.iq), "sample_rate": sample_rate}):
                report = scanner.scan_iq_file(
                    path=args.iq,
                    sample_rate=sample_rate,
                    center_frequency=args.center_frequency,
                    iq_format=args.iq_format,
                    duration=args.duration,
                    threshold_db=args.threshold_db,
                    verify_flrc=verify_flrc,
                    candidate_bit_rates=candidate_rates,
                    min_snr=args.min_snr,
                )
        else:
            sdr_options = {
                "serial": args.serial,
                "device_index": args.device_index,
                "amp": args.amp,
                "bias_tee": args.bias_tee,
                "args": args.args,
                "channel": args.channel,
                "antenna": args.antenna,
                "master_clock_rate": args.master_clock_rate,
                "dll": args.dll,
            }
            with emitter.stage("scanning_sdr", {"sdr": args.sdr, "start_freq": args.start_freq, "stop_freq": args.stop_freq}):
                report = scanner.scan_live_sdr(
                    sdr=args.sdr,
                    start_freq=args.start_freq,
                    stop_freq=args.stop_freq,
                    step_bandwidth=args.step_bw,
                    sample_rate=args.sample_rate,
                    dwell_seconds=args.dwell,
                    gain=args.gain,
                    threshold_db=args.threshold_db,
                    verify_flrc=verify_flrc,
                    candidate_bit_rates=candidate_rates,
                    min_snr=args.min_snr,
                    sdr_options=sdr_options,
                )

        # 导出 TOML 配置文件
        if args.output_config is not None:
            args.output_config.parent.mkdir(parents=True, exist_ok=True)
            toml_content = report.to_toml_config(
                sample_rate=report.sample_rate,
                center_frequency=args.center_frequency,
            )
            args.output_config.write_text(toml_content, encoding="utf-8")
            emitter.artifact("channel_plan", args.output_config, "channel_plan_toml", {
                "total_channels": len(report.channels),
                "flrc_channels": len(report.flrc_channels),
            })

        emitter.metric("scan_report", report.to_dict())
        emitter.task_completed(True, **report.to_dict())

        # 结果输出
        if ui_mode:
            pass
        elif args.json:
            print(report.to_json(indent=2))
        else:
            use_color = not args.no_color and sys.stdout.isatty()
            print(report.format_table(use_color=use_color))
            if args.output_config is not None:
                print(f"\n[提示] 信道规划配置已成功保存至: {args.output_config}")

        return 0

    except (FileNotFoundError, ValueError, OSError) as err:
        emitter.emit("task.state", payload={"state": "FAILED", "error": str(err)})
        if not ui_mode:
            print(f"错误: {err}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        emitter.emit("task.state", payload={"state": "CANCELLED"})
        if not ui_mode:
            print("\n扫描操作已由用户手动中断 (Ctrl+C)", file=sys.stderr)
        return 130
    except Exception as err:
        emitter.emit("task.state", payload={"state": "FAILED", "error": str(err)})
        if not ui_mode:
            print(f"扫频感知执行异常: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
