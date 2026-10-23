"""Bounded, request-correlated control protocol for a persistent duplex process."""
import json
import queue
import sys
import threading
import time

from .requests import validate_request
from .orchestration import PlanScheduler, validate_plan, validate_plan_payloads


def exchange_status(result, wait):
    return ('cancelled' if result.cancelled else 'reply_matched' if result.reply_matched else
            'reply_timeout' if result.tx_completed and wait else
            'tx_completed' if result.tx_completed else 'send_failed')


def run_session(transceiver, args, emitter, stream=None):
    """One reader and one TX worker; stdin EOF drains accepted work and closes RX."""
    inbox = queue.Queue(maxsize=16)
    stopped = threading.Event()
    failures = []
    seen = set()
    default_invert_sync = getattr(args, 'tx_invert_sync', False)
    default_reply_invert_sync = getattr(args, 'reply_invert_sync', False)
    scheduler = PlanScheduler(emitter, invert_sync=default_invert_sync,
                              reply_invert_sync=default_reply_invert_sync)

    def status():
        reported_drops = 0
        while not stopped.is_set():
            try:
                info = transceiver.sync_diagnostics()
                emitter.emit('duplex.status', payload=info)
                if info.get('windows_skipped', 0) > reported_drops:
                    delta = info['windows_skipped'] - reported_drops
                    reported_drops = info['windows_skipped']
                    payload = {'action': 'skip_stale_and_reacquire', 'skipped_windows_delta': delta, **info}
                    emitter.emit('radio.backlog', payload=payload)
                    print(json.dumps({'event': 'sync.backlog', 'entry_point': 'flrc.duplex', **payload},
                                     ensure_ascii=False), file=sys.stderr, flush=True)
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
                        if scheduler.active:
                            scheduler.stop(interrupted=True)
                        transceiver.request_stop()
                        return
                    if isinstance(record, dict) and record.get('action') == 'start_plan':
                        if set(record) != {'action', 'plan'}:
                            raise ValueError('start_plan 仅支持 action / plan')
                        definition = validate_plan(record['plan'])
                        validate_plan_payloads(definition, protocol=getattr(args, 'protocol', 'dk32s'),
                                               sample_rate=transceiver.session.sample_rate)
                        if 'sync_probe' in definition:
                            transceiver.validate_sync_probe(definition['sync_probe'])
                        scheduler.start(definition)
                        continue
                    if isinstance(record, dict) and record.get('action') == 'stop_plan':
                        if (set(record) != {'action', 'plan_id'}
                                or not isinstance(record['plan_id'], str) or not record['plan_id']):
                            raise ValueError('stop_plan 需要 plan_id')
                        scheduler.stop(record['plan_id'])
                        continue
                    request = validate_request(record)
                    if request[0] in seen:
                        raise ValueError('request_id 已提交，拒绝重复发送')
                    if len(seen) >= 10000:
                        raise ValueError('会话请求上限为 10000，请重新建立会话')
                    inbox.put_nowait((*request, record.get('invert_sync', default_invert_sync),
                                      record.get('reply_invert_sync', default_reply_invert_sync)))
                    seen.add(request[0])
                    emitter.emit('duplex.request', payload={'request_id': request[0], 'status': 'queued',
                                 'invert_sync': record.get('invert_sync', default_invert_sync),
                                 'reply_invert_sync': record.get('reply_invert_sync', default_reply_invert_sync)})
                except (ValueError, TypeError, queue.Full) as error:
                    plan_control = isinstance(record, dict) and record.get('action') in ('start_plan', 'stop_plan')
                    emitter.emit('duplex.plan.rejected' if plan_control else 'duplex.result', payload={
                        'request_id': record.get('request_id') if isinstance(record, dict) else None,
                        **({'plan_id': record.get('plan', {}).get('plan_id')
                            if isinstance(record.get('plan'), dict) else record.get('plan_id'),
                            'action': record.get('action')} if plan_control else {}),
                        'status': 'rejected', 'error': str(error) or '会话队列已满'})
                    if plan_control and getattr(args, 'plan_file', None) is not None:
                        failures.append(error)
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
    input_closed, prefer_plan = False, False
    probe_plan_id, probe_last_emit = None, 0.0

    def close_probe(status):
        nonlocal probe_plan_id
        result = transceiver.finish_sync_probe(status)
        if result:
            emitter.emit('duplex.probe', payload=result)
        probe_plan_id = None
        return result

    try:
        while not stopped.is_set():
            if failures:
                raise OSError(str(failures[0]))
            if probe_plan_id is not None:
                if not scheduler.active or scheduler.plan['plan_id'] != probe_plan_id:
                    close_probe('stopped' if scheduler.plan['plan_id'] != probe_plan_id else scheduler.state)
                else:
                    snapshot = transceiver.sync_probe_snapshot()
                    if snapshot and snapshot.get('error'):
                        result = close_probe('error')
                        scheduler.finish_probe(result['error'], plan_id=result['plan_id'])
                    elif scheduler.state == 'observing' and time.monotonic() >= scheduler.observe_until:
                        result = close_probe('finished')
                        scheduler.finish_probe(result.get('error') if result else '探测结果丢失',
                                               plan_id=result['plan_id'] if result else None)
                    elif time.monotonic() - probe_last_emit >= 1:
                        if snapshot:
                            emitter.emit('duplex.probe', payload=snapshot)
                        probe_last_emit = time.monotonic()
            job = scheduler.next_job() if prefer_plan else None
            request = None
            try:
                if job is None and not input_closed:
                    request = inbox.get_nowait()
                    if request is None:
                        input_closed = True
            except queue.Empty:
                pass
            if job is None and request is None:
                job = scheduler.next_job()
            if job is None and request is None:
                if input_closed and not scheduler.active:
                    if failures:
                        raise OSError(str(failures[0]))
                    if getattr(args, 'plan_file', None) is not None and scheduler.plan:
                        return int(scheduler.state != 'completed' or scheduler.failed > 0)
                    return 0
                stopped.wait(.01)
                continue
            ident, payload, wait, invert_sync, reply_invert_sync = (
                job.request_id, job.payload, job.wait_reply,
                job.metadata['invert_sync'], job.metadata['reply_invert_sync']) if job else request
            emitter.emit('duplex.request', payload={'request_id': ident, 'status': 'sending',
                         'invert_sync': invert_sync, 'reply_invert_sync': reply_invert_sync,
                         **(job.metadata if job else {})})
            try:
                if job and 'sync_probe' in scheduler.plan and probe_plan_id != job.metadata['plan_id']:
                    if probe_plan_id is not None:
                        close_probe('stopped')
                    snapshot = transceiver.start_sync_probe(scheduler.plan['sync_probe'],
                        plan_id=job.metadata['plan_id'], sync_timeout=args.sync_timeout, cancel_event=job.cancel)
                    probe_plan_id = job.metadata['plan_id']
                    probe_last_emit = time.monotonic()
                    emitter.emit('duplex.probe', payload=snapshot)
                result = transceiver.send_and_wait_reply(payload, wait_reply=wait, invert_sync=invert_sync,
                    reply_invert_sync=reply_invert_sync,
                    reply_timeout=args.timeout, sync_timeout=args.sync_timeout,
                    **({'cancel_event': job.cancel} if job else {}))
                outcome = {'request_id': ident, 'status': exchange_status(result, wait),
                           **result.to_dict(), 'invert_sync': invert_sync,
                           'reply_invert_sync': reply_invert_sync}
            except (ValueError, TypeError, OSError) as error:
                outcome = {'request_id': ident, 'status': 'rejected', 'error': str(error),
                           'invert_sync': invert_sync, 'reply_invert_sync': reply_invert_sync}
            if job:
                scheduler.finish(job, outcome)
            else:
                emitter.emit('duplex.result', payload=outcome)
            prefer_plan = job is None
        return 0
    finally:
        stopped.set()
        if scheduler.active:
            scheduler.stop(interrupted=True)
        if probe_plan_id is not None:
            close_probe('interrupted')
        monitor.join(timeout=1)
