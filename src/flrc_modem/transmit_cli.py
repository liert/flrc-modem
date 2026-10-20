"""直接输入载荷或 JSONL 动态报文，调制后经内存送给 HackRF。"""
import argparse
import json
from math import ceil, isfinite
from pathlib import Path
import sys
import threading

from .modulation import ModulationConfig
from .encoding import load_encoder
from .encoding.input import iter_message_records, read_message
from .phy.profile import CODING_RATES, FLRC_RATE_PROFILES, RAMP_TIME_PARAMS, parse_bt
from .sdr.hackrf import HackRFConfig, HackRFError, StreamCancelled
from .sdr.hackrf.config import BASEBAND_BANDWIDTHS
from .sdr.hackrf.selection_cli import add_device_arguments, report_selected_device
from .transmission import LiveTransmitter
from .transmission.input import iter_json_records


def make_argument_parser():
    parser = argparse.ArgumentParser(description="FLRC 实时发送：载荷直接调制并送入 HackRF，不保存 IQ")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload-hex", help="6..127B 原始物理载荷，十六进制")
    source.add_argument("--payload-file", type=Path, help="原始载荷二进制文件，不是 IQ")
    source.add_argument("--stdin-jsonl", action="store_true", help="逐行读取动态 JSON 报文，EOF 排空后结束")
    source.add_argument("--message-file", type=Path, help="协议业务字段 JSON 模板，配合 --protocol")
    parser.add_argument("--protocol", "--encoder", dest="encoder", help="发送插件：dk32s 或 Python模块:函数名")
    parser.add_argument("--sync", help="默认 4B 同步字，十六进制；JSONL 可逐条覆盖")
    parser.add_argument("--crc-initial", type=lambda value: int(value, 0), default=0xFFFFFF)
    parser.add_argument("--frequency", type=int, required=True, help="HackRF 发射中心频率，Hz")
    parser.add_argument("--sample-rate", type=float, help="IQ 采样率，默认按码率选择，当前模式为 2MSps")
    parser.add_argument("--bit-rate", type=float, choices=tuple(FLRC_RATE_PROFILES), default=325_000)
    parser.add_argument("--bandwidth-hz", "--bandwidth", dest="bandwidth_hz", type=float,
                        help="SX1280 FLRC 标称 BW，必须匹配 bitrate")
    parser.add_argument("--cr", dest="coding_rate", choices=CODING_RATES, default="3/4")
    parser.add_argument("--bt", type=parse_bt, default=1.0, help="0 关闭高斯成形，或 0.5、1；默认 1")
    parser.add_argument("--ramp-time-us", type=float, choices=tuple(RAMP_TIME_PARAMS), default=12)
    parser.add_argument("--offset-hz", type=float, default=0, help="相对中心的数字频率偏移，Hz")
    parser.add_argument("--amplitude", type=float, default=0.7)
    parser.add_argument("--repeat", type=int, default=1, help="重复次数；载荷/业务模板设 0 可持续发送；模板每包重新编码")
    parser.add_argument("--gap-ms", type=float, default=10, help="每个突发后最小静默时间，ms")
    parser.add_argument("--guard-ms", type=float, default=10, help="整个流首尾静默时间，ms")
    parser.add_argument("--duration", type=float, default=0, help="最长会话秒数；0 不限制，到时立即停止")
    parser.add_argument("--input-queue-depth", type=int, default=64)
    parser.add_argument("--queue-depth", type=int, default=8, help="原始 IQ 缓冲数量，默认 8")
    parser.add_argument("--prefill-blocks", type=int, default=4)
    parser.add_argument("--block-bytes", type=int)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--health-interval", type=float, default=1)
    parser.add_argument("--underrun-policy", choices=("stop", "zeros"), default="stop")
    parser.add_argument("--allow-device-shortfalls", action="store_true")
    parser.add_argument("--baseband-bandwidth-hz", type=int, choices=BASEBAND_BANDWIDTHS)
    parser.add_argument("--tx-gain", type=int, default=0)
    parser.add_argument("--amp", action="store_true")
    parser.add_argument("--bias-tee", action="store_true")
    parser.add_argument("--dll", type=Path)
    add_device_arguments(parser)
    return parser


