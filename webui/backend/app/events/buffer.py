"""Bounded arrival-order buffers; only explicit snapshots may coalesce."""
from collections import OrderedDict
from dataclasses import dataclass
import asyncio
import json


SNAPSHOT_TYPES = frozenset({'metric.sample', 'stage.progress', 'duplex.status'})


@dataclass
class Entry:
    message: dict | str | None
    text: str = ''
    size: int = 0
    key: tuple | None = None
    barrier: asyncio.Future | None = None

    @classmethod
    def create(cls, message: dict | str):
        text = json.dumps(message, ensure_ascii=False) if isinstance(message, dict) else message
        key = None
        if isinstance(message, dict) and message.get('type') in SNAPSHOT_TYPES:
            key = (message['run_id'], message['type'], message.get('stage_id'))
        return cls(message, text, len(text.encode('utf-8')), key)


class OutputBuffer:
    def __init__(self, max_records: int, max_bytes: int):
        self.max_records, self.max_bytes = max_records, max_bytes
        self.entries: OrderedDict[int, Entry] = OrderedDict()
        self._snapshots: dict[tuple, int] = {}
        self._sequence = 0
        self.bytes = self.high_records = self.high_bytes = self.coalesced = 0

    def __len__(self):
        return len(self.entries)

    def put(self, entry: Entry) -> bool:
        old_id = self._snapshots.get(entry.key) if entry.key is not None else None
        old = self.entries.get(old_id)
        size = self.bytes + entry.size - (old.size if old else 0)
        count = len(self) + 1 - int(old is not None)
        if size > self.max_bytes or count > self.max_records:
            return False
        if old is not None:
            del self.entries[old_id]
            self.bytes -= old.size
            self.coalesced += 1
        # Do not move a snapshot across a result, lifecycle event, or flush barrier.
        if entry.barrier is not None or (isinstance(entry.message, dict)
                and entry.key is None and entry.message.get('type') != 'task.log'):
            self._snapshots.clear()
        self._sequence += 1
        self.entries[self._sequence] = entry
        if entry.key is not None:
            self._snapshots[entry.key] = self._sequence
        self.bytes += entry.size
        self.high_records = max(self.high_records, len(self))
        self.high_bytes = max(self.high_bytes, self.bytes)
        return True

    def pop(self) -> Entry:
        ident, entry = self.entries.popitem(last=False)
        self.bytes -= entry.size
        if entry.key is not None and self._snapshots.get(entry.key) == ident:
            del self._snapshots[entry.key]
        return entry

    def snapshot(self) -> dict:
        return {'queued_records': len(self), 'queued_bytes': self.bytes,
                'max_records': self.max_records, 'max_bytes': self.max_bytes,
                'high_records': self.high_records, 'high_bytes': self.high_bytes,
                'coalesced_snapshots': self.coalesced}
