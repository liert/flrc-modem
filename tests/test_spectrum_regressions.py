"""Exercise actual SDR wrappers with fake native drivers; never opens hardware."""
import asyncio
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from flrc_modem.sdr.hackrf.device import HackRFDevice
from flrc_modem.sdr.usrp.device import USRPDevice
from hackrf_fake import FakeAPI
from usrp_fake import FakeUHDLibrary, _set_val
from webui.backend.app.devices.manager import DeviceManager
from webui.backend.app.spectrum.dsp import compute_spectrum, generate_test_tone_iq
from webui.backend.app.spectrum.manager import SpectrumManager
from webui.backend.app.spectrum.schemas import SpectrumStartRequest, SpectrumUpdateRequest
from webui.backend.app.spectrum.session import SpectrumSession


def wait_until(condition, timeout=2):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= end:
            raise AssertionError("condition timed out")
        time.sleep(.005)


@pytest.fixture
def sessions(monkeypatch):
    devices = DeviceManager()
    monkeypatch.setattr("webui.backend.app.spectrum.session.get_device_manager", lambda: devices)
    # Native-wrapper checks use only the locally patched fake device. Production
    # capture now spawns a process, which cannot inherit these monkeypatches.
    class FakeProcessReceiver:
        def __init__(self, **kwargs):
            self.options, self.device = kwargs, None

        def receive(self, on_chunk, config, *, cancel_event, on_open):
            from flrc_modem.sdr.hackrf import device as device_module
            self.device = device_module.HackRFDevice(**self.options)
            self.device.__enter__()
            on_open(self.device.selected_device)

            class Sink:
                def write(self, buffer):
                    data = bytes(buffer)
                    on_chunk(data, time.time() - len(data) / (2 * config.sample_rate), False, 0)
                    return len(data)

            self.device.receive(Sink(), config, sample_count=None, cancel_event=cancel_event, timeout=None)

        def close(self):
            if self.device is not None:
                self.device.close()
                self.device = None

    monkeypatch.setattr("webui.backend.app.spectrum.session.HackRFSpectrumReceiver", FakeProcessReceiver)
    created = []

    def create(**kwargs):
        session = SpectrumSession(SpectrumStartRequest(**kwargs))
        created.append(session)
        return session, devices

    yield create
    for session in created:
        session.stop()


@pytest.mark.parametrize("values", [
    {"n_fft": 0}, {"n_fft": -1}, {"n_fft": 1000}, {"n_fft": 1048576},
    {"sample_rate": float("inf")}, {"center_freq": float("nan")},
    {"master_clock_rate": -1}, {"lna_gain": 3}, {"vga_gain": 65},
    {"serial": "a", "device_index": 0}, {"device_index": -1},
])
def test_invalid_config_rejected_before_open(values):
    with pytest.raises(ValidationError):
        SpectrumStartRequest(**values)


def test_low_float_iq_display_reference_is_valid_for_start_and_update():
    assert SpectrumStartRequest(sdr="usrp", ref_level_dbfs=-110).ref_level_dbfs == -110
    assert SpectrumUpdateRequest(ref_level_dbfs=-110).ref_level_dbfs == -110
    with pytest.raises(ValidationError):
        SpectrumUpdateRequest(ref_level_dbfs=-241)


def test_average_is_linear_power_not_average_decibels():
    iq = generate_test_tone_iq(8e6, 1024, 1e6, amplitude_dbfs=-20, noise_floor_dbfs=-160)
    previous = np.full(1024, -200.0)
    previous[640] = 0  # +1 MHz bin, prior full-scale tone
    frame = compute_spectrum(iq, sample_rate=8e6, previous_powers=previous, avg_weight=.5)
    assert frame['powers'][640] == pytest.approx(10 * np.log10(.505), abs=.06)


def test_real_hackrf_wrapper_preserves_zero_gains_and_continuous_timeout(sessions):
    api = FakeAPI(rx_data=b"\x10\xf0" * 262144, buffer_size=262144)
    with patch("flrc_modem.sdr.hackrf.device.HackRFDevice",
               side_effect=lambda **kwargs: HackRFDevice(api, **kwargs)), \
         patch("flrc_modem.sdr.hackrf.streaming.RxStream._wait", autospec=True) as wait:
        def receive_until_cancel(stream):
            assert stream.timeout is None
            wait_until(stream.cancel_event.is_set)
            from flrc_modem.sdr.hackrf.streaming import StreamCancelled
            raise StreamCancelled("test stop")
        wait.side_effect = receive_until_cancel
        session, devices = sessions(sdr="hackrf", lna_gain=0, vga_gain=0)
        session.start()
        assert session.device_id == api.serials[0]
        assert ("hackrf_set_lna_gain", 0) in api.calls
        assert ("hackrf_set_vga_gain", 0) in api.calls
        assert not devices.acquire_device("hackrf", "other")[0]
        session.stop()
        assert api.calls.index("stop_rx") < api.calls.index("close")
        assert devices.acquire_device("hackrf", "other")[0]
        devices.release_device("other")


