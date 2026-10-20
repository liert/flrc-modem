"""基于硬件时间戳同步的交互式 TDD 双工收发机。

后台持续接收 IQ 采样并动态纠正时钟与跳频同步；
支持上层随时按需投递业务数据（十六进制/字符串），并在精准的 TDD 同步时隙以硬件时间戳发射，同时捕获对端回复。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import queue
import re
import threading
import time
from typing import Sequence

import numpy as np

from .live import DEFAULT_DK32S_HOPPING_TABLE, LiveSyncSession, LiveSyncStats
from .state_machine import SyncSnapshot, SyncState
from .tdd import TDDMode
from ..encoding import EncodeContext, load_encoder
from ..models import DecodedFrame
from ..modulation import ModulationConfig, modulate_bits
from ..phy.encoder import encode_packet
from ..sdr.usrp.config import DEFAULT_N310_SAMPLE_RATE, USRPConfig
from ..sdr.usrp.device import USRPDevice


@dataclass
class TDDExchangeResult:
    """单次 TDD 发送及接收回复记录。"""

    success: bool
    tx_payload: bytes
    tx_hw_time: float
    tx_slot: int
    tx_frequency_hz: int
    rx_reply: DecodedFrame | None = None
    rtt_seconds: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "tx_payload_hex": self.tx_payload.hex(),
            "tx_hw_time": self.tx_hw_time,
            "tx_slot": self.tx_slot,
            "tx_frequency_hz": self.tx_frequency_hz,
            "rtt_ms": (self.rtt_seconds * 1000) if self.rtt_seconds is not None else None,
            "error": self.error,
        }
        if self.rx_reply:
            d["rx_reply"] = {
                "time_s": self.rx_reply.time_s,
                "frequency_hz": self.rx_reply.frequency_hz,
                "payload_hex": self.rx_reply.packet.payload.hex(),
                "snr_db": self.rx_reply.sample_power_db,
            }
        return d


class LiveTDDTransceiver:
    """实时 TDD 同步双工收发机。"""

    def __init__(
        self,
        *,
        sdr_type: str = "usrp",
        usrp_args: str = "addr=192.168.10.2",
        frequency_hz: int = 2471660000,
        sample_rate: float | None = None,
        master_clock_rate: float | None = None,
        gain: float = 30.0,
        tx_gain: float = 30.0,
        channel: int = 0,
        sync_word: str = "ecee6698",
        bit_rate: float = 325_000.0,
        coding_rate: str = "3/4",
        protocol: str | None = "dk32s",
        hopping_table: Sequence[int] | None = None,
        tdd_mode: TDDMode = TDDMode.RECEPTION_TRIGGERED,
        timed_hopping: bool = False,
        usrp_device: USRPDevice | None = None,
    ):
        self.sdr_type = sdr_type.lower()
        self.usrp_args = usrp_args
        self.frequency_hz = int(frequency_hz)
        self.master_clock_rate = master_clock_rate
        self.gain = float(gain)
        self.tx_gain = float(tx_gain)
        self.channel = int(channel)
        self.sync_word = sync_word.lower()
        self.bit_rate = float(bit_rate)
        self.coding_rate = coding_rate
        self.protocol = protocol
        self.hopping_table = list(hopping_table or DEFAULT_DK32S_HOPPING_TABLE)
        self.tdd_mode = tdd_mode
        self.timed_hopping = timed_hopping

        # 协议编码器（支持直接将 JSON 字典编码为 FLRC 物理载荷）
        self._encoder = load_encoder(protocol) if protocol else None
        self._tx_frame_counter: int = 0

        # 硬件句柄（单设备收发复用）
        self._custom_dev = usrp_device
        self.device = usrp_device or USRPDevice(device_args=self.usrp_args)
        self._owns_device = usrp_device is None

        # 实时同步会话（负责持续 RX 采样与时钟校准）
        self.session = LiveSyncSession(
            sdr_type=self.sdr_type,
            usrp_args=self.usrp_args,
            frequency_hz=self.frequency_hz,
            sample_rate=sample_rate,
            master_clock_rate=self.master_clock_rate,
            gain=self.gain,
            channel=self.channel,
            sync_word=self.sync_word,
            hopping_table=self.hopping_table,
            tdd_mode=self.tdd_mode,
            timed_hopping=self.timed_hopping,
            usrp_device=self.device,
        )

        # 状态分发与回复等待队列
        self.last_snapshot: SyncSnapshot | None = None
        self._pending_waiters: list[tuple[float, queue.Queue[DecodedFrame]]] = []
        self._waiter_lock = threading.Lock()
        self._dispatcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """打开设备并启动后台持续接收与同步调度线程。"""
        if not self.device.handle.value:
            self.device.open()

        # 启动后台流式接收会话
        self.session.start()

        # 启动事件分发器线程
        self._stop_event.clear()
        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_loop,
            name="flrc-tdd-dispatcher",
            daemon=True,
        )
        self._dispatcher_thread.start()

    def stop(self):
        """停止收发机并释放资源。"""
        self._stop_event.set()
        self.session.stop()

        if self._dispatcher_thread and self._dispatcher_thread.is_alive():
            self._dispatcher_thread.join(timeout=1.0)

        if self._owns_device:
            self.device.close()

    def _dispatch_loop(self):
        """持续消费 session 事件，更新最新同步快照并唤醒等待回复的发送方。"""
        for frame, snap in self.session.events():
            if self._stop_event.is_set():
                break
            self.last_snapshot = snap

            # 匹配回复帧
            t_rx = frame.time_s
            with self._waiter_lock:
                matched = []
                for waiter in self._pending_waiters:
                    min_time, q = waiter
                    if t_rx >= min_time:
                        try:
                            q.put_nowait(frame)
                        except queue.Full:
                            pass
                        matched.append(waiter)
                for m in matched:
                    self._pending_waiters.remove(m)

    def wait_locked(self, timeout: float = 5.0) -> bool:
        """阻塞等待同步状态达到 LOCKED 或 HOLDOVER。"""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            snap = self.last_snapshot or self.session.last_snapshot
            if snap and snap.state in (SyncState.LOCKED, SyncState.HOLDOVER):
                return True
            time.sleep(0.010)
        return False

    def send_and_wait_reply(
        self,
        payload: bytes | str | dict,
        *,
        wait_reply: bool = True,
        reply_timeout: float = 1.0,
        sync_timeout: float = 3.0,
        target_hw_time: float | None = None,
        target_frequency_hz: int | None = None,
    ) -> TDDExchangeResult:
        """在同步锁定的 TDD 时隙准时打出数据，并等待接收回复。

        参数:
            payload: 发送载荷（bytes、十六进制字符串如 '010203040506' 或协议 JSON dict）
            wait_reply: 是否等待对端回复
            reply_timeout: 等待回复超时（秒）
            sync_timeout: 等待同步锁定超时（秒）
            target_hw_time: 显式指定的硬件发射时刻（默认由状态机自动选定最佳发射窗口）
            target_frequency_hz: 显式指定的发射载频（默认按跳频状态机选定）
        """
        # 1. 规范化载荷（统一支持 bytes、十六进制字符串与协议 JSON 字典）
        encode_ctx = EncodeContext(
            frame_index=self._tx_frame_counter,
            start_sample=0,
            sample_rate=self.session.sample_rate,
        )
        if isinstance(payload, dict):
            if self._encoder is None:
                raise ValueError(f"发送协议业务字段必须配置有效的 --protocol (当前为 None)")
            raw_payload = bytes(self._encoder(payload, encode_ctx))
            self._tx_frame_counter += 1
        elif isinstance(payload, str):
            trimmed = payload.strip()
            if trimmed.startswith("{") and trimmed.endswith("}"):
                import json
                if self._encoder is None:
                    raise ValueError(f"发送协议 JSON 报文必须配置有效的 --protocol (当前为 None)")
                msg_dict = json.loads(trimmed)
                raw_payload = bytes(self._encoder(msg_dict, encode_ctx))
                self._tx_frame_counter += 1
            else:
                clean = re.sub(r"[\s,]+", "", trimmed)
                try:
                    raw_payload = bytes.fromhex(clean)
                except ValueError:
                    raw_payload = trimmed.encode("utf-8")
        else:
            raw_payload = bytes(payload)

        # 保证 FLRC 物理载荷在 6..127 字节
        if len(raw_payload) < 6:
            raw_payload = raw_payload.ljust(6, b"\x00")
        elif len(raw_payload) > 127:
            raise ValueError(f"物理载荷长度 {len(raw_payload)} 超过 FLRC 上限 127 字节")

        # 2. 检查同步锁定状态
        if not self.wait_locked(timeout=sync_timeout):
            curr_state = self.last_snapshot.state.value if self.last_snapshot else "unknown"
            return TDDExchangeResult(
                success=False,
                tx_payload=raw_payload,
                tx_hw_time=0.0,
                tx_slot=0,
                tx_frequency_hz=self.frequency_hz,
                error=f"同步未锁定 (当前状态: {curr_state})，请等待接收到基准帧以校准时钟与时隙",
            )

        # 3. 规划发送时刻与载频
        t_now = self.device.get_time_now()
        lead_time = 0.005  # 5ms 提前量，保证网络下发指令至 FPGA 缓冲就绪

        if target_hw_time is not None:
            t_tx = float(target_hw_time)
            f_tx = int(target_frequency_hz or self.frequency_hz)
            s_tx = 0
        else:
            # 根据预测确定下一个可用发送时隙
            pred = self.session.state_machine.predict(t_now + lead_time)
            if pred.slot_remaining_s > 0.015:
                # 当前 50ms 时隙内还有充足时间 (>=15ms)，在当前时隙内发射
                t_tx = t_now + lead_time
                f_tx = pred.frequency_hz
                s_tx = pred.slot_index
            else:
                # 槽尾守候期不足，顺延至下一个跳频时隙首部
                t_tx = pred.next_hop_hw_time + 0.001
                f_tx = pred.next_frequency_hz
                s_tx = pred.next_slot_index

        # 4. 在线 FLRC 物理调制
        packet = encode_packet(
            raw_payload,
            sync_word=bytes.fromhex(self.sync_word),
            coding_rate=self.coding_rate,
        )
        mod_cfg = ModulationConfig(
            sample_rate=self.session.sample_rate,
            bit_rate=self.bit_rate,
        )
        waveform = modulate_bits(packet.bits, mod_cfg)

        # 转换并归一化为 cs8 格式 (I, Q int8)
        i_data = np.clip(np.round(waveform.real * 127), -128, 127).astype(np.int8)
        q_data = np.clip(np.round(waveform.imag * 127), -128, 127).astype(np.int8)
        tx_cs8 = np.column_stack((i_data, q_data)).tobytes()

        # 5. 配置 TX 射频前端
        tx_config = USRPConfig(
            frequency_hz=float(f_tx),
            sample_rate=self.session.sample_rate,
            gain=self.tx_gain,
            channel=self.channel,
            master_clock_rate=self.master_clock_rate,
            cpu_format="sc8",
        )
        self.device.configure_tx(tx_config)

        # 6. 注册回复监听器
        reply_box: queue.Queue[DecodedFrame] = queue.Queue(maxsize=1)
        if wait_reply:
            with self._waiter_lock:
                self._pending_waiters.append((t_tx, reply_box))

        # 7. 下发硬件定时发射指令 (Timed Burst)
        try:
            self.device.transmit_burst(
                tx_cs8,
                tx_config,
                target_hw_time_s=t_tx,
                timeout=2.0,
            )
        except Exception as e:
            with self._waiter_lock:
                self._pending_waiters = [w for w in self._pending_waiters if w[1] is not reply_box]
            return TDDExchangeResult(
                success=False,
                tx_payload=raw_payload,
                tx_hw_time=t_tx,
                tx_slot=s_tx,
                tx_frequency_hz=f_tx,
                error=f"硬件发射失败: {type(e).__name__}: {e}",
            )

        # 8. 等待接收回复
        if not wait_reply:
            return TDDExchangeResult(
                success=True,
                tx_payload=raw_payload,
                tx_hw_time=t_tx,
                tx_slot=s_tx,
                tx_frequency_hz=f_tx,
            )

        try:
            reply_frame = reply_box.get(timeout=reply_timeout)
            rtt = max(0.0, reply_frame.time_s - t_tx)
            return TDDExchangeResult(
                success=True,
                tx_payload=raw_payload,
                tx_hw_time=t_tx,
                tx_slot=s_tx,
                tx_frequency_hz=f_tx,
                rx_reply=reply_frame,
                rtt_seconds=rtt,
            )
        except queue.Empty:
            with self._waiter_lock:
                self._pending_waiters = [w for w in self._pending_waiters if w[1] is not reply_box]
            return TDDExchangeResult(
                success=True,
                tx_payload=raw_payload,
                tx_hw_time=t_tx,
                tx_slot=s_tx,
                tx_frequency_hz=f_tx,
                rx_reply=None,
                error="已发送，等待对端回复超时",
            )
