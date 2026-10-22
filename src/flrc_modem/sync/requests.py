"""Validation shared by manual submissions and orchestration plans."""

FIELDS = {'request_id', 'payload_hex', 'message', 'wait_reply', 'invert_sync', 'reply_invert_sync'}


def validate_request(record):
    if not isinstance(record, dict) or set(record) - FIELDS:
        raise ValueError('请求仅支持 request_id / payload_hex / message / wait_reply / invert_sync / reply_invert_sync')
    ident = record.get('request_id')
    if not isinstance(ident, str) or not 1 <= len(ident) <= 100:
        raise ValueError('request_id 必须为 1..100 字符')
    if ident.startswith('@plan:'):
        raise ValueError('request_id 的 @plan: 前缀保留给编排执行结果')
    if ('payload_hex' in record) == ('message' in record):
        raise ValueError('必须且只能提供 payload_hex 或 message')
    if 'payload_hex' in record:
        if not isinstance(record['payload_hex'], str):
            raise ValueError('payload_hex 必须为十六进制字符串')
        payload = bytes.fromhex(record['payload_hex'])
        if not 6 <= len(payload) <= 127:
            raise ValueError('原始载荷长度必须为 6..127B')
    else:
        payload = record['message']
        if not isinstance(payload, dict):
            raise ValueError('message 必须为协议 JSON 对象')
    if type(record.get('wait_reply', True)) is not bool:
        raise ValueError('wait_reply 必须为布尔值')
    if type(record.get('invert_sync', False)) is not bool:
        raise ValueError('invert_sync 必须为布尔值')
    if type(record.get('reply_invert_sync', False)) is not bool:
        raise ValueError('reply_invert_sync 必须为布尔值')
    return ident, payload, record.get('wait_reply', True)
