"""Regression coverage for the live timestamp/DSP/UHD integration."""
import ctypes as C
from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import MagicMock

import numpy as np

from flrc_modem.encoding import EncodeContext, load_encoder
from flrc_modem.io.iq import encode_samples
from flrc_modem.modulation import ModulationConfig, modulate_bits
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.sdr.usrp import USRPConfig, USRPDevice, UHDError
from flrc_modem.sync import LiveSyncSession, HWTimestampExtractor, SyncState, USRPTimedScheduler
from flrc_modem.sdr.usrp.abi import UHDRxMetadataErrorCode
from usrp_fake import FakeUHDLibrary, _set_val
from flrc_modem.sync.usrp_scheduler import LateTuneError


class TimestampRegressionTests(unittest.TestCase):
    def test_old_window_is_not_moved_by_new_rx_metadata(self):
        clock = HWTimestampExtractor(2500000)
        clock.on_metadata(True,100,0,250000)
        clock.on_metadata(True,100,.1,4096)
        self.assertAlmostEqual(clock.window_time(0,10000)[0],100.0)
        self.assertAlmostEqual(clock.window_time(250000,1000)[0],100.1)

    def test_unequal_blocks_without_metadata_advance_previous_block_length(self):
        clock = HWTimestampExtractor(2000000)
        clock.on_metadata(True,100,.25,1000)
        ts = clock.on_metadata(False,0,0,100)
        self.assertAlmostEqual(ts.time_s,100.2505)

    def test_windows_crossing_hardware_gap_are_rejected(self):
        clock=HWTimestampExtractor(2000000)
        clock.on_metadata(True,100,0,1000)
        clock.on_metadata(False,0,0,0,UHDRxMetadataErrorCode.OVERFLOW)
        clock.on_metadata(True,100,.01,1000)
        self.assertIsNone(clock.window_time(500,1000))
        recovered=clock.window_time(1000,500)
        self.assertAlmostEqual(recovered[0],100.01)
        self.assertEqual(recovered[1],1)

    def test_unanchored_samples_after_overflow_are_not_extrapolated(self):
        clock=HWTimestampExtractor(2000000)
        clock.on_metadata(True,100,0,1000)
        clock.on_metadata(False,0,0,0,UHDRxMetadataErrorCode.OVERFLOW)
        clock.on_metadata(False,0,0,100)
        self.assertIsNone(clock.window_time(1000,50))
        clock.on_metadata(True,100,.01,1000)
        self.assertAlmostEqual(clock.window_time(1100,500)[0],100.01)


