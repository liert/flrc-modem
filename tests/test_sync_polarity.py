"""同步字极性反转：发送、接收解码与回复匹配独立选择。

用户场景：地面发送保持基准同步字，天空端在回复时使用取反后的同步字；
地面帧仍需用基准字完成同步锁定，回复必须按反字解码并匹配。
"""
import contextlib
import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from usrp_fake import FakeUHDLibrary

from flrc_modem.config import DemodConfig
from flrc_modem.dsp.detector import BurstDemodulator
from flrc_modem.duplex_cli import main as duplex_cli_main
from flrc_modem.duplex_cli import make_argument_parser, resolve_configuration
from flrc_modem.encoding import EncodeContext, load_encoder
from flrc_modem.io.ui_events import UIEventEmitter
from flrc_modem.modulation import ModulationConfig, iter_signal
from flrc_modem.models import DecodedFrame, PhysicalPacket
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.phy.sync import sync_word_for_polarity
from flrc_modem.sdr.usrp import USRPDevice
from flrc_modem.sync import LiveTDDTransceiver, SyncState, TDDExchangeResult
from flrc_modem.sync.control import run_session
from flrc_modem.sync.orchestration import PlanScheduler, validate_plan
from flrc_modem.sync.requests import validate_request
from webui.backend.app.commands.preflight import preflight
from webui.backend.app.commands.registry import CommandRegistry

BASE = "54313253"
INVERTED = "abcecdac"
SAMPLE_RATE = 2_000_000.0
FREQUENCY = 2_480_000_000
PAYLOAD = bytes(range(12))
TDD_FREQUENCY = 2_471_660_000
TDD_SYNC = bytes.fromhex("ecee6698")
TDD_INVERTED = sync_word_for_polarity(TDD_SYNC.hex(), True)
REQUEST = {"direction": "ground_to_air", "command": "0x23", "sequence": 23,
           "need_ack": True, "fields": {"data_hex": ""}}


def _burst_iq(sync_hex):
    packet = encode_packet(PAYLOAD, bytes.fromhex(sync_hex), coding_rate="3/4")
    config = ModulationConfig(sample_rate=SAMPLE_RATE, bit_rate=325_000, coding_rate="3/4",
                              bt=1.0, offset_hz=0, guard_seconds=0.01)
    return np.concatenate([np.asarray(chunk, dtype=np.complex64) for chunk in iter_signal(packet.bits, config)])


def _decode(iq, sync_hex, extra_words=()):
    config = DemodConfig(sample_rate=SAMPLE_RATE, center_frequency=FREQUENCY, frequencies=(FREQUENCY,),
                         bit_rate=325_000,
                         sync_word=bytes.fromhex(sync_hex) if sync_hex else None,
                         sync_words=tuple(bytes.fromhex(word) for word in extra_words))
    frames, _ = BurstDemodulator(config).demodulate(iq, FREQUENCY)
    return [(frame.packet.sync_word.hex(), frame.packet.payload) for frame in frames]


@contextlib.contextmanager
def _radio(**options):
    device = USRPDevice(FakeUHDLibrary())
    device.open()
    try:
        transceiver = LiveTDDTransceiver(usrp_device=device, sync_word=TDD_SYNC.hex(),
                                         hopping_table=[TDD_FREQUENCY] * 20, **options)
        device.get_time_estimate = MagicMock(return_value=100.5)
        machine = transceiver.session.state_machine
        for index in range(3):
            machine.fhss.observe(100 + index * .25, 100000 + index * 2500)
        machine.state = SyncState.LOCKED
        machine.last_anchor_hw = 100.5
        yield transceiver
    finally:
        device.close()


def _reply_frame(time_s, sync_word, sequence=101):
    raw = load_encoder("dk32s")({"direction": "air_to_ground", "command": "0x23", "sequence": sequence,
                                 "is_ack": True, "fields": {"data_hex": ""}},
                                EncodeContext(0, 0, 2_500_000))
    return DecodedFrame(time_s, TDD_FREQUENCY, 0, -20, 1, 0, PhysicalPacket(sync_word, b"", bytes(raw), b"", 0))


