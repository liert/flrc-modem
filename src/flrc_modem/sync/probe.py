"""Passive unknown-sync discovery on a bounded branch of the existing RX stream."""
from dataclasses import replace
from collections import deque
import queue
import threading
import time

from ..parsing.api import load_parser


def validate_probe(value):
    if not isinstance(value, dict) or set(value) - {'observe_ms', 'min_frames', 'frequency_offsets_hz'}:
        raise ValueError('sync_probe 仅支持 observe_ms / min_frames / frequency_offsets_hz')
    result = {}
    for name, default, lower, upper in (('observe_ms', 5000, 1, 60000), ('min_frames', 3, 2, 100)):
        number = value.get(name, default)
        if type(number) is not int or not lower <= number <= upper:
            raise ValueError(f'sync_probe.{name} 必须为 {lower}..{upper} 的整数')
        result[name] = number
    offsets = value.get('frequency_offsets_hz', [0])
    if (not isinstance(offsets, list) or not 1 <= len(offsets) <= 9
            or any(type(offset) is not int for offset in offsets) or len(set(offsets)) != len(offsets)):
        raise ValueError('sync_probe.frequency_offsets_hz 必须为 1..9 个不重复的整数频偏')
    result['frequency_offsets_hz'] = list(offsets)
    return result


def probe_config(config, options):
    """Reuse physical settings and passband checks; only remove known sync filtering."""
    return replace(config, sync_word=None, sync_words=(),
                   frequencies=tuple(config.center_frequency + offset for offset in options['frequency_offsets_hz']))


def validate_probe_receiver(config, options, *, protocol, timed_hopping):
    options = validate_probe(options)
    if protocol != 'dk32s':
        raise ValueError('同步字探测编排目前仅支持 DK32S 协议')
    if timed_hopping:
        raise ValueError('同步字探测需要固定频段监听，请关闭定时跳频后重新建立会话')
    probe_config(config, options)
    return options


class SyncWordProbe:
    """A separate decoder never feeds the link clock or reply dispatcher.

    The producer hands over owned IQ arrays, never recyclable WindowQueue views.
    On overload the oldest pending window is discarded, keeping memory bounded.
    """

    def __init__(self, config, options, *, plan_id, excluded_words, start_hw_time, is_echo):
        self.config = probe_config(config, options)
        self.options, self.plan_id = options, plan_id
        self.excluded = set(excluded_words)
        self.start_hw_time = start_hw_time
        self.is_echo = is_echo
        self.parser = load_parser('dk32s')
        self.queue = queue.Queue(maxsize=8)
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.ready = threading.Event()
        self.recent = deque(maxlen=256)
        self.candidates = {}
        self.dropped_windows = self.stale_windows = self.candidate_limit_drops = 0
        self.decoded_frames = self.rejected_frames = self.ambiguous_frames = self.duplicate_frames = 0
        self.processed_windows = 0
        self.error = None
        self.thread = threading.Thread(target=self._run, name='flrc-sync-probe', daemon=True)
        self.thread.start()

    def submit(self, iq, start_time_s):
        if self.stopped.is_set() or start_time_s + len(iq) / self.config.sample_rate <= self.start_hw_time:
            return
        item = (iq, start_time_s, time.monotonic())
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            with self.lock:
                self.dropped_windows += 1
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                with self.lock:
                    self.dropped_windows += 1

    def accept(self, frame):
        if self.stopped.is_set() or frame.time_s < self.start_hw_time:
            return
        word = frame.packet.sync_word.hex()
        with self.lock:
            self.decoded_frames += 1
            if len(frame.packet.sync_crc_candidates) > 1:
                self.ambiguous_frames += 1
                return
            if word in self.excluded or self.is_echo(frame):
                self.rejected_frames += 1
                return
            if any(sync == word and abs(when - frame.time_s) < .00025 and payload == frame.packet.payload
                   for sync, when, payload in self.recent):
                self.duplicate_frames += 1
                return
            try:
                app = self.parser(frame) or {}
            except (ValueError, TypeError, IndexError):
                self.rejected_frames += 1
                return
            # A relay controller is a ground transmitter. Air telemetry cannot
            # establish the identity of a newly appearing controller sync word.
            if app.get('sum8_ok') is not True or app.get('direction') != 'ground_to_air':
                self.rejected_frames += 1
                return
            self.recent.append((word, frame.time_s, frame.packet.payload))
            candidate = self.candidates.get(word)
            if candidate is None:
                if len(self.candidates) >= 32:
                    self.candidate_limit_drops += 1
                    return
                candidate = {'sync_word_hex': word, 'valid_frames': 0, 'first_hw_time': frame.time_s,
                             'last_hw_time': frame.time_s, 'frequencies_hz': [], 'command': app.get('command'),
                             'sequence': app.get('sequence'), 'payload_hex': frame.packet.payload.hex()}
                self.candidates[word] = candidate
            candidate['valid_frames'] += 1
            candidate['last_hw_time'] = frame.time_s
            if frame.frequency_hz not in candidate['frequencies_hz']:
                candidate['frequencies_hz'].append(frame.frequency_hz)
            candidate['confirmed'] = candidate['valid_frames'] >= self.options['min_frames']

    def _run(self):
        try:
            from ..pipeline import WindowDecoder
            decoder = WindowDecoder(self.config)
            self.ready.set()
            while not self.stopped.is_set():
                try:
                    iq, start, queued = self.queue.get(timeout=.05)
                except queue.Empty:
                    continue
                if time.monotonic() - queued > .250:
                    with self.lock:
                        self.stale_windows += 1
                    continue
                for frame in decoder.decode(iq, start):
                    self.accept(frame)
                with self.lock:
                    self.processed_windows += 1
        except Exception as error:
            with self.lock:
                self.error = f'{type(error).__name__}: {error}'
        finally:
            self.ready.set()

    def snapshot(self, status='running'):
        with self.lock:
            candidates = [{**item, 'frequencies_hz': list(item['frequencies_hz'])}
                          for item in self.candidates.values()]
            return {'plan_id': self.plan_id, 'status': 'error' if self.error else status,
                    'start_hw_time': self.start_hw_time, 'center_frequency_hz': self.config.center_frequency,
                    'frequencies_hz': list(self.config.frequencies), 'sample_rate': self.config.sample_rate,
                    'excluded_sync_words': sorted(self.excluded), 'min_frames': self.options['min_frames'],
                    'candidates': sorted(candidates, key=lambda item: -item['valid_frames']),
                    'decoded_frames': self.decoded_frames, 'rejected_frames': self.rejected_frames,
                    'ambiguous_frames': self.ambiguous_frames, 'dropped_windows': self.dropped_windows,
                    'duplicate_frames': self.duplicate_frames,
                    'processed_windows': self.processed_windows,
                    'stale_windows': self.stale_windows, 'candidate_limit_drops': self.candidate_limit_drops,
                    'error': self.error}

    def close(self, status='finished'):
        self.stopped.set()
        self.thread.join(timeout=2)
        with self.lock:
            self.dropped_windows += self.queue.qsize()
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
        result = self.snapshot(status)
        result['outcome'] = ('error' if result['error'] else 'candidate_found'
                             if any(item['confirmed'] for item in result['candidates']) else
                             'insufficient_frames' if result['candidates'] else 'not_observed')
        result['worker_stopped'] = not self.thread.is_alive()
        return result
