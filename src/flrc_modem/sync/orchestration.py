"""Mixed periodic/workflow scheduling inside the persistent radio process.

Host monotonic time controls eligibility. The transceiver still chooses the
hardware TDD window; eligibility is not a promise of an exact RF period.
"""
from copy import deepcopy
from dataclasses import dataclass
import threading
import time

from .requests import validate_request

MAX_EXECUTIONS = 10000
TERMINAL_PLAN_STATES = {'completed', 'failed', 'stopped', 'interrupted', 'limit_reached'}


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} 必须为 {minimum}..{maximum} 的整数')
    return value


def validate_plan(plan):
    """Return a normalized copy, rejecting the entire plan before it can run."""
    if not isinstance(plan, dict) or set(plan) - {'plan_id', 'periodic', 'steps', 'cycles'}:
        raise ValueError('编排仅支持 plan_id / periodic / steps / cycles')
    ident = plan.get('plan_id')
    if not isinstance(ident, str) or not 1 <= len(ident) <= 64:
        raise ValueError('plan_id 必须为 1..64 字符')
    result = {'plan_id': ident, 'cycles': _integer(plan.get('cycles', 1), 'cycles', 1, 10000)}
    for lane, maximum in (('periodic', 8), ('steps', 64)):
        items = plan.get(lane, [])
        if not isinstance(items, list) or len(items) > maximum:
            raise ValueError(f'{lane} 必须为不超过 {maximum} 项的数组')
        normalized, seen = [], set()
        for item in items:
            timing = {'interval_ms', 'count'} if lane == 'periodic' else {'delay_ms', 'on_failure'}
            if not isinstance(item, dict) or set(item) - ({'id', 'message', 'payload_hex', 'wait_reply'} | timing):
                raise ValueError(f'{lane} 包含不支持的字段')
            item_id = item.get('id')
            if not isinstance(item_id, str) or not 1 <= len(item_id) <= 32 or item_id in seen:
                raise ValueError(f'{lane} 的 id 必须为唯一的 1..32 字符字符串')
            seen.add(item_id)
            request = {key: value for key, value in item.items() if key in {'message', 'payload_hex', 'wait_reply'}}
            request.setdefault('wait_reply', lane == 'steps')
            validate_request({'request_id': item_id, **request})
            entry = {'id': item_id, **deepcopy(request)}
            if lane == 'periodic':
                entry['interval_ms'] = _integer(item.get('interval_ms'), 'interval_ms', 1, 86400000)
                entry['count'] = _integer(item.get('count', 0), 'count', 0, MAX_EXECUTIONS)
            else:
                entry['delay_ms'] = _integer(item.get('delay_ms', 0), 'delay_ms', 0, 86400000)
                entry['on_failure'] = item.get('on_failure', 'stop')
                if entry['on_failure'] not in ('stop', 'continue'):
                    raise ValueError('on_failure 必须为 stop 或 continue')
            normalized.append(entry)
        result[lane] = normalized
    if not result['periodic'] and not result['steps']:
        raise ValueError('编排至少需要一个周期任务或流程步骤')
    return result


def validate_plan_payloads(plan, *, protocol, sample_rate):
    """Preview every structured payload without advancing live template counters."""
    messages = [item['message'] for lane in ('periodic', 'steps') for item in plan[lane] if 'message' in item]
    if not messages:
        return
    from ..encoding import EncodeContext, MessageTemplate, load_encoder
    from ..phy.encoder import encode_packet
    encoder = load_encoder(protocol)
    for message in messages:
        raw = MessageTemplate(encoder, message).encode(EncodeContext(0, 0, sample_rate))
        encode_packet(raw, b'\x00' * 4)


@dataclass
class PlanJob:
    request_id: str
    payload: bytes | dict
    wait_reply: bool
    metadata: dict
    item: dict
    periodic_index: int | None
    due: float
    cancel: threading.Event