def test_sync_word_for_polarity_complements_every_byte_and_rejects_bad_words():
    assert sync_word_for_polarity(BASE) == BASE
    assert sync_word_for_polarity(BASE, True) == INVERTED
    assert sync_word_for_polarity(None) is None and sync_word_for_polarity(None, True) is None
    for invalid in ("123", "zzzzzzzz", BASE + "00"):
        with pytest.raises(ValueError):
            sync_word_for_polarity(invalid, True)


def test_explicit_receiver_word_accepts_only_that_polarity():
    # 单字显式候选（sync/demod 路径）仍然严格只接受配置的同步字。
    for tx_hex in (BASE, INVERTED):
        iq = _burst_iq(tx_hex)
        for rx_hex, expected in ((BASE, BASE if tx_hex == BASE else None),
                                 (INVERTED, INVERTED if tx_hex == INVERTED else None),
                                 (None, tx_hex)):
            frames = _decode(iq, rx_hex)
            received = [word for word, payload in frames if payload == PAYLOAD]
            assert received == ([] if expected is None else [expected]), (tx_hex, rx_hex)


def test_dual_candidate_receiver_accepts_both_polarities():
    # 双字候选（同步收发路径）：解码同时接受原字与反字，由 CRC 决定实际同步字。
    for tx_hex in (BASE, INVERTED):
        frames = _decode(_burst_iq(tx_hex), BASE, extra_words=(INVERTED,))
        assert [(word, payload == PAYLOAD) for word, payload in frames] == [(tx_hex, True)]


def test_request_and_plan_items_accept_per_message_polarity():
    validate_request({"request_id": "a", "payload_hex": PAYLOAD.hex(),
                      "invert_sync": True, "reply_invert_sync": True})
    for field in ("invert_sync", "reply_invert_sync"):
        for invalid in ("true", 1, None):
            with pytest.raises(ValueError, match=field):
                validate_request({"request_id": "a", "payload_hex": PAYLOAD.hex(), field: invalid})
    definition = validate_plan({
        "plan_id": "polarity",
        "periodic": [{"id": "session-default", "interval_ms": 500, "payload_hex": PAYLOAD.hex()},
                     {"id": "explicit", "interval_ms": 500, "payload_hex": PAYLOAD.hex(),
                      "invert_sync": False, "reply_invert_sync": True}],
        "steps": [{"id": "flipped", "payload_hex": PAYLOAD.hex(),
                   "invert_sync": True, "reply_invert_sync": False}],
    })
    assert definition["periodic"][0].get("invert_sync") is None
    assert definition["periodic"][1]["invert_sync"] is False
    assert definition["periodic"][1]["reply_invert_sync"] is True
    assert definition["steps"][0]["invert_sync"] is True
    assert definition["steps"][0]["reply_invert_sync"] is False
    output = io.StringIO()
    scheduler = PlanScheduler(UIEventEmitter(output, True), clock=lambda: 100.0,
                              invert_sync=True, reply_invert_sync=True)
    scheduler.start(definition)
    applied = {}
    while (job := scheduler.next_job()) is not None:
        applied[job.metadata["item_id"]] = (job.metadata["invert_sync"], job.metadata["reply_invert_sync"])
        scheduler.finish(job, {"status": "tx_completed", "tx_completed": True})
    # 缺省沿用会话默认；显式值逐条覆盖。
    assert applied == {"session-default": (True, True), "explicit": (False, True), "flipped": (True, False)}


