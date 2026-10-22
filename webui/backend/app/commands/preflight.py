"""Resolve and validate message IO without loading drivers or opening hardware."""
from dataclasses import asdict
from pathlib import Path

from flrc_modem.encoding import EncodeContext, MessageTemplate, load_encoder
from flrc_modem.encoding.input import read_message
from flrc_modem.modulation import ModulationConfig
from flrc_modem.physical_cli import apply_profile
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.config import DemodConfig
from flrc_modem.sdr.usrp.config import with_master_clock_rate
from ..paths import PROJECT_ROOT


def preflight(command_id, argv):
    if command_id == 'transmit':
        from flrc_modem.transmit_cli import make_argument_parser, resolve_configuration
    else:
        from flrc_modem.duplex_cli import make_argument_parser, resolve_configuration as resolve_duplex
    try:
        args = make_argument_parser().parse_args(argv[4:])
    except SystemExit:
        raise ValueError('收发命令参数不完整或互相冲突') from None
    if args.config is not None and not args.config.is_absolute():
        args.config = PROJECT_ROOT / args.config
    rf = None
    if command_id == 'duplex':
        if args.rf_config is not None and not args.rf_config.is_absolute():
            args.rf_config = PROJECT_ROOT / args.rf_config
        profile, rf = resolve_duplex(args)
    else:
        profile = apply_profile(args)
    if command_id == 'transmit':
        modulation, hardware = resolve_configuration(args)
        sample_rate = modulation.sample_rate
        device = {'type': 'hackrf', 'serial': args.serial, 'device_index': args.device_index,
                  'selection': args.serial or (f'index={args.device_index}' if args.device_index is not None else '自动选择首个可打开设备'),
                  'hardware': asdict(hardware)}
    else:
        sample_rate = args.sample_rate or {122880000: 1920000, 153600000: 2400000}.get(args.master_clock_rate, 2500000)
        modulation = ModulationConfig(sample_rate=sample_rate, bit_rate=args.bit_rate,
                                      coding_rate=args.coding_rate, bt=args.bt, offset_hz=0,
                                      amplitude=args.amplitude, ramp_time_us=args.ramp_time_us)
        DemodConfig(sample_rate=sample_rate, center_frequency=args.frequency, frequencies=(args.frequency,),
                    bit_rate=args.bit_rate, coding_rate=args.coding_rate, bt=args.bt,
                    header_type=profile.packet.header_type, crc_bytes=profile.packet.crc_bytes,
                    crc_initial=profile.packet.crc_initial, payload_lengths=profile.packet.payload_lengths)
        device = {'type': 'usrp', 'selection': with_master_clock_rate(args.args, args.master_clock_rate), 'channel': args.channel,
                  'antenna': args.antenna, 'rx_gain': args.gain, 'tx_gain': args.tx_gain}
    size = None
    session = command_id == 'duplex' and args.session
    if not session:
        if args.message_file:
            path = args.message_file if args.message_file.is_absolute() else PROJECT_ROOT / args.message_file
            encoder = load_encoder(args.encoder if command_id == 'transmit' else args.protocol)
            if encoder is None:
                raise ValueError('JSON 业务模板需要明确的发送协议插件')
            payload = MessageTemplate(encoder, read_message(path)).encode(EncodeContext(0, 0, sample_rate))
        elif args.payload_file is not None:
            path = args.payload_file if args.payload_file.is_absolute() else PROJECT_ROOT / args.payload_file
            payload = path.read_bytes()
        elif args.payload_hex is not None:
            payload = bytes.fromhex(args.payload_hex)
        else:
            raise ValueError('请选择 JSON 业务模板或原始十六进制载荷')
        fmt = profile.packet
        if fmt.header_type == 'fixed' and fmt.payload_lengths and len(payload) not in fmt.payload_lengths:
            raise ValueError(f'实际载荷 {len(payload)}B 不符合固定包长 {fmt.payload_lengths}')
        if command_id == 'transmit' and profile.sync is None:
            raise ValueError('盲发必须明确指定 4B 同步字 --sync 或模板 sync_word')
        encode_packet(payload, bytes.fromhex(profile.sync or '00000000'), fmt.crc_initial,
                      profile.coding_rate, header_type=fmt.header_type, crc_bytes=fmt.crc_bytes)
        size = len(payload)
    return {**profile.to_dict(), 'sample_rate': sample_rate, 'payload_bytes': size,
            **({'tx_invert_sync': args.tx_invert_sync, 'reply_invert_sync': args.reply_invert_sync,
                'amplitude': args.amplitude, 'ramp_time_us': args.ramp_time_us,
                'tx_sync_word': args.tx_sync_word, 'reply_sync_word': args.reply_sync_word}
               if command_id == 'duplex' else {}),
            'device': device, 'session': session, 'timing_model': 'DK32S FHSS/TDD' if command_id == 'duplex' else None,
            'rate_status': '计划值；硬件打开后的实际值见会话状态或任务日志',
            'bandwidth_hz': modulation.bandwidth_hz, 'rf_config': rf}
