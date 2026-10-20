"""Shared transmit profile resolution. Receive-only search candidates never select TX settings."""
from dataclasses import asdict, dataclass
from pathlib import Path
from math import isfinite
import tomllib

from .phy.packet_format import PacketFormat
from .phy.profile import parse_bt


@dataclass(frozen=True)
class TransmitProfile:
    frequency: int
    sample_rate: float | None
    bit_rate: float
    coding_rate: str
    bt: float
    sync: str | None
    packet: PacketFormat

    def to_dict(self):
        return asdict(self)


def add_physical_arguments(parser):
    parser.add_argument('--config', type=Path, help='共用物理 TOML：[demod] 基础参数，可用 [transmit] 明确发送参数')
    parser.add_argument('--header-type', choices=['variable', 'fixed'])
    parser.add_argument('--crc-bytes', type=int, choices=[2, 3, 4])
    parser.add_argument('--payload-length', type=int, help='固定包长；必须与实际载荷一致，不自动填充')


def resolve_profile(args, *, duplex=False, require_rf=True):
    base, tx = {}, {}
    if args.config is not None:
        with Path(args.config).open('rb') as stream:
            doc = tomllib.load(stream)
        base, tx = doc.get('demod', {}), doc.get('transmit', {})
        if not isinstance(base, dict) or not isinstance(tx, dict):
            raise ValueError('[demod] 和 [transmit] 必须为 TOML 表')
        allowed = {'frequency', 'sample_rate', 'bit_rate', 'coding_rate', 'bt', 'sync_word',
                   'header_type', 'payload_lengths', 'crc_bytes', 'crc_initial'}
        if set(tx) - allowed:
            raise ValueError(f'未知 [transmit] 参数：{sorted(set(tx) - allowed)}')

    def value(cli, key, default):
        override = getattr(args, cli, None)
        return override if override is not None else tx.get(key, base.get(key, default))

    header = value('header_type', 'header_type', 'variable')
    width = value('crc_bytes', 'crc_bytes', 3)
    seed = value('crc_initial', 'crc_initial', None)
    if getattr(args, 'crc_initial', None) is None and 'crc_initial' not in tx:
        candidates = base.get('crc_initials', [])
        if len(set(candidates)) > 1:
            raise ValueError('接收模板含多个 CRC 初值，发送必须指定 --crc-initial 或 [transmit].crc_initial；不能按方向猜测')
        if candidates:
            seed = candidates[0]
    lengths = ((args.payload_length,) if args.payload_length is not None
               else tuple(tx.get('payload_lengths', base.get('payload_lengths', ()))))
    packet = PacketFormat(header, lengths, width, seed)
    if header == 'auto':
        raise ValueError('发送不能使用 auto 硬件头，必须选择 fixed 或 variable')
    # The recovered timestamps and reply matcher currently describe DK32S timing.
    if duplex and (header != 'variable' or width != 3 or packet.crc_initial != 0xFFFFFF):
        raise ValueError('同步时隙模型目前仅验证 DK32S 变长头/CRC24/0xFFFFFF；其他成包配置请使用盲发，不能套用 DK32S 时序')
    sync = value('sync', 'sync_word', None)
    if sync is not None:
        raw = bytes.fromhex(sync)
        if len(raw) != 4:
            raise ValueError('同步字必须为 4B 十六进制')
        sync = raw.hex()
    # Captured digital frequencies (including aliases) are never chosen as transmit RF.
    frequency = getattr(args, 'frequency', None)
    if frequency is None:
        frequency = tx.get('frequency')
    if frequency is None:
        if not duplex and require_rf:
            raise ValueError('请明确指定实际 RF 中心频率 --frequency 或 [transmit].frequency；不从接收采样/混叠频点推断')
        frequency = 2471660000 if duplex else 2480000000
    if isinstance(frequency, bool) or not isinstance(frequency, (int, float)) or not isfinite(frequency) or int(frequency) != frequency:
        raise ValueError('实际 RF 中心频率必须为整数 Hz')
    profile = TransmitProfile(int(frequency), value('sample_rate', 'sample_rate', None),
                              float(value('bit_rate', 'bit_rate', 325000)),
                              value('coding_rate', 'coding_rate', '3/4'),
                              parse_bt(value('bt', 'bt', 1)), sync, packet)
    if profile.frequency <= 0:
        raise ValueError('实际 RF 中心频率必须为正数')
    return profile


def apply_profile(args, *, duplex=False, require_rf=True):
    profile = resolve_profile(args, duplex=duplex, require_rf=require_rf)
    for key in ('frequency', 'sample_rate', 'bit_rate', 'coding_rate', 'bt', 'sync'):
        setattr(args, key, getattr(profile, key))
    args.header_type, args.crc_bytes = profile.packet.header_type, profile.packet.crc_bytes
    args.crc_initial, args.payload_lengths = profile.packet.crc_initial, profile.packet.payload_lengths
    return profile