def test_transceiver_applies_session_default_and_per_request_override():
    with _radio(tx_invert_sync=True, reply_invert_sync=True) as transceiver:
        info = transceiver.sync_diagnostics()
        assert info["base_sync_word"] == TDD_SYNC.hex()
        assert info["accepted_sync_words"] == sorted((TDD_SYNC.hex(), TDD_INVERTED))
        assert info["tx_invert_sync"] is True and info["reply_invert_sync"] is True
        assert info["reply_sync_word"] == TDD_INVERTED
        bursts = []
        transceiver.device.transmit_burst = lambda samples, config, **kwargs: (
            bursts.append(bytes(samples)), len(samples) // 2)[1]
        default = transceiver.send_and_wait_reply(PAYLOAD, wait_reply=False)
        baseline = transceiver.send_and_wait_reply(PAYLOAD, wait_reply=False,
                                                   invert_sync=False, reply_invert_sync=False)
        assert default.success and baseline.success
        assert (default.invert_sync, default.reply_invert_sync) == (True, True)
        assert default.tx_sync_word == bytes.fromhex(TDD_INVERTED)
        assert default.reply_sync_word == TDD_INVERTED
        assert (baseline.invert_sync, baseline.reply_invert_sync) == (False, False)
        assert baseline.tx_sync_word == TDD_SYNC and baseline.reply_sync_word == TDD_SYNC.hex()
        # 两条报文的载荷相同，空口波形必须因发送同步字极性而不同。
        assert len(bursts) == 2 and bursts[0] != bursts[1]


def test_reply_matching_uses_the_configured_reply_word_only():
    # 发送极性与回复极性互不影响。
    for tx_inverted, reply_inverted in ((True, False), (False, True), (False, False)):
        expected = bytes.fromhex(TDD_INVERTED if reply_inverted else TDD_SYNC.hex())
        with _radio(tx_invert_sync=tx_inverted, reply_invert_sync=reply_inverted) as transceiver:
            captured = {}

            def send(samples, config, target_hw_time_s, timeout):
                captured["waiter"] = list(transceiver._pending_waiters)[0]
                return len(samples) // 2

            transceiver.device.transmit_burst = send
            result = transceiver.send_and_wait_reply(REQUEST, reply_timeout=.001)
            assert result.tx_completed and not result.reply_matched
            assert captured["waiter"].sync_word == expected
            assert result.reply_sync_word == expected.hex()
            assert result.tx_sync_word == (bytes.fromhex(TDD_INVERTED) if tx_inverted else TDD_SYNC)


def test_ground_base_word_still_locks_and_inverted_reply_matches():
    """发送用原字、天空端回复用反字：地面帧锁定，反字回复可匹配。"""
    with _radio(tx_invert_sync=False, reply_invert_sync=True) as transceiver:
        # 地面帧（原字）仍进入同步状态机并作为时钟锚点。
        ground = DecodedFrame(100.75, TDD_FREQUENCY, 0, -20, 1, 0, PhysicalPacket(
            TDD_SYNC, b"", bytes(load_encoder("dk32s")(
                {"direction": "ground_to_air", "command": "0x11", "timestamp_ticks": 107500,
                 "fields": {"channels_raw": [1024] * 16}}, EncodeContext(0, 0, 2_500_000))), b"", 0))
        assert transceiver.session.state_machine.on_frame(100.75, ground) is True

        # 反字回复既能被会话解调配置接受，也能完成请求-回复匹配。
        assert set(transceiver.session.demod_config.sync_candidates) == {
            TDD_SYNC, bytes.fromhex(TDD_INVERTED)}
        transceiver._last_air_sequence = 100

        def send(samples, config, target_hw_time_s, timeout):
            tx_end = target_hw_time_s + len(samples) / 2 / transceiver.session.sample_rate
            transceiver.session.events = lambda: iter([
                (_reply_frame(tx_end + .001, bytes.fromhex(TDD_INVERTED)), None)])
            transceiver._dispatch_loop()
            return len(samples) // 2

        transceiver.device.transmit_burst = send
        result = transceiver.send_and_wait_reply(REQUEST, reply_timeout=.01)
        assert result.tx_completed and result.reply_matched, result.error
        assert result.rx_reply.packet.sync_word == bytes.fromhex(TDD_INVERTED)
        assert result.tx_sync_word == TDD_SYNC


def test_cli_resolves_tx_and_reply_polarity_independently():
    args = make_argument_parser().parse_args(["--sync", TDD_SYNC.hex(), "--tx-invert-sync", "--session"])
    assert resolve_configuration(args)[0].sync == TDD_SYNC.hex()
    assert args.tx_sync_word == TDD_INVERTED and args.reply_sync_word == TDD_SYNC.hex()
    args = make_argument_parser().parse_args(["--sync", TDD_SYNC.hex(), "--reply-invert-sync", "--session"])
    resolve_configuration(args)
    assert args.tx_sync_word == TDD_SYNC.hex() and args.reply_sync_word == TDD_INVERTED
    with pytest.raises(ValueError, match="回复反转需要明确的基准同步字"):
        resolve_configuration(make_argument_parser().parse_args(["--reply-invert-sync", "--session"]))


def test_cli_forwards_polarity_flags_to_transceiver(monkeypatch):
    radio = MagicMock()
    radio.return_value.send_and_wait_reply.return_value = SimpleNamespace(success=True, to_dict=lambda: {})
    monkeypatch.setattr("flrc_modem.duplex_cli.LiveTDDTransceiver", radio)
    assert duplex_cli_main(["--sync", TDD_SYNC.hex(), "--payload-hex", PAYLOAD.hex(), "--json"]) == 0
    defaults = radio.call_args.kwargs
    assert defaults["tx_invert_sync"] is False and defaults["reply_invert_sync"] is False
    assert duplex_cli_main(["--sync", TDD_SYNC.hex(), "--tx-invert-sync", "--reply-invert-sync",
                            "--payload-hex", PAYLOAD.hex(), "--json"]) == 0
    forwarded = radio.call_args.kwargs
    assert forwarded["tx_invert_sync"] is True and forwarded["reply_invert_sync"] is True
    assert forwarded["sync_word"] == TDD_SYNC.hex()


def test_session_control_forwards_per_request_polarity_and_default():
    transceiver = MagicMock()
    transceiver.sync_diagnostics.return_value = {"state": "locked", "error": None}
    transceiver.send_and_wait_reply.return_value = TDDExchangeResult(
        True, PAYLOAD, 0, 0, TDD_FREQUENCY, tx_completed=True)
    requests = [{"request_id": "default", "payload_hex": PAYLOAD.hex()},
                {"request_id": "flipped", "payload_hex": PAYLOAD.hex(),
                 "invert_sync": True, "reply_invert_sync": True},
                {"request_id": "plain", "payload_hex": PAYLOAD.hex(),
                 "invert_sync": False, "reply_invert_sync": False}]
    stream = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
    output = io.StringIO()
    # 会话默认同时反转发送和回复：缺省请求沿用默认，显式 false 覆盖默认。
    args = SimpleNamespace(timeout=1, sync_timeout=5, tx_invert_sync=True, reply_invert_sync=True)
    assert run_session(transceiver, args, UIEventEmitter(output, True), stream) == 0
    calls = transceiver.send_and_wait_reply.call_args_list
    assert [call.kwargs["invert_sync"] for call in calls] == [True, True, False]
    assert [call.kwargs["reply_invert_sync"] for call in calls] == [True, True, False]
    results = [json.loads(line)["payload"] for line in output.getvalue().splitlines()
               if json.loads(line)["type"] == "duplex.result"]
    assert [result["reply_invert_sync"] for result in results] == [True, True, False]


def test_webui_preflight_exposes_effective_polarity_words():
    registry = CommandRegistry()
    values = dict(session=True, sync=BASE, tx_invert_sync=True, reply_invert_sync=True)
    argv = registry.build_cli_argv("duplex", values)
    assert "--tx-invert-sync" in argv and "--reply-invert-sync" in argv
    planned = preflight("duplex", argv)
    assert planned["sync"] == BASE
    assert planned["tx_invert_sync"] is True and planned["reply_invert_sync"] is True
    assert planned["tx_sync_word"] == planned["reply_sync_word"] == INVERTED
    json.dumps(planned, allow_nan=False)
    plain = preflight("duplex", registry.build_cli_argv("duplex", dict(session=True, sync=BASE)))
    assert "--tx-invert-sync" not in registry.build_cli_argv("duplex", dict(session=True, sync=BASE))
    assert plain["tx_invert_sync"] is False and plain["tx_sync_word"] == BASE
    assert plain["reply_sync_word"] == BASE
    with pytest.raises(ValueError, match="回复反转需要明确的基准同步字"):
        preflight("duplex", registry.build_cli_argv("duplex", dict(session=True, reply_invert_sync=True)))