def test_real_usrp_wrapper_reports_coerced_rate_and_selected_channel(sessions):
    lib = FakeUHDLibrary(rx_samples=b"\x10\xf0" * 32768 * 100)
    lib.dll.fixed_rx_rate = 2457600.0
    receive = lib.dll.uhd_rx_streamer_recv
    def slow_receive(*args):
        time.sleep(.01)
        return receive(*args)
    lib.dll.uhd_rx_streamer_recv = slow_receive
    lib.dll.uhd_usrp_get_rx_freq = lambda h, channel, out: (_set_val(out, lib.dll.rx_freq + 123) or 0)
    with patch("flrc_modem.sdr.usrp.device.USRPDevice",
               side_effect=lambda **kwargs: USRPDevice(lib, **kwargs)):
        session, devices = sessions(sdr="usrp", sample_rate=2500000, master_clock_rate=122880000,
                                    center_freq=2419260000, gain=0, channel=2)
        with pytest.warns(UserWarning, match="Coerced"):
            session.start()
        assert session.actual_sample_rate == 2457600
        assert session.actual_center_freq == 2419260123
        assert session.actual_master_clock == 122880000
        assert ("set_rx_gain", 0.0, 2) in lib.dll.calls
        assert sum(call[0] == "set_rx_rate" for call in lib.dll.calls) == 1
        wait_until(lambda: session._latest_frame is not None)
        assert session._latest_frame["sample_rate"] == 2457600
        assert session._latest_frame["timestamp_source"] == "uhd"
        with pytest.warns(UserWarning, match="Coerced"):
            session.update_parameters(SpectrumUpdateRequest(center_freq=2420000000))
        assert ("set_rx_freq", 2420000000, 2) in lib.dll.calls
        session.stop()
        assert not devices._locks
        assert not session._rx_thread.is_alive()


def test_start_failure_and_stream_failure_release_hardware(sessions):
    api = FakeAPI(fail_start=True)
    with patch("flrc_modem.sdr.hackrf.device.HackRFDevice",
               side_effect=lambda **kwargs: HackRFDevice(api, **kwargs)):
        session, devices = sessions(sdr="hackrf")
        with pytest.raises(RuntimeError, match="接收中断"):
            session.start()
        assert not devices._locks
        assert "close" in api.calls
        assert session.state == "error"


def test_midstream_native_failure_releases_device_without_user_stop(sessions):
    lib = FakeUHDLibrary(rx_samples=b"\x10\xf0" * 32768 * 10)
    failed = threading.Event()
    receive = lib.dll.uhd_rx_streamer_recv
    def recv(*args):
        time.sleep(.005)
        return 1 if failed.is_set() else receive(*args)
    lib.dll.uhd_rx_streamer_recv = recv
    with patch("flrc_modem.sdr.usrp.device.USRPDevice",
               side_effect=lambda **kwargs: USRPDevice(lib, **kwargs)):
        session, devices = sessions(sdr="usrp", sample_rate=2500000)
        session.start()
        failed.set()
        wait_until(lambda: session.state == "error" and not devices._locks)
        assert session.error_code == "STREAM_ERROR"
        assert session._device is None


def test_stop_timeout_retains_handle_and_device_lock(sessions):
    session, devices = sessions(sdr="hackrf")
    devices.acquire_device("hackrf", session.session_id)
    from unittest.mock import MagicMock
    session._device = MagicMock()
    worker = MagicMock()
    worker.is_alive.return_value = True
    session._rx_thread = worker
    with pytest.raises(RuntimeError, match="保留设备占用"):
        session.stop()
    session._device.close.assert_not_called()
    assert not devices.acquire_device("hackrf", "other")[0]
    session._rx_thread = None


def test_retune_restarts_receive_and_validates_before_stopping(sessions):
    session, _ = sessions(sdr="sim")
    session.start()
    old_worker = session._rx_thread
    response = session.update_parameters(SpectrumUpdateRequest(center_freq=2400000000))
    assert response["restarted"]
    assert not old_worker.is_alive()
    assert session.actual_center_freq == 2400000000
    wait_until(lambda: session._latest_frame is not None)
    assert session._latest_frame["center_freq"] == 2400000000
    assert session._latest_frame["discontinuity"]


def test_watchdog_stops_paused_sessions_and_does_not_replace_unstopped_session(monkeypatch):
    manager = SpectrumManager()
    manager.start_session(SpectrumStartRequest(sdr="sim"))
    session = manager.get_session()
    try:
        manager.pause_session(session.session_id)
        manager._last_subscriber_activity -= 16
        manager.check_watchdog()
        assert manager.get_session() is None
        assert not session._rx_thread.is_alive()
        manager.start_session(SpectrumStartRequest(sdr="sim"))
        active = manager.get_session()
        with patch.object(active, "stop", side_effect=RuntimeError("still receiving")):
            with pytest.raises(RuntimeError, match="still receiving"):
                manager.start_session(SpectrumStartRequest(sdr="sim"))
            assert manager.get_session() is active
    finally:
        manager.stop_session()