class LiveDSPRegressionTests(unittest.TestCase):
    def replay(self,requested_rate,actual_rate):
        session=LiveSyncSession(sample_rate=requested_rate,sync_word="ecee6698")
        session._on_rate_configured(actual_rate)
        encoder=load_encoder("dk32s")
        iq=np.zeros(round(actual_rate*.65),np.complex64)
        for i,t in enumerate((.01,.26,.51)):
            message={"direction":"ground_to_air","command":"0x11","timestamp_ticks":100000+round(t*10000),
                     "fields":{"channels_raw":[1024]*16}}
            raw=encoder(message,EncodeContext(i,0,actual_rate))
            packet=encode_packet(raw,sync_word=bytes.fromhex("ecee6698"),coding_rate="3/4")
            wave=modulate_bits(packet.bits,ModulationConfig(sample_rate=actual_rate,offset_hz=0))
            start=round(t*actual_rate)
            iq[start:start+len(wave)]=wave
        # RX has already advanced to the end when the consumer begins.
        half=round(actual_rate*.3)
        session._metadata_callback(True,100,0,half,0)
        session._metadata_callback(True,100,half/actual_rate,len(iq)-half,0)
        session.window_queue.write(encode_samples(iq,"cs8"))
        session.window_queue.finish()
        session.done_event.set()
        session._dsp_loop()
        self.assertEqual(session.stats.decoded_frames,3)
        self.assertEqual(len(session.state_machine.fhss.anchors),3)
        self.assertEqual(session.state_machine.state,SyncState.LOCKED)
        self.assertAlmostEqual(session.state_machine.fhss.anchors[0][0],100.010012,delta=.0001)
        return session

    def test_real_modem_dsp_parses_sparse_clock_packets(self):
        self.replay(2500000,2500000)

    def test_coerced_rate_rebuilds_decoder_and_window_geometry(self):
        rate=125000000/62
        session=self.replay(2000000,rate)
        self.assertEqual(session.window_queue.step_bytes//2,round(.01*rate))

    def test_scheduler_advances_without_new_decoded_frames(self):
        lib=FakeUHDLibrary()
        with USRPDevice(lib) as dev:
            session=LiveSyncSession(usrp_device=dev,timed_hopping=True)
            session._scheduler=USRPTimedScheduler(dev,command_lead_time_s=.01)
            schedule=session._scheduler.schedule_rx_hop
            first=[True]
            def delayed(*args,**kwargs):
                if first[0]:
                    first[0]=False
                    raise LateTuneError("simulated slow control operation")
                return schedule(*args,**kwargs)
            session._scheduler.schedule_rx_hop=delayed
            now=[100.5]
            dev.get_time_estimate=lambda:now[0]
            sm=session.state_machine
            for i in range(3):
                sm.fhss.observe(100+i*.25,100000+i*2500)
            sm.last_anchor_hw=100.5
            sm.state=SyncState.LOCKED
            iterations=[0]
            def advance(_):
                now[0]+=.05
                iterations[0]+=1
                if iterations[0]>=5:
                    session._schedule_stop.set()
            session._schedule_stop.wait=advance
            session._schedule_loop()
            times=[c.scheduled_hw_time for c in session._scheduler.scheduled_hops]
            self.assertGreaterEqual(len(times),4)
            self.assertEqual(times,sorted(set(times)))
            self.assertEqual(session.stats.decoded_frames,0)
            self.assertEqual(session.stats.late_tune_skips,1)
            self.assertIsNone(session.stats.error)


class ReacquisitionRegressionTests(unittest.TestCase):
    def session(self):
        dev = USRPDevice(FakeUHDLibrary())
        dev.open()
        self.addCleanup(dev.close)
        now = [100.5]
        dev.get_time_estimate = lambda: now[0]
        frequencies = [2419260000 + i * 1000000 for i in range(20)]
        session = LiveSyncSession(usrp_device=dev, timed_hopping=True,
                                  frequency_hz=frequencies[0], hopping_table=frequencies)
        session._scheduler = USRPTimedScheduler(dev, command_lead_time_s=.01, settling_s=.0005)
        sm = session.state_machine
        for i in range(3):
            sm.fhss.observe(100 + i * .25, 100000 + i * 2500)
        sm.state = SyncState.LOCKED
        sm.last_anchor_hw = 100.5
        session._schedule_step()
        return session, now

    def anchor(self, session, hw, ticks):
        frame = SimpleNamespace(
            packet=SimpleNamespace(sync_word=b"TEST", payload=b"123456"),
            application={"direction": "ground_to_air", "timestamp_ticks": ticks, "sum8_ok": True})
        return session.state_machine.on_frame(hw, frame)

    def relock(self, session, now):
        # A fresh remote clock epoch must be accepted after the old fit is discarded.
        start = session._reacquire_until_hw + .01
        for i in range(3):
            self.assertTrue(self.anchor(session, start + i * .25, 20000 + i * 2500),
                            session.state_machine.last_rejection)
        now[0] = start + .5
        self.assertEqual(session.state_machine.state, SyncState.LOCKED)
        self.assertIsNone(session.state_machine.loss_reason)
        self.assertFalse(session.clock_ready_for_transmit)
        session._schedule_step()
        self.assertTrue(session.clock_ready_for_transmit)
        self.assertFalse(session._reacquiring)

    def test_timeout_parks_then_relocks_and_resumes_repeatedly(self):
        session, now = self.session()
        scheduler = session._scheduler
        first = scheduler.scheduled_hops[-1]
        self.assertNotEqual(first.frequency_hz, session.frequency_hz)
        for cycle in range(2):
            now[0] += 6
            session._schedule_step()
            parked = scheduler.scheduled_hops[-1]
            self.assertTrue(session._reacquiring)
            self.assertFalse(session.clock_ready_for_transmit)
            self.assertEqual(parked.frequency_hz, session.frequency_hz)
            self.assertGreater(parked.scheduled_hw_time, now[0] + scheduler.command_lead_time_s)
            self.assertEqual(session.state_machine.state, SyncState.SEARCHING)
            self.assertEqual(len(session.state_machine.fhss.anchors), 0)
            self.assertEqual(session.stats.reacquisition_count, cycle + 1)
            self.assertEqual(session.stats.overflow_count, 0)
            # Buffered frames from before the return cannot revive the old clock.
            self.assertFalse(self.anchor(session, now[0] - .1, 155000))
            self.assertFalse(self.anchor(session, parked.scheduled_hw_time, 155000))
            self.assertEqual(session.state_machine.last_rejection, "stale_after_loss")
            count = len(scheduler.scheduled_hops)
            now[0] = session._reacquire_until_hw + .001
            session._schedule_step()
            self.assertEqual(len(scheduler.scheduled_hops), count)
            self.assertEqual(session.receive_frequency_at(now[0]), session.frequency_hz)
            self.relock(session, now)
            self.assertGreater(len(scheduler.scheduled_hops), count)
        self.assertEqual(session.state_machine.fhss.accepted_count, 9)
        times = [c.scheduled_hw_time for c in scheduler.scheduled_hops]
        self.assertEqual(times, sorted(set(times)))

    def test_recovery_waits_for_already_queued_tunes(self):
        session, now = self.session()
        scheduler = session._scheduler
        pending_time = now[0] + 1
        pending_freq = session.hopping_table[2]
        scheduler.schedule_rx_hop(pending_freq, pending_time, 2)
        session._tunes.append((pending_time, pending_freq))
        session._last_hop_hw = pending_time
        session.state_machine.on_discontinuity("simulated loss")
        session._schedule_step()
        parked = scheduler.scheduled_hops[-1]
        self.assertGreater(parked.scheduled_hw_time, pending_time)
        self.assertEqual(session.receive_frequency_at(pending_time), pending_freq)
        self.assertEqual(session.receive_frequency_at(parked.scheduled_hw_time), session.frequency_hz)
        self.assertEqual(session.state_machine.overflow_count, 1)
        self.assertEqual(session.stats.reacquisition_count, 1)

    def test_relocked_generation_still_requires_return_before_hopping(self):
        session, now = self.session()
        sm = session.state_machine
        sm.on_discontinuity("clock epoch changed")
        for i in range(3):
            self.assertTrue(self.anchor(session, 100 + i * .25, 20000 + i * 2500))
        self.assertEqual(sm.state, SyncState.LOCKED)
        self.assertFalse(session.clock_ready_for_transmit)
        session._schedule_step()
        self.assertTrue(session._reacquiring)
        self.assertEqual(sm.state, SyncState.SEARCHING)
        self.assertEqual(session._scheduler.scheduled_hops[-1].frequency_hz, session.frequency_hz)

    def test_missed_recovery_deadline_can_be_retried(self):
        session, now = self.session()
        now[0] += 6
        scheduler = session._scheduler
        original = scheduler.schedule_rx_reacquisition
        scheduler.schedule_rx_reacquisition = MagicMock(side_effect=LateTuneError("network delay"))
        with self.assertRaises(LateTuneError):
            session._schedule_step()
        self.assertFalse(session.clock_ready_for_transmit)
        self.assertEqual(session.stats.reacquisition_count, 0)
        scheduler.schedule_rx_reacquisition = original
        session._schedule_step()
        self.assertTrue(session._reacquiring)
        self.assertEqual(session.stats.reacquisition_count, 1)


class UHDTransmitRegressionTests(unittest.TestCase):
    def device(self):
        lib=FakeUHDLibrary()
        dev=USRPDevice(lib)
        dev.open()
        self.addCleanup(dev.close)
        dev.get_time_estimate=MagicMock(return_value=100.5)
        return dev,lib

    def test_burst_reuses_streamer_and_waits_for_ack(self):
        dev,lib=self.device()
        cfg=USRPConfig(2471660000)
        dev.transmit_burst(b"\x01\x02"*100,cfg,100.7)
        dev.transmit_burst(b"\x01\x02"*100,cfg,100.9)
        self.assertEqual(sum(c[0]=="get_tx_stream" for c in lib.dll.calls),1)
        self.assertEqual(sum(c[0]=="set_tx_rate" for c in lib.dll.calls),1)
        self.assertIn(("get_tx_stream",b"fc32"),lib.dll.calls)

    def test_late_burst_rejected(self):
        dev,_=self.device()
        with self.assertRaisesRegex(UHDError,"deadline"):
            dev.transmit_burst(b"\0\0"*100,USRPConfig(2471660000),100.4)

    def test_digital_tx_retune_does_not_rebuild_or_reconfigure_rate(self):
        dev,lib=self.device()
        config=USRPConfig(2471660000,lo_frequency_hz=2450060000,bandwidth_hz=100000000)
        dev.transmit_burst(b"\x01\x02"*100,config,100.7)
        config=replace(config,frequency_hz=2411660000)
        dev.transmit_burst(b"\x01\x02"*100,config,100.9)
        self.assertEqual(sum(c[0]=="get_tx_stream" for c in lib.dll.calls),1)
        self.assertEqual(sum(c[0]=="set_tx_rate" for c in lib.dll.calls),1)
        self.assertEqual(len(lib.dll.tx_sent_bytes),400)

    def test_partial_burst_rejected(self):
        dev,lib=self.device()
        def partial(streamer,buf,count,md,timeout,out):
            _set_val(out,count//2)
            return 0
        lib.dll.uhd_tx_streamer_send=partial
        with self.assertRaisesRegex(UHDError,"Partial"):
            dev.transmit_burst(b"\0\0"*100,USRPConfig(2471660000),100.7)
        self.assertFalse(dev._burst_stream.value)

    def test_finite_capture_stops_at_hardware_end_after_missing_samples(self):
        lib=FakeUHDLibrary(rx_samples=b"\x01\x02"*4000)
        def timestamp(md,full,frac):
            start=lib.dll._last_rx_start
            t=100+start/2000000+(0.0005 if start>=1000 else 0)
            _set_val(full,int(t)); _set_val(frac,t-int(t))
            return 0
        lib.dll.uhd_rx_metadata_time_spec=timestamp
        with USRPDevice(lib) as dev:
            data=[]
            stats=dev.receive(data.append,USRPConfig(2471660000,sample_rate=2000000),5000,block_samples=1000)
        self.assertEqual(stats.sample_count,4000)

    def test_async_late_and_underflow_are_failures(self):
        for event in (2,8):
            with self.subTest(event=event):
                dev,lib=self.device()
                lib.dll.tx_async_event=event
                with self.assertRaisesRegex(UHDError,"async"):
                    dev.transmit_burst(b"\0\0"*100,USRPConfig(2471660000),100.7)

    def test_no_completion_ack_is_failure(self):
        dev,lib=self.device()
        def empty(streamer,md,timeout,valid):
            _set_val(valid,False)
            return 0
        lib.dll.uhd_tx_streamer_recv_async_msg=empty
        with self.assertRaisesRegex(UHDError,"ACK timeout"):
            dev._wait_burst_ack(C.c_void_p(1),.001)

    def test_late_and_duplicate_tunes(self):
        dev,_=self.device()
        scheduler=USRPTimedScheduler(dev,command_lead_time_s=.01,digital_only=True)
        with self.assertRaisesRegex(ValueError,"deadline"):
            scheduler.schedule_rx_hop(2471660000,100.4,17)
        a=scheduler.schedule_rx_hop(2471660000,100.6,17)
        self.assertIs(a,scheduler.schedule_rx_hop(2471660000,100.6,17))
        with self.assertRaisesRegex(ValueError,"ordered"):
            scheduler.schedule_rx_hop(2471660000,100.55,17)
        self.assertFalse(scheduler.evaluate_tuning_budget().is_safe)