def print_configuration(modulation, hardware, args, payload, packet_sync, first_repeat):
    """打印解析/自动选择后的生效值；在设备打开前刷新到终端。"""
    def number(value):
        return str(int(value)) if value == int(value) else str(value)
    actual_frequency = hardware.frequency_hz + modulation.offset_hz
    repeat = (f"逐条指定，默认 {args.repeat} 次；首包 {first_repeat} 次" if args.stdin_jsonl
              else "持续重复 (0)" if args.repeat == 0 else f"{args.repeat} 次")
    duration = "不限" if args.duration == 0 else f"{args.duration:g} s（到时立即停止）"
    sync_label = "首包同步字" if args.stdin_jsonl else "同步字"
    lines = [
        "发送配置（实际生效值）：",
        f"  设备选择={'序列号 ' + args.serial if args.serial is not None else '索引 ' + str(args.device_index) if args.device_index is not None else '自动（第一个可打开的设备）'}",
        f"  中心频率={hardware.frequency_hz} Hz ({hardware.frequency_hz / 1e6:.6f} MHz)",
        f"  数字偏移={modulation.offset_hz:+g} Hz；实际载频={number(actual_frequency)} Hz "
        f"({actual_frequency / 1e6:.6f} MHz)",
        f"  采样率={number(modulation.sample_rate)} Sps；bitrate={number(modulation.bit_rate)} bit/s；"
        f"FLRC BW={number(modulation.bandwidth_hz)} Hz",
        f"  CR={modulation.coding_rate}；BT={modulation.bt:g}"
        f"{'（关闭高斯成形）' if modulation.bt == 0 else ''}；RampTime={modulation.ramp_time_us:g} us "
        f"({modulation.ramp_samples} 采样，实际 {modulation.ramp_samples / modulation.sample_rate * 1e6:g} us)",
        f"  IQ 幅度={modulation.amplitude:g}；HackRF 基带 BW={hardware.bandwidth_hz} Hz；"
        f"TX gain={hardware.tx_gain} dB；AMP={'开' if hardware.amp else '关'}；"
        f"Bias-Tee={'开' if hardware.bias_tee else '关'}",
        f"  {sync_label}={packet_sync.hex()}；CRC24 初值=0x{args.crc_initial:06X}；载荷={len(payload)} B",
        f"  输入={'JSONL' if args.stdin_jsonl else '协议模板' if args.encoder else '静态载荷'}；重复={repeat}；"
        f"gap={modulation.gap_seconds * 1000:g} ms；guard={modulation.guard_seconds * 1000:g} ms；时限={duration}",
        f"  报文队列={args.input_queue_depth}；IQ 缓冲={args.queue_depth}；预填充={args.prefill_blocks}；"
        f"块大小={args.block_bytes if args.block_bytes is not None else 'DLL 默认'}",
    ]
    if args.encoder:
        lines.insert(-1, f"  发送插件={args.encoder}；载荷长度为首包预览值；字段按每个突发的包号/样本时钟更新")
    print("\n".join(lines), file=sys.stderr, flush=True)


