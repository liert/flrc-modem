"""离线/实时入口共用的物理参数和协议输出选项。"""
from pathlib import Path
from .parsing.protocols import BUILTIN_PARSERS
from .phy.profile import CODING_RATES, parse_bt


def add_demod_options(parser, *, file_format=True):
    parser.add_argument("--config", type=Path, help="可选 TOML 物理层参数文件")
    parser.add_argument("--sample-rate", type=float, help="IQ 采样率，Hz")
    parser.add_argument("--center-frequency", type=float, help="采集中心频率，Hz")
    parser.add_argument("--frequency", dest="frequencies", type=float, action="append", help="目标载频，Hz；可重复")
    parser.add_argument("--bit-rate", type=float, help="FLRC 空口码率，bit/s")
    parser.add_argument("--filter-cutoff", type=float, help="解调 FIR 低通截止频率，Hz")
    parser.add_argument("--cr", dest="coding_rate", choices=CODING_RATES, help="编码率，默认 3/4")
    parser.add_argument("--bt", type=parse_bt, help="0 关闭高斯成形，或选择 0.5、1；默认 1")
    if file_format:
        parser.add_argument("--iq-format", choices=("cs8", "cu8", "cf32"))
    parser.add_argument("--chunk-seconds", type=float, help="解调窗口步进，秒")
    parser.add_argument("--overlap-ms", type=float, help="窗口重叠，毫秒")
    parser.add_argument("--max-seconds", type=float, help="最长输入时间，0 表示不限制")
    parser.add_argument("--sync", dest="sync_word", help="可选 4B 同步字十六进制")
    parser.add_argument("--header-type", choices=("variable", "fixed", "auto"), help="硬件头类型，默认 variable")
    parser.add_argument("--payload-length", dest="payload_lengths", type=int, action="append",
                        help="固定载荷字节数，可重复；未指定时尝试 6..127")
    parser.add_argument("--crc-bytes", type=int, choices=(2, 3, 4), help="硬件 CRC 字节数，默认 3")
    parser.add_argument("--crc-initial", type=lambda value: int(value, 0), help="硬件 CRC 初值，默认所选宽度的全 1")
    parser.add_argument("--crc-seed", dest="crc_initials", type=lambda value: int(value, 0), action="append",
                        help="候选 CRC 初值，可重复；指定后替代单一 crc-initial")
    parser.add_argument("--allow-frequency-wrap", action="store_true", default=None,
                        help="允许采集频带边缘的信号频谱环绕；频率仅代表数字域位置")


def add_output_options(parser):
    parser.add_argument("--output", type=Path, help="成功帧 JSONL 输出文件")
    parser.add_argument("--jsonl", action="store_true", help="stdout 只输出 JSONL，统计写入 stderr")
    parser.add_argument("--quiet", action="store_true", help="不输出逐帧控制台信息")
    parser.add_argument("--ui-events", choices=("ndjson",), default=None, help="启用机器事件流输出 (NDJSON)")
    parsing = parser.add_mutually_exclusive_group()
    parsing.add_argument("--protocol", choices=tuple(BUILTIN_PARSERS))
    parsing.add_argument("--parser", dest="parser_spec", help="业务插件：Python模块:函数名")


def demod_overrides(args):
    values = {name: getattr(args, name, None) for name in (
        "sample_rate", "center_frequency", "frequencies", "bit_rate", "iq_format",
        "chunk_seconds", "max_seconds", "sync_word", "crc_initial", "coding_rate", "bt",
        "header_type", "payload_lengths", "crc_bytes", "crc_initials", "allow_frequency_wrap",
        "filter_cutoff",
    )}
    if args.overlap_ms is not None:
        values["overlap_seconds"] = args.overlap_ms / 1000
    return values