class PlanScheduler:
    """One in-flight exchange, bounded state, earliest eligible job first."""

    def __init__(self, emitter, clock=time.monotonic):
        self.emitter, self.clock = emitter, clock
        self.lock = threading.RLock()
        self.plan = None
        self.state = 'completed'
        self.in_flight = None
        self.seen = set()

    @property
    def active(self):
        with self.lock:
            return self.state in ('running', 'stopping')

    def start(self, definition):
        plan = validate_plan(definition)
        with self.lock:
            if self.active:
                raise ValueError('已有编排正在运行，请先停止当前编排')
            if plan['plan_id'] in self.seen:
                raise ValueError('plan_id 已提交，拒绝重复运行')
            if len(self.seen) >= 10000:
                raise ValueError('会话编排数量已达上限，请重新建立会话')
            self.seen.add(plan['plan_id'])
            self.plan, self.state = plan, 'running'
            self.cancel = threading.Event()
            self.started = self.clock()
            self.executed = self.succeeded = self.failed = self.skipped = 0
            self.step_index = self.cycles_completed = 0
            self.workflow_due = self.started + (plan['steps'][0]['delay_ms'] / 1000 if plan['steps'] else 0)
            self.periodic = [{'due': self.started, 'executed': 0, 'skipped': 0} for _ in plan['periodic']]
            self.emitter.emit('duplex.plan.definition', payload=deepcopy(plan))
            self._emit()

    def _emit(self, error=None):
        payload = {'plan_id': self.plan['plan_id'], 'status': self.state,
                   'executed': self.executed, 'succeeded': self.succeeded, 'failed': self.failed,
                   'skipped': self.skipped, 'step_index': self.step_index,
                   'cycles_completed': self.cycles_completed,
                   'elapsed_ms': round((self.clock() - self.started) * 1000, 3)}
        if error:
            payload['error'] = error
        self.emitter.emit('duplex.plan', payload=payload)

    def stop(self, plan_id=None, *, interrupted=False):
        with self.lock:
            if not self.active and plan_id is None:
                return
            if not self.active or (plan_id is not None and self.plan['plan_id'] != plan_id):
                raise ValueError('指定编排当前未运行')
            self.cancel.set()
            self.state = 'interrupted' if interrupted else 'stopping' if self.in_flight else 'stopped'
            self._emit()

    def next_job(self):
        with self.lock:
            if self.state != 'running' or self.in_flight:
                return None
            now, candidates = self.clock(), []
            for index, (item, runtime) in enumerate(zip(self.plan['periodic'], self.periodic)):
                if not item['count'] or runtime['executed'] < item['count']:
                    candidates.append((runtime['due'], index, item))
            if self.plan['steps'] and self.cycles_completed < self.plan['cycles']:
                candidates.append((self.workflow_due, -1, self.plan['steps'][self.step_index]))
            if not candidates:
                self.state = 'completed'
                self._emit()
                return None
            due, index, item = min(candidates, key=lambda candidate: candidate[:2])
            if due > now:
                return None
            if self.executed >= MAX_EXECUTIONS:
                self.state = 'limit_reached'
                self._emit('编排已执行 10000 次，停止发送；需要时请提交新编排')
                return None
            request_id = f"@plan:{self.plan['plan_id']}:{self.executed + 1}"
            _, payload, wait = validate_request({'request_id': item['id'], **{
                key: item[key] for key in ('message', 'payload_hex', 'wait_reply') if key in item}})
            metadata = {'plan_id': self.plan['plan_id'], 'source': 'step' if index == -1 else 'periodic',
                        'item_id': item['id'], 'cycle': self.cycles_completed + 1 if index == -1 else None,
                        'scheduled_offset_ms': round((due - self.started) * 1000, 3),
                        'started_offset_ms': round((now - self.started) * 1000, 3),
                        'lateness_ms': round(max(0, now - due) * 1000, 3)}
            self.in_flight = PlanJob(request_id, deepcopy(payload), wait, metadata, item,
                                     None if index == -1 else index, due, self.cancel)
            return self.in_flight

    def finish(self, job, result):
        with self.lock:
            if self.in_flight is not job:
                raise RuntimeError('编排执行结果与当前请求不匹配')
            self.in_flight = None
            self.executed += 1
            succeeded = result['status'] in ('reply_matched', 'tx_completed')
            self.succeeded += int(succeeded)
            self.failed += int(not succeeded)
            now = self.clock()
            skipped = 0
            if job.periodic_index is not None:
                runtime = self.periodic[job.periodic_index]
                interval = job.item['interval_ms'] / 1000
                skipped = max(0, int((now - job.due) / interval))
                runtime['due'] = job.due + (skipped + 1) * interval
                runtime['executed'] += 1
                runtime['skipped'] += skipped
                self.skipped += skipped
            else:
                self.step_index += 1
                if self.step_index == len(self.plan['steps']):
                    self.cycles_completed += 1
                    self.step_index = 0
                self.workflow_due = now + self.plan['steps'][self.step_index]['delay_ms'] / 1000
            self.emitter.emit('duplex.result', payload={**result, 'request_id': job.request_id,
                               **job.metadata, 'skipped_periods': skipped})
            if self.state == 'stopping':
                self.state = 'stopped'
            elif self.state == 'running' and not succeeded and (job.periodic_index is None
                    and job.item['on_failure'] == 'stop'):
                self.state = 'failed'
            self._emit(result.get('error') if self.state == 'failed' else None)
