"""Stopping a scan interrupts capture/analysis and waits for device cleanup."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock
import uuid

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.scan.cancellation import ScanCancelled
from flrc_modem.scan.engine import SpectrumScanner
from flrc_modem.scan.flrc_verifier import verify_flrc_candidate
from flrc_modem.scan.models import ScanReport
from flrc_modem.sdr.hackrf.streaming import StreamCancelled
from webui.backend.app.api.router import _active_scans
from webui.backend.app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("backend", ["hackrf", "usrp"])
def test_stop_capture_closes_device_without_starting_next_hop(monkeypatch, backend):
    scanner = SpectrumScanner()
    started = Event()
    device = MagicMock()
    device.__enter__.return_value = device

    def receive(sink, config, *, sample_count, cancel_event):
        started.set()
        assert cancel_event.wait(3), "scan did not forward cancellation to the device"
        if backend == "hackrf":
            raise StreamCancelled("cancelled")
        # USRP returns its partial capture when cancelled.

    device.receive.side_effect = receive
    module = f"flrc_modem.sdr.{backend}"
    monkeypatch.setattr(f"{module}.device.{'HackRFDevice' if backend == 'hackrf' else 'USRPDevice'}", lambda *a, **kw: device)
    monkeypatch.setattr(f"{module}.library.{'HackRFLibrary' if backend == 'hackrf' else 'UHDLibrary'}", MagicMock())

    with ThreadPoolExecutor() as executor:
        future = executor.submit(scanner.scan_live_sdr, sdr=backend)
        try:
            assert started.wait(3)
        finally:
            scanner.cancel()
        with pytest.raises(ScanCancelled):
            future.result(timeout=3)

    assert device.receive.call_count == 1
    device.__exit__.assert_called_once()


def test_stop_during_flrc_verification_skips_remaining_candidates(monkeypatch):
    scanner = SpectrumScanner()
    frontend = MagicMock()

    def process(*args):
        scanner.cancel()
        return None

    frontend.process.side_effect = process
    monkeypatch.setattr("flrc_modem.dsp.detector.Frontend", lambda cfg: frontend)
    with pytest.raises(ScanCancelled):
        verify_flrc_candidate(
            np.zeros(4096, dtype=np.complex64),
            sample_rate=10_000_000,
            center_frequency=2_405_000_000,
            target_frequency=2_406_000_000,
            bandwidth_hz=350_000,
            cancel_event=scanner.cancel_event,
        )
    assert frontend.process.call_count == 1


def test_stop_before_offline_analysis_skips_file_read(tmp_path, monkeypatch):
    scanner = SpectrumScanner()
    scanner.cancel()
    read = MagicMock()
    monkeypatch.setattr("pathlib.Path.open", read)
    with pytest.raises(ScanCancelled):
        scanner.scan_iq_file(tmp_path / "capture.cs8", 10_000_000, 2_405_000_000)
    read.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["offline", "hackrf", "usrp"])
async def test_stop_api_waits_for_cleanup_and_allows_restart(monkeypatch, tmp_path, mode):
    started, cleaning, allow_cleanup, finished = Event(), Event(), Event(), Event()

    def scan(scanner, **kwargs):
        started.set()
        assert scanner.cancel_event.wait(3)
        cleaning.set()
        assert allow_cleanup.wait(3)
        finished.set()
        raise ScanCancelled("stopped")

    method = "scan_iq_file" if mode == "offline" else "scan_live_sdr"
    monkeypatch.setattr(SpectrumScanner, method, scan)
    iq = tmp_path / "capture.cs8"
    iq.write_bytes(b"\x00\x00")
    scan_id = str(uuid.uuid4())
    parameters = {"iq": str(iq)} if mode == "offline" else {"sdr": mode}
    parameters["scan_id"] = scan_id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        scanning = asyncio.create_task(client.post("/api/v1/scan/quick", json=parameters))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            duplicate = await client.post("/api/v1/scan/quick", json=parameters)
            assert duplicate.status_code == 409
            stopping = asyncio.create_task(client.post(f"/api/v1/scan/{scan_id}/stop"))
            assert await asyncio.to_thread(cleaning.wait, 3)
            assert not stopping.done()
            assert not scanning.done()
        finally:
            allow_cleanup.set()

        stopped = await asyncio.wait_for(stopping, 3)
        result = await asyncio.wait_for(scanning, 3)
        assert stopped.status_code == 200
        assert stopped.json()["stopped"] is True
        assert finished.is_set()
        assert result.status_code == 200
        assert result.json() == {"status": "cancelled"}
        assert uuid.UUID(scan_id) not in _active_scans

        # Repeated stop requests are harmless after cleanup.
        repeated = await client.post(f"/api/v1/scan/{scan_id}/stop")
        assert repeated.json()["stopped"] is False

        def completed(scanner, **kwargs):
            assert not scanner.cancel_event.is_set()
            return ScanReport("offline_iq", 2.4e9, 2.41e9, -70, 10)

        monkeypatch.setattr(SpectrumScanner, method, completed)
        restarted = await client.post("/api/v1/scan/quick", json=parameters)
        assert restarted.status_code == 200
        assert restarted.json()["total_channels"] == 0


@pytest.mark.anyio
async def test_cancelled_http_handler_stops_capture_thread(monkeypatch):
    started, finished = Event(), Event()

    def scan(scanner, **kwargs):
        started.set()
        assert scanner.cancel_event.wait(3)
        finished.set()
        raise ScanCancelled("stopped")

    monkeypatch.setattr(SpectrumScanner, "scan_live_sdr", scan)
    scan_id = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        scanning = asyncio.create_task(client.post("/api/v1/scan/quick", json={"scan_id": scan_id, "sdr": "hackrf"}))
        try:
            assert await asyncio.to_thread(started.wait, 3)
        finally:
            scanning.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(scanning, 3)
        assert finished.is_set()
        assert uuid.UUID(scan_id) not in _active_scans