def main(argv=None):
    args = make_argument_parser().parse_args(argv)
    sender = feeder = None
    input_errors = []
    try:
        if args.encoder and (args.payload_hex is not None or args.payload_file is not None):
            raise ValueError("协议发送请使用 --message-file 或 --stdin-jsonl")
        if args.message_file is not None and not args.encoder:
            raise ValueError("--message-file 必须指定 --protocol dk32s 或 --encoder 模块:函数")
        encoder = load_encoder(args.encoder) if args.encoder else None
        if args.repeat < 0 or (args.stdin_jsonl and args.repeat == 0):
            raise ValueError("repeat 必须为正整数；载荷/业务模板文件允许 0 表示持续发送")
        if not isfinite(args.offset_hz):
            raise ValueError("offset_hz 必须为有限数")
        sample_rate = args.sample_rate
        if sample_rate is None:
            minimum = max(2_000_000, 4 * args.bit_rate, 2.1 * (abs(args.offset_hz) + args.bit_rate))
            sample_rate = ceil(minimum / 2_000_000) * 2_000_000
        modulation = ModulationConfig(sample_rate=sample_rate, bit_rate=args.bit_rate,
                                      bandwidth_hz=args.bandwidth_hz, offset_hz=args.offset_hz,
                                      coding_rate=args.coding_rate, bt=args.bt, ramp_time_us=args.ramp_time_us,
                                      amplitude=args.amplitude, gap_seconds=args.gap_ms / 1000,
                                      guard_seconds=args.guard_ms / 1000)
        hardware = HackRFConfig(args.frequency, sample_rate=sample_rate, tx_gain=args.tx_gain,
                                bandwidth_hz=args.baseband_bandwidth_hz, amp=args.amp, bias_tee=args.bias_tee)
        sync = bytes.fromhex(args.sync) if args.sync is not None else None
        sender = LiveTransmitter(modulation, hardware, sync, crc_initial=args.crc_initial,
                                 duration=args.duration, input_queue_depth=args.input_queue_depth,
                                 queue_depth=args.queue_depth, prefill_blocks=args.prefill_blocks,
                                 block_bytes=args.block_bytes, timeout=args.timeout, health_interval=args.health_interval,
                                 underrun_policy=args.underrun_policy,
                                 allow_device_shortfalls=args.allow_device_shortfalls,
                                 dll_path=args.dll, serial=args.serial, device_index=args.device_index,
                                 on_device_open=report_selected_device)
        records = None
        if args.stdin_jsonl:
            reader = iter_message_records if encoder else iter_json_records
            records = reader(sys.stdin, sync_word=sync, repeat=args.repeat)
            try:
                payload, packet_sync, repeat = next(records)
            except StopIteration:
                raise ValueError("标准输入中没有报文") from None
        else:
            if args.message_file is not None:
                payload = read_message(args.message_file)
            elif args.payload_file is not None:
                with args.payload_file.open("rb") as stream:
                    payload = stream.read(128)
            else:
                payload = bytes.fromhex(args.payload_hex)
            packet_sync, repeat = sync, args.repeat or 1
        # 首包在打开设备前完成校验，有限静态发送可直接结束输入。
        def submit(data, word, count=None):
            if encoder:
                return sender.submit_message(data, encoder, sync_word=word, repeat=count)
            sender.submit(data, sync_word=word, repeat=count)
            return data
        preview = submit(payload, packet_sync, repeat)
        dynamic = args.stdin_jsonl or args.repeat == 0
        if not dynamic:
            sender.finish()
        print_configuration(modulation, hardware, args, preview, packet_sync, repeat)
        def feed():
            try:
                if records is not None:
                    for data, word, count in records:
                        if sender.cancel.is_set():
                            return
                        submit(data, word, count)
                else:
                    while not sender.cancel.is_set():
                        submit(payload, packet_sync)
                sender.finish()
            except StreamCancelled:
                pass
            except BaseException as error:
                input_errors.append(error)
                sender.stop()
        with sender:
            if dynamic:
                # stdin 的阻塞读取不占用发送控制线程；EOF 或入队错误会结束会话。
                feeder = threading.Thread(target=feed, name="flrc-tx-input", daemon=True)
                feeder.start()
            sender.wait()
            if input_errors:
                raise input_errors[0]
        return 0
    except KeyboardInterrupt:
        print("实时发送已停止。", file=sys.stderr)
        return 130
    except (HackRFError, OSError, ValueError, TypeError) as error:
        if sender is not None and sender.stats.status == "created":
            sender.stats.status = "failed"
        print(f"错误：{error}", file=sys.stderr)
        return 2
    finally:
        if sender is not None:
            sender.stop()
            if feeder is not None:
                feeder.join(timeout=0.1)
            print(json.dumps(sender.stats.to_dict(), ensure_ascii=False), file=sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
