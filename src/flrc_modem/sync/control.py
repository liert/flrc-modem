"""Bounded, request-correlated control protocol for a persistent duplex process."""
import json
import queue
import sys
import threading


def validate_request(record):
    if not isinstance(record, dict) or set(record) - {'request_id', 'payload_hex', 'message', 'wait_reply'}:
        raise ValueError('请求仅支持 request_id / payload_hex / message / wait_reply')
    ident = record.get('request_id')
    if not isinstance(ident, str) or not 1 <= len(ident) <= 100:
        raise ValueError('request_id 必须为 1..100 字符')
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
    return ident, payload, record.get('wait_reply', True)


def run_session(transceiver, args, emitter, stream=None):
    """One reader and one TX worker; stdin EOF drains accepted work and closes RX."""
    inbox = queue.Queue(maxsize=16)
    stopped = threading.Event()
    failures = []
    seen = set()

    def status():
        while not stopped.is_set():
            try:
                info = transceiver.sync_diagnostics()
                emitter.emit('duplex.status', payload=info)
                if info.get('error'):
                    raise OSError(info['error'])
            except Exception as error:
                failures.append(error)
                return
            stopped.wait(1)

    def read():
        try:
            source = stream or sys.stdin
            while not stopped.is_set():
                line = source.readline(65538)
                if not line:
                    break
                record = {}
                try:
                    if len(line.encode('utf-8')) > 65536 or not line.endswith('\n'):
                        while line and not line.endswith('\n'):
                            line = source.readline(65538)
                        raise ValueError('请求必须为单行 JSON，长度不超过 64KiB')
                    record = json.loads(line)
                    if record == {'action': 'stop'}:
                        stopped.set()
                        transceiver.request_stop()
                        return
                    request = validate_request(record)
                    if request[0] in seen:
                        raise ValueError('request_id 已提交，拒绝重复发送')
                    if len(seen) >= 10000:
                        raise ValueError('会话请求上限为 10000，请重新建立会话')
                    inbox.put_nowait(request)
                    seen.add(request[0])
                    emitter.emit('duplex.request', payload={'request_id': request[0], 'status': 'queued'})
                except (ValueError, TypeError, queue.Full) as error:
                    emitter.emit('duplex.result', payload={
                        'request_id': record.get('request_id') if isinstance(record, dict) else None,
                        'status': 'rejected', 'error': str(error) or '会话队列已满'})
        except (OSError, UnicodeError) as error:
            failures.append(error)
        finally:
            # Avoid blocking on a full queue while a worker exits after hardware failure.
            while not stopped.is_set():
                try:
                    inbox.put(None, timeout=.2)
                    break
                except queue.Full:
                    pass

    reader = threading.Thread(target=read, name='duplex-control-input', daemon=True)
    monitor = threading.Thread(target=status, name='duplex-control-status', daemon=True)
    monitor.start()
    reader.start()
    try:
        while not stopped.is_set():
            if failures:
                raise OSError(str(failures[0]))
            try:
                request = inbox.get(timeout=.5)
            except queue.Empty:
                continue
            if request is None:
                if failures:
                    raise OSError(str(failures[0]))
                return 0
            ident, payload, wait = request
            emitter.emit('duplex.request', payload={'request_id': ident, 'status': 'sending'})
            try:
                result = transceiver.send_and_wait_reply(payload, wait_reply=wait,
                    reply_timeout=args.timeout, sync_timeout=args.sync_timeout)
                status = ('cancelled' if result.cancelled else 'reply_matched' if result.reply_matched else
                          'reply_timeout' if result.tx_completed and wait else
                          'tx_completed' if result.tx_completed else 'send_failed')
                emitter.emit('duplex.result', payload={'request_id': ident, 'status': status, **result.to_dict()})
            except (ValueError, TypeError, OSError) as error:
                emitter.emit('duplex.result', payload={'request_id': ident, 'status': 'rejected', 'error': str(error)})
        return 0
    finally:
        stopped.set()
        monitor.join(timeout=1)
