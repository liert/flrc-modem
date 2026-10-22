"""DataLink radio compact frames; no DK32S outer timestamp or host protocol header."""
from collections.abc import Mapping

from ..parsing.protocols.common import BAUD_RATES
from .dk32s import integer


def _business(command, fields, direction, need_ack, is_ack):
    if 'data_hex' in fields:
        value = fields.pop('data_hex')
        if not isinstance(value, str):
            raise ValueError('data_hex 必须为十六进制字符串')
        return bytes.fromhex(value)
    if command not in (0x18, 0xF3, 0xF7):
        raise ValueError(f'DataLink 命令 0x{command:02X} 暂无字段编码器，请显式提供 fields.data_hex')
    if direction != 'ground_to_air' or need_ack or not is_ack:
        raise ValueError('DataLink 配置下发需要 ground_to_air、need_ack=false、is_ack=true')
    if command == 0x18:
        return bytes((integer(fields.pop('mode'), 0, 255, 'mode'),
                      integer(fields.pop('sync_count'), 0, 255, 'sync_count')))
    if command == 0xF3:
        return bytes((integer(fields.pop('fcu_type'), 0, 255, 'fcu_type'),))
    if ('baud_index' in fields) == ('baud_rate' in fields):
        raise ValueError('必须且只能提供 baud_index 或 baud_rate')
    if 'baud_rate' in fields:
        baud = integer(fields.pop('baud_rate'), 1, 0xFFFFFFFF, 'baud_rate')
        if baud not in BAUD_RATES:
            raise ValueError(f'baud_rate 必须为 {", ".join(map(str, BAUD_RATES))}')
        index = BAUD_RATES.index(baud)
    else:
        index = integer(fields.pop('baud_index'), 0, len(BAUD_RATES) - 1, 'baud_index')
    return bytes((index,))


def encode(message, context):
    """Build N | compact; sequence defaults to the current global frame index."""
    if not isinstance(message, Mapping):
        raise ValueError('DataLink 报文必须为对象')
    allowed = {'direction', 'command', 'sequence', 'need_ack', 'is_ack', 'fields'}
    if set(message) - allowed:
        raise ValueError(f'未知 DataLink 字段：{sorted(set(message) - allowed)}')
    try:
        direction = message['direction']
        if direction not in ('ground_to_air', 'air_to_ground'):
            raise ValueError('direction 应为 ground_to_air 或 air_to_ground')
        command = integer(message['command'], 0, 255, 'command')
        sequence = integer(message.get('sequence', context.frame_index % 32768), 0, 32767, 'sequence')
        need_ack, is_ack = message.get('need_ack', False), message.get('is_ack', False)
        if type(need_ack) is not bool or type(is_ack) is not bool:
            raise ValueError('need_ack/is_ack 必须为布尔值')
        fields = message.get('fields', {})
        if not isinstance(fields, dict):
            raise ValueError('fields 必须为业务字段对象')
        fields = fields.copy()
        data = _business(command, fields, direction, need_ack, is_ack)
        if fields:
            raise ValueError(f'未使用的 DataLink 业务字段：{sorted(fields)}')
        # The FLRC physical payload includes one software length byte and five
        # compact framing bytes. Raw input does not infer firmware command semantics.
        if len(data) > 121:
            raise ValueError('DataLink 业务数据最多 121B，完整物理载荷不得超过 127B')
        compact = bytes((len(data) | (int(need_ack) << 7),))
        compact += ((sequence << 1) | int(is_ack)).to_bytes(2, 'little') + bytes((command,)) + data
        compact += bytes((sum(compact) & 255,))
        return bytes((len(compact),)) + compact
    except KeyError as error:
        raise ValueError(f'缺少 DataLink 字段：{error.args[0]}') from None
