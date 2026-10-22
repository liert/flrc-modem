"""End-to-end request/reply checks with a fake UHD device; no RF emission."""
import json
import io
import queue
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

from flrc_modem.duplex_cli import make_argument_parser, main as duplex_cli_main
from flrc_modem.encoding import EncodeContext, load_encoder
from flrc_modem.encoding.input import read_message
from flrc_modem.parsing.api import load_parser
from flrc_modem.models import DecodedFrame, PhysicalPacket
from flrc_modem.sdr.usrp import USRPDevice
from flrc_modem.sync import LiveTDDTransceiver, SyncState, TDDExchangeResult, TDDPhase
from flrc_modem.sync.transceiver import PendingReply
from usrp_fake import FakeUHDLibrary

FREQUENCY = 2471660000
SYNC = bytes.fromhex("ecee6698")


def frame(message, time_s):
    raw = load_encoder("dk32s")(message, EncodeContext(0, 0, 2500000))
    return DecodedFrame(time_s, FREQUENCY, 0, -20, 1, 0,
                        PhysicalPacket(SYNC, b"", bytes(raw), b"", 0))


class TransceiverTests(unittest.TestCase):
    def make_trx(self, sync="ecee6698", **settings):
        lib = FakeUHDLibrary()
        dev = USRPDevice(lib)
        dev.open()
        self.addCleanup(dev.close)
        trx = LiveTDDTransceiver(usrp_device=dev, sync_word=sync,
                                  hopping_table=[FREQUENCY] * 20, **settings)
        dev.get_time_estimate = MagicMock(return_value=100.5)
        sm = trx.session.state_machine
        for i in range(3):
            sm.fhss.observe(100 + i * .25, 100000 + i * 2500)
        sm.state = SyncState.LOCKED
        sm.last_anchor_hw = 100.5
        return trx, lib

    def request(self):
        return {"direction":"ground_to_air", "command": "0x23", "sequence":23,
                "need_ack":True, "fields":{"data_hex":""}}

    def test_linked_packet_and_waveform_settings_reach_real_tx_path(self):
        from flrc_modem.phy.packet_format import PacketFormat
        from flrc_modem.phy.encoder import encode_packet
        from flrc_modem.modulation import modulate_bits
        trx, _ = self.make_trx(packet_format=PacketFormat(header_type='variable',
                                                        crc_bytes=3, crc_initial=0xffffff),
                               amplitude=.4, ramp_time_us=20)
        self.assertEqual(trx.session.demod_config.header_type, 'variable')
        self.assertEqual(trx.session.demod_config.crc_bytes, 3)
        self.assertEqual(trx.session.demod_config.crc_initial, 0xffffff)
        with patch('flrc_modem.sync.transceiver.encode_packet', wraps=encode_packet) as encoder, \
             patch('flrc_modem.sync.transceiver.modulate_bits', wraps=modulate_bits) as modulator:
            result = trx.send_and_wait_reply(bytes.fromhex('112233445566'), wait_reply=False)
        self.assertTrue(result.success, result.error)
        self.assertEqual(encoder.call_args.kwargs['header_type'], 'variable')
        self.assertEqual(encoder.call_args.kwargs['crc_bytes'], 3)
        self.assertEqual(encoder.call_args.kwargs['crc_initial'], 0xffffff)
        self.assertEqual(modulator.call_args.args[1].amplitude, .4)
        self.assertEqual(modulator.call_args.args[1].ramp_time_us, 20)

    def test_reacquisition_blocks_transmit_even_with_a_locked_snapshot(self):
        trx, _ = self.make_trx()
        trx.session.timed_hopping = True
        trx.session._reacquiring = True
        with self.assertRaisesRegex(ValueError, "同步已失效"):
            trx._plan_window(100.5, .001)
        with patch("flrc_modem.sync.transceiver.time") as clock:
            clock.monotonic.side_effect = [0, 0, 1]
            self.assertFalse(trx.wait_locked(.5))

    def test_diagnostics_distinguishes_receive_frequency_from_search_placeholder(self):
        trx, _ = self.make_trx()
        trx.session.state_machine.reset_clock("simulated timeout")
        trx.session._reacquiring = True
        trx.session.stats.reacquisition_count = 2
        trx.session._tunes.append((100.4, FREQUENCY + 1000000))
        info = trx.sync_diagnostics()
        self.assertEqual(info["state"], "searching")
        self.assertEqual(info["frequency_hz"], FREQUENCY)
        self.assertEqual(info["receive_frequency_hz"], FREQUENCY + 1000000)
        self.assertTrue(info["reacquiring"])
        self.assertEqual(info["reacquisition_count"], 2)

    def test_parser(self):
        args = make_argument_parser().parse_args(["--payload-hex","010203040506"])
        self.assertEqual(args.payload_hex,"010203040506")
        self.assertIsNone(args.sync)
        self.assertEqual(args.sync_timeout,5.0)
        self.assertEqual(make_argument_parser().parse_args(["--sync-timeout","30"]).sync_timeout,30.0)

    def test_sync_timeout_rejects_nonfinite_or_nonpositive_values(self):
        for value in ("0","-1","nan","inf","-inf"):
            with self.subTest(value=value), patch("sys.stderr"), self.assertRaises(SystemExit):
                make_argument_parser().parse_args(["--sync-timeout",value])

    def test_weak_signal_sparse_anchors_can_lock_after_five_seconds(self):
        for timeout,expected in ((5,False),(30,True)):
            with self.subTest(timeout=timeout):
                trx,_=self.make_trx()
                sm=trx.session.state_machine
                sm.on_discontinuity()
                clock=[0]
                def tick(_):
                    clock[0]+=1
                    if clock[0] in (1,4,8):
                        message={"direction":"ground_to_air","command":"0x11",
                                 "timestamp_ticks":(10+clock[0])*10000,
                                 "fields":{"channels_raw":[1024]*16}}
                        f=frame(message,100+clock[0])
                        sm.on_frame(f.time_s,f)
                trx.device.get_time_estimate=lambda:100+clock[0]
                fake_time=MagicMock()
                fake_time.monotonic.side_effect=lambda:clock[0]
                fake_time.sleep.side_effect=tick
                with patch("flrc_modem.sync.transceiver.time",fake_time):
                    self.assertEqual(trx.wait_locked(timeout=timeout),expected)

    @patch("flrc_modem.duplex_cli.LiveTDDTransceiver")
    def test_sync_timeout_is_forwarded_in_all_cli_modes(self,cls):
        for mode in ("oneshot","stdin","repl"):
            with self.subTest(mode=mode):
                instance=cls.return_value
                instance.reset_mock()
                instance.__enter__.return_value=instance
                instance.last_snapshot=None
                instance.session.last_snapshot=None
                instance.wait_locked.return_value=True
                instance.send_and_wait_reply.return_value=TDDExchangeResult(True,b"123456",100.6,17,FREQUENCY,tx_completed=True)
                args=["--sync-timeout","30","--json"]
                if mode=="oneshot":
                    args += ["--payload-hex","010203040506"]
                elif mode=="stdin":
                    args += ["--stdin-jsonl"]
                with patch("sys.stdout"), patch("sys.stdin",io.StringIO("010203040506\n")), \
                     patch("builtins.input",side_effect=["010203040506","exit"]):
                    self.assertEqual(duplex_cli_main(args),0)
                if mode!="repl":
                    instance.wait_locked.assert_called_once_with(timeout=30.0)
                self.assertEqual(instance.send_and_wait_reply.call_args.kwargs["sync_timeout"],30.0)

    @patch("flrc_modem.duplex_cli.LiveTDDTransceiver")
    def test_sync_failure_reports_frames_anchors_and_rx_errors(self,cls):
        instance=cls.return_value
        instance.__enter__.return_value=instance
        instance.wait_locked.return_value=False
        instance.sync_diagnostics.return_value={"state":"acquiring","decoded_frames":7,
            "anchor_count":2,"anchor_span_s":1.0,"overflow_count":0,"error":"RX stream timed out"}
        output=io.StringIO()
        with patch("sys.stderr",output):
            code=duplex_cli_main(["--sync-timeout","30","--payload-hex","010203040506"])
        self.assertEqual(code,1)
        instance.send_and_wait_reply.assert_not_called()
        self.assertIn("CRC有效帧=7",output.getvalue())
        self.assertIn("地面时间锚点=2",output.getvalue())
        self.assertIn("RX stream timed out",output.getvalue())

    def test_sync_diagnostics_distinguishes_frames_from_clock_anchors(self):
        trx,_=self.make_trx()
        trx.session.stats.decoded_frames=9
        info=trx.sync_diagnostics()
        self.assertEqual(info["decoded_frames"],9)
        self.assertEqual(info["anchor_count"],3)
        self.assertAlmostEqual(info["anchor_span_s"],.5)

    def test_sync_diagnostics_keeps_total_anchors_after_lock_is_lost(self):
        trx,_=self.make_trx()
        trx.session.state_machine.predict(110)
        info=trx.sync_diagnostics()
        self.assertEqual(info["anchor_count"],0)
        self.assertEqual(info["accepted_anchors_total"],3)
        self.assertIsNotNone(info["loss_reason"])

    def test_relay_template_updates_send_once_for_dict_and_json(self):
        message=read_message(Path(__file__).resolve().parents[1]/"templates/messages/dk32s_tx_rc_relay.json")
        original=deepcopy(message)
        parser=load_parser("dk32s")
        for use_json in (False, True):
            with self.subTest(use_json=use_json):
                trx,_=self.make_trx()
                sent=[]
                def send(samples,config,**kwargs):
                    sent.append(samples)
                    return len(samples)//2
                trx.device.transmit_burst=send
                payload=json.dumps(message) if use_json else message
                result=trx.send_and_wait_reply(payload,wait_reply=False)
                self.assertTrue(result.success,result.error)
                parsed=parser(DecodedFrame(0,FREQUENCY,0,0,0,0,
                                          PhysicalPacket(SYNC,b"",result.tx_payload,b"",0)))
                self.assertEqual(parsed["command"],0x54)
                self.assertEqual(parsed["sequence"],100)
                self.assertEqual(parsed["payload_hex"],"01")
                self.assertEqual(len(sent),1)
                self.assertEqual(trx._tx_frame_counter,1)
                self.assertEqual(message,original)

    def test_template_counter_advances_between_requests_not_replanning(self):
        trx,_=self.make_trx()
        message=read_message(Path(__file__).resolve().parents[1]/"templates/messages/dk32s_tx_rc_relay.json")
        clock=[100.5]
        trx.device.get_time_estimate=lambda:clock[0]
        prepare=trx.device.prepare_transmit
        first=[True]
        def slow_prepare(config):
            if first[0]:
                clock[0]+=.2
                first[0]=False
            return prepare(config)
        trx.device.prepare_transmit=slow_prepare
        parser=load_parser("dk32s")
        sequences=[]
        for _ in range(2):
            result=trx.send_and_wait_reply(message,wait_reply=False)
            self.assertTrue(result.success,result.error)
            sequences.append(parser(DecodedFrame(0,FREQUENCY,0,0,0,0,
                              PhysicalPacket(SYNC,b"",result.tx_payload,b"",0)))["sequence"])
        self.assertEqual(sequences,[100,101])

    def test_cli_invalid_template_returns_error_without_traceback(self):
        with patch("flrc_modem.duplex_cli.read_message",side_effect=ValueError("invalid updates")), \
             patch("flrc_modem.duplex_cli.LiveTDDTransceiver"), \
             patch("sys.stderr") as error:
            self.assertEqual(duplex_cli_main(["--message-file","templates/messages/dk32s_tx_rc_relay.json"]),2)
        self.assertIn("invalid updates", "".join(str(c) for c in error.write.call_args_list))

    def test_unsynced_send_rejected(self):
        trx, _ = self.make_trx()
        trx.session.state_machine.fhss.invalidate()
        trx.session.state_machine.state = SyncState.SEARCHING
        result = trx.send_and_wait_reply(self.request(), sync_timeout=.01)
        self.assertFalse(result.success)

    def test_stopped_plan_does_not_submit_a_burst_or_stop_the_session(self):
        trx, _ = self.make_trx()
        cancelled = threading.Event()
        cancelled.set()
        trx.device.transmit_burst = MagicMock()
        result = trx.send_and_wait_reply(self.request(), cancel_event=cancelled)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.tx_completed)
        trx.device.transmit_burst.assert_not_called()
        self.assertFalse(trx._stop_event.is_set())

    def test_plan_stop_after_hardware_submission_keeps_tx_confirmation_and_removes_waiter(self):
        trx, _ = self.make_trx()
        cancelled = threading.Event()
        def send(samples, config, **kwargs):
            cancelled.set()
            return len(samples) // 2
        trx.device.transmit_burst = send
        result = trx.send_and_wait_reply(self.request(), cancel_event=cancelled)
        self.assertTrue(result.cancelled)
        self.assertTrue(result.tx_completed)
        self.assertFalse(result.reply_matched)
        self.assertEqual(trx._pending_waiters, [])
        self.assertFalse(trx._stop_event.is_set())

    def test_send_and_match_actual_dispatch(self):
        trx, _ = self.make_trx()
        trx._last_air_sequence = 100
        def send(samples, config, target_hw_time_s, timeout):
            # Test the real dispatcher: unrelated traffic cannot wake the waiter.
            tx_end = target_hw_time_s + len(samples)/2/trx.session.sample_rate
            wrong = frame({"direction":"air_to_ground","command":"0x23","sequence":100,
                           "is_ack":True,"fields":{"data_hex":""}}, tx_end+.0008)
            good = frame({"direction":"air_to_ground","command":"0x23","sequence":101,
                          "is_ack":True,"fields":{"data_hex":""}}, tx_end+.001)
            trx.session.events = lambda: iter([(wrong,None),(good,None)])
            trx._dispatch_loop()
            return len(samples)//2
        trx.device.transmit_burst = send
        result = trx.send_and_wait_reply(self.request(), reply_timeout=.01)
        self.assertTrue(result.success, result.error)
        self.assertTrue(result.tx_completed)
        self.assertTrue(result.reply_matched)
        self.assertEqual(result.rx_reply.packet.payload,
                         frame({"direction":"air_to_ground","command":"0x23","sequence":101,
                                "is_ack":True,"fields":{"data_hex":""}},0).packet.payload)

    def test_zero_send_is_failure(self):
        trx, _ = self.make_trx()
        trx.device.transmit_burst = MagicMock(return_value=0)
        result = trx.send_and_wait_reply(self.request(), wait_reply=False)
        self.assertFalse(result.success)
        self.assertFalse(result.tx_completed)

    def test_reply_timeout_is_not_exchange_success(self):
        trx, _ = self.make_trx()
        result = trx.send_and_wait_reply(self.request(), reply_timeout=.001)
        self.assertFalse(result.success)
        self.assertTrue(result.tx_completed)
        self.assertFalse(result.reply_matched)

    def test_busy_explicit_slot_rejected(self):
        trx, _ = self.make_trx()
        # hardware 100.7018 -> remote 10.7018 -> start of ground packet
        with self.assertRaisesRegex(ValueError, "TDD"):
            trx._plan_window(100.5, .001, target_hw_time=100.7018)

    def test_planner_covers_entire_exchange(self):
        trx, _ = self.make_trx()
        t,f,slot,end=trx._plan_window(100.5,.003)
        sm=trx.session.state_machine
        pred=sm.predict(t)
        w=sm.tdd.classify_phase(t,pred.slot_start_hw_time,sm.last_ground_tx_hw)
        self.assertEqual(w.phase,TDDPhase.IDLE)
        self.assertLess(t+.003+sm.tdd.estimated_turnaround_s+sm.tdd.air_duration_s,end)
        self.assertEqual(f,FREQUENCY)

    def test_slow_prepare_replans_deadline(self):
        trx, _ = self.make_trx()
        clock=[100.5]
        trx.device.get_time_estimate=lambda:clock[0]
        original=trx.device.prepare_transmit
        first=[True]
        def prepare(config):
            if first[0]:
                clock[0]+=.2
                first[0]=False
            return original(config)
        trx.device.prepare_transmit=prepare
        result=trx.send_and_wait_reply(self.request(),wait_reply=False)
        self.assertTrue(result.success,result.error)
        self.assertGreaterEqual(result.tx_hw_time,clock[0]+.1)

    def test_missing_sync_and_learning(self):
        trx,_=self.make_trx(sync=None)
        result=trx.send_and_wait_reply(self.request(),wait_reply=False)
        self.assertFalse(result.success)
        trx.session.state_machine.sync_word="ecee6698"
        result=trx.send_and_wait_reply(self.request(),wait_reply=False)
        self.assertTrue(result.success,result.error)

    def test_reply_filters_echo_direction_sync_frequency_command_sequence_time(self):
        trx,_=self.make_trx()
        req=frame(self.request(),100.6)
        waiter=PendingReply(100.6,100.606,FREQUENCY,SYNC,req.packet.payload,0x23,23,queue.Queue(1))
        good_message={"direction":"air_to_ground","command":"0x23","sequence":23,
                      "is_ack":True,"fields":{"data_hex":""}}
        self.assertFalse(trx._matches_reply(req,waiter))
        self.assertTrue(trx._matches_reply(frame(good_message,100.601),waiter))
        self.assertFalse(trx._matches_reply(frame(good_message,100.607),waiter))
        for key,value in [("sequence",24),("command","0x21"),("direction","ground_to_air")]:
            message={**good_message,key:value}
            self.assertFalse(trx._matches_reply(frame(message,100.601),waiter))
        from dataclasses import replace
        self.assertFalse(trx._matches_reply(replace(frame(good_message,100.601),frequency_hz=2480000000),waiter))
        good=frame(good_message,100.601)
        self.assertFalse(trx._matches_reply(replace(good,packet=replace(good.packet,sync_word=b"1234")),waiter))

    @patch("flrc_modem.duplex_cli.LiveTDDTransceiver")
    def test_cli_oneshot(self, cls):
        instance=cls.return_value
        instance.__enter__.return_value=instance
        instance.wait_locked.return_value=True
        instance.send_and_wait_reply.return_value=TDDExchangeResult(True,b"123456",100.6,17,FREQUENCY,tx_completed=True)
        with patch("sys.stdout"):
            self.assertEqual(duplex_cli_main(["--payload-hex","010203040506","--json"]),0)
        instance.send_and_wait_reply.assert_called_once_with("010203040506",wait_reply=True,reply_timeout=1.0,sync_timeout=5.0)

    def test_independent_air_sequence_wrap_rejects_replay(self):
        trx,_=self.make_trx()
        req=frame(self.request(),100.6)
        waiter=PendingReply(100.6,100.606,FREQUENCY,SYNC,req.packet.payload,0x23,23,
                            queue.Queue(1),independent_sequence=True,previous_air_sequence=32767)
        message={"direction":"air_to_ground","command":"0x23","sequence":0,
                 "is_ack":True,"fields":{"data_hex":""}}
        self.assertTrue(trx._matches_reply(frame(message,100.601),waiter))
        self.assertFalse(trx._matches_reply(frame({**message,"sequence":32767},100.601),waiter))
        self.assertFalse(trx._matches_reply(frame({**message,"sequence":32766},100.601),waiter))
