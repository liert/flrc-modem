"""Decouple pipe readers, transactional persistence, and individual WebSocket senders."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from time import perf_counter
from typing import Any
from fastapi import WebSocket

from flrc_modem.log import WideEvent
from ..storage.db import get_db
from .buffer import Entry, OutputBuffer

logger = logging.getLogger(__name__)


@dataclass
class Subscriber:
    run_id: str | None
    queue: OutputBuffer
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    reason: str = 'disconnected'


class EventHub:
    def __init__(self, *, max_records=2048, max_bytes=8 * 1024 * 1024,
                 client_records=256, client_bytes=2 * 1024 * 1024,
                 batch_size=128, batch_delay=0.01, send_timeout=1.0):
        if min(max_records, max_bytes, client_records, client_bytes, batch_size) <= 0:
            raise ValueError('Output queue limits must be positive')
        self._pending = OutputBuffer(max_records, max_bytes)
        self._space = asyncio.Event()
        self._writer: asyncio.Task | None = None
        self._failure: Exception | None = None
        self._failed = asyncio.Event()
        self._closing = False
        self._clients: dict[WebSocket, Subscriber] = {}
        self._retiring: set[asyncio.Task] = set()
        self._client_records, self._client_bytes = client_records, client_bytes
        self._batch_size, self._batch_delay = batch_size, batch_delay
        self._send_timeout = send_timeout
        self._persisted = self._batches = self._slow_clients = self._pressure_count = 0
        self._batch_ms = 0.0
        self._last_pressure_log = -float('inf')
        self._inflight_records = self._inflight_bytes = 0

    def start(self) -> None:
        self._check_failure()
        self._closing = False

    def _check_failure(self):
        if self._failure is not None:
            raise RuntimeError(f'任务输出持久化失败：{self._failure}') from self._failure

    async def _enqueue(self, entry: Entry, *, internal=False) -> None:
        self._check_failure()
        if self._closing and not internal:
            raise RuntimeError('任务输出服务正在停止')
        if entry.size > self._pending.max_bytes:
            raise ValueError('单条任务输出超过持久化队列字节上限')
        pressure_reported = False
        while True:
            self._space.clear()
            self._check_failure()
            if self._pending.put(entry):
                break
            if not pressure_reported:
                pressure_reported = True
                self._pressure_count += 1
                if perf_counter() - self._last_pressure_log >= 5:
                    self._last_pressure_log = perf_counter()
                    WideEvent('task_output.backpressure', logger=logger,
                              entry_point='webui.events', **self.snapshot()).emit(logging.WARNING)
            await self._space.wait()
        if self._writer is None or self._writer.done():
            self._writer = asyncio.create_task(self._write_pending(get_db()))

    async def dispatch_event(self, run_id: str, type_: str, seq: int,
                             stage_id: str | None, timestamp: str, payload: dict[str, Any]) -> None:
        await self._enqueue(Entry.create({'type': type_, 'run_id': run_id, 'seq': seq,
                           'stage_id': stage_id, 'timestamp': timestamp, 'payload': payload}))

    async def dispatch_log(self, run_id: str, stream: str, line: str) -> None:
        await self._enqueue(Entry.create({'type': 'task.log', 'run_id': run_id, 'stream': stream,
                           'line': line, 'timestamp': datetime.now(timezone.utc).isoformat()}))

    async def _write_pending(self, db) -> None:
        barriers = []
        try:
            while self._pending:
                await asyncio.sleep(self._batch_delay)
                batch, barriers = [], []
                self._inflight_bytes = 0
                for _ in range(self._batch_size):
                    if not self._pending:
                        break
                    entry = self._pending.pop()
                    if entry.barrier is not None:
                        barriers.append(entry.barrier)
                        break
                    batch.append(entry.message)
                    self._inflight_bytes += entry.size
                self._space.set()
                if batch:
                    self._inflight_records = len(batch)
                    start = perf_counter()
                    messages = await asyncio.to_thread(db.insert_output_batch, batch)
                    self._batch_ms = round((perf_counter() - start) * 1000, 2)
                    self._batches += 1
                    self._persisted += len(messages)
                    for message in messages:
                        await self._broadcast(message['run_id'], message)
                self._inflight_records = self._inflight_bytes = 0
                for barrier in barriers:
                    if not barrier.done():
                        barrier.set_result(None)
                barriers = []
        except Exception as error:
            self._failure = error
            self._failed.set()
            WideEvent('task_output.write_failed', logger=logger, entry_point='webui.events',
                      **self.snapshot()).record_error(error).emit(logging.ERROR)
            while self._pending:
                entry = self._pending.pop()
                if entry.barrier is not None:
                    barriers.append(entry.barrier)
            for barrier in barriers:
                if not barrier.done():
                    barrier.set_result(None)
            self._space.set()
        finally:
            self._inflight_records = self._inflight_bytes = 0

    async def wait_failed(self) -> None:
        await self._failed.wait()
        self._check_failure()

    async def flush(self) -> None:
        """A FIFO barrier: wait for accepted output, never for network clients."""
        self._check_failure()
        if not self._pending and (self._writer is None or self._writer.done()):
            return
        barrier = asyncio.get_running_loop().create_future()
        await self._enqueue(Entry(None, barrier=barrier), internal=True)
        await asyncio.shield(barrier)
        self._check_failure()

    async def register(self, ws: WebSocket, run_id: str | None = None) -> None:
        if self._closing:
            await ws.close(code=1013)
            return
        await ws.accept()
        client = Subscriber(run_id, OutputBuffer(self._client_records, self._client_bytes))
        self._clients[ws] = client
        client.task = asyncio.create_task(self._send_client(ws, client))

    async def unregister(self, ws: WebSocket, run_id: str | None = None) -> None:
        client = self._clients.pop(ws, None)
        if client is not None and client.task is not asyncio.current_task():
            client.task.cancel()
            await asyncio.gather(client.task, return_exceptions=True)
            await self._close_socket(ws, client)

    async def _close_socket(self, ws, client):
        try:
            await asyncio.wait_for(ws.close(code=1013 if client.reason != 'disconnected' else 1000), 0.1)
        except Exception:
            pass

    async def _retire_client(self, ws, client):
        await asyncio.gather(client.task, return_exceptions=True)
        await self._close_socket(ws, client)

    def _offer_client(self, ws: WebSocket, client: Subscriber, entry: Entry) -> None:
        if not client.queue.put(entry):
            self._slow_clients += 1
            client.reason = 'queue_full'
            self._clients.pop(ws, None)
            client.task.cancel()
            retiring = asyncio.create_task(self._retire_client(ws, client))
            self._retiring.add(retiring)
            retiring.add_done_callback(self._retiring.discard)
            WideEvent('task_output.client_disconnected', logger=logger,
                      entry_point='webui.events', run_id=client.run_id,
                      reason=client.reason, **client.queue.snapshot()).emit(logging.WARNING)
        else:
            client.ready.set()

    async def send_text(self, ws: WebSocket, text: str) -> None:
        client = self._clients.get(ws)
        if client is not None:
            self._offer_client(ws, client, Entry.create(text))

    async def _broadcast(self, run_id: str, message: dict[str, Any]) -> None:
        entry = Entry.create(message)
        for ws, client in list(self._clients.items()):
            if client.run_id is None or client.run_id == run_id:
                self._offer_client(ws, client, entry)

    async def _send_client(self, ws: WebSocket, client: Subscriber) -> None:
        try:
            while True:
                client.ready.clear()
                while client.queue:
                    entry = client.queue.pop()
                    await asyncio.wait_for(ws.send_text(entry.text), self._send_timeout)
                await client.ready.wait()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._slow_clients += 1
            client.reason = 'send_failed'
            WideEvent('task_output.client_disconnected', logger=logger,
                      entry_point='webui.events', run_id=client.run_id,
                      reason=client.reason).record_error(error).emit(logging.WARNING)
        finally:
            if self._clients.get(ws) is client:
                del self._clients[ws]
                await self._close_socket(ws, client)

    async def notify_run_change(self, run_id: str, type_: str, payload: dict[str, Any]) -> None:
        await self._broadcast(run_id, {'type': type_, 'run_id': run_id, 'seq': 0,
                              'stage_id': None, 'timestamp': datetime.now(timezone.utc).isoformat(),
                              'payload': payload})

    def snapshot(self) -> dict:
        return {**self._pending.snapshot(), 'persisted_records': self._persisted,
                'batches': self._batches, 'last_batch_ms': self._batch_ms,
                'backpressure_count': self._pressure_count, 'slow_clients': self._slow_clients,
                'inflight_records': self._inflight_records, 'inflight_bytes': self._inflight_bytes,
                'clients': len(self._clients),
                'client_queued_records': sum(len(client.queue) for client in self._clients.values()),
                'client_queued_bytes': sum(client.queue.bytes for client in self._clients.values()),
                'write_error': str(self._failure) if self._failure else None}

    async def shutdown(self) -> None:
        self._closing = True
        try:
            await self.flush()
        finally:
            await asyncio.gather(*(self.unregister(ws) for ws in list(self._clients)))
            await asyncio.gather(*self._retiring)


_EVENT_HUB: EventHub | None = None


def get_event_hub() -> EventHub:
    global _EVENT_HUB
    if _EVENT_HUB is None:
        _EVENT_HUB = EventHub()
    return _EVENT_HUB