def test_thread_safe_bounded_async_delivery_and_pause(sessions):
    session, _ = sessions(sdr="sim")
    async def exercise():
        loop = asyncio.get_running_loop()
        loop.set_debug(True)
        subscriber = session.register_subscriber(loop)
        frame = {"session_id": session.session_id, "powers": [1]}
        producer = threading.Thread(target=lambda: [session._publish({**frame, "sequence": i}) for i in range(100)])
        producer.start()
        producer.join()
        result = await asyncio.wait_for(subscriber.get(), 1)
        assert result["sequence"] == 99
        assert subscriber.qsize() <= 2
        assert session.client_dropped_frames >= 99
        session.state = "running"
        session.pause_display()
        session._publish({**frame, "sequence": 100})
        await asyncio.sleep(.01)
        assert subscriber.empty()
        session.resume_display()
        result = await asyncio.wait_for(subscriber.get(), 1)
        assert result["sequence"] == 100
        assert result["discontinuity"]
        assert not session.accepts_display_frame({**frame, "sequence": 99})
        assert session.accepts_display_frame(result)
        session.unregister_subscriber(subscriber)
        assert not session.has_subscribers
    asyncio.run(exercise())


def test_hackrf_inactivity_is_distinct_from_total_runtime_limit(sessions):
    session, _ = sessions(sdr="hackrf")
    session._last_rx_activity = time.monotonic() - 6
    with pytest.raises(TimeoutError, match="未收到 IQ"):
        session._check_rx_health()
    session._enqueue_iq(b"\0\0" * 256, time.time())
    session._check_rx_health()


def test_periodic_watchdog_expires_half_open_connection():
    async def exercise():
        manager = SpectrumManager()
        manager.start_session(SpectrumStartRequest(sdr="sim"))
        session = manager.get_session()
        sub = session.register_subscriber(asyncio.get_running_loop())
        manager._last_subscriber_activity -= 31
        manager.start_watchdog()
        try:
            for _ in range(30):
                if manager.get_session() is None:
                    break
                await asyncio.sleep(.05)
            assert manager.get_session() is None
            assert not session._rx_thread.is_alive()
        finally:
            session.unregister_subscriber(sub)
            await manager.shutdown()
    asyncio.run(exercise())


def test_hardware_start_does_not_block_http_event_loop(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from webui.backend.app.main import app
    from webui.backend.app.spectrum.schemas import SpectrumStatusResponse
    from unittest.mock import Mock
    started, release = threading.Event(), threading.Event()
    manager = Mock()
    def start(_):
        started.set()
        release.wait(2)
        return SpectrumStatusResponse(state="running", session_id="test")
    manager.start_session.side_effect = start
    monkeypatch.setattr("webui.backend.app.api.router.get_spectrum_manager", lambda: manager)
    async def exercise():
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            request = asyncio.create_task(client.post('/api/v1/spectrum/start', json={'sdr': 'sim'}))
            try:
                assert await asyncio.to_thread(started.wait, 1)
                assert not request.done()
                health = await asyncio.wait_for(client.get('/health'), .5)
                assert health.status_code == 200
            finally:
                release.set()
                assert (await request).status_code == 200
    asyncio.run(exercise())


def test_websocket_sends_status_frames_and_cleans_subscription(monkeypatch, tmp_path):
    from webui.backend.app.main import app
    from webui.backend.app.storage.db import Database
    from webui.backend.app.jobs.manager import JobManager
    manager = SpectrumManager()
    monkeypatch.setattr("webui.backend.app.storage.db._DB", Database(tmp_path / 'spectrum.db'))
    monkeypatch.setattr("webui.backend.app.main.get_job_manager", lambda: JobManager())
    monkeypatch.setattr("webui.backend.app.spectrum.manager.get_spectrum_manager", lambda: manager)
    monkeypatch.setattr("webui.backend.app.api.router.get_spectrum_manager", lambda: manager)
    try:
        with TestClient(app) as client:
            response = client.post('/api/v1/spectrum/start', json={'sdr': 'sim'})
            assert response.status_code == 200, response.text
            session = manager.get_session()
            with client.websocket_connect(f'/ws/spectrum?session_id={session.session_id}') as socket:
                info = socket.receive_json()
                assert info['type'] == 'spectrum_status'
                assert info['state'] == 'running'
                frame = socket.receive_json()
                assert frame['session_id'] == session.session_id
                assert len(frame['powers']) == 1024
            wait_until(lambda: not session.has_subscribers)
            assert client.post('/api/v1/spectrum/pause', params={'session_id': session.session_id}).status_code == 200
            assert client.post('/api/v1/spectrum/stop', params={'session_id': session.session_id}).status_code == 200
            assert client.post('/api/v1/spectrum/start', json={'sdr': 'sim', 'n_fft': 0}).status_code == 422
    finally:
        manager.stop_session()
