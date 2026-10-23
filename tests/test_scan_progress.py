"""Progress becomes available before verification/capture finishes."""
import asyncio
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock
import uuid

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.scan.cancellation import ScanCancelled
from flrc_modem.scan.engine import SpectrumScanner
from flrc_modem.scan.models import ChannelCandidate, ScanReport
from webui.backend.app.main import app


def candidate(frequency):
    return {"frequency_hz": frequency, "bandwidth_hz": 350000, "peak_power_dbfs": -30,
            "snr_db": 40, "burst_count": 1, "duty_cycle": 0.2}


def test_energy_candidates_are_published_before_slow_flrc_verification(monkeypatch):
    scanner = SpectrumScanner()
    raw = [candidate(2404000000), candidate(2406000000)]
    monkeypatch.setattr("flrc_modem.scan.engine.detect_spectral_peaks", lambda *a, **kw: raw)
    snapshots = []

    def verify(*args, **kwargs):
        snapshot = scanner.progress
        snapshots.append(snapshot)
        assert snapshot["candidates_completed"] == len(snapshots) - 1
        assert snapshot["report"]["total_channels"] == 2
        assert snapshot["report"]["channels"][len(snapshots) - 1]["protocol_hint"] == "待 FLRC 检验"
        return {"is_flrc": True, "flrc_confidence": 1, "sync_words": ["4a65b21c"],
                "bit_rate": 325000, "protocol_hint": "FLRC (325k)", "burst_count": 1}

    monkeypatch.setattr("flrc_modem.scan.engine.verify_flrc_candidate", verify)
    result = scanner.scan_iq_samples(np.zeros(4096, dtype=np.complex64), 10000000, 2405000000)
    assert snapshots[0]["report"]["flrc_channels_count"] == 0
    assert snapshots[0]["report"]["device_info"]["partial"] is True
    assert snapshots[1]["report"]["flrc_channels_count"] == 1
    assert scanner.progress["stage"] == "completed"
    assert scanner.progress["percent"] == 100
    assert scanner.progress["report"] == result.to_dict()
    assert result.device_info["partial"] is False
    # Later verification does not mutate a snapshot already being read by HTTP.
    assert snapshots[0]["report"]["channels"][0]["sync_words"] == []


@pytest.mark.parametrize("backend", ["hackrf", "usrp"])
def test_completed_hops_visible_while_next_hop_is_capturing(monkeypatch, backend):
    scanner = SpectrumScanner()
    next_hop, allow_capture = Event(), Event()
    device = MagicMock()
    device.__enter__.return_value = device
    device.configure_rx.return_value = 10000000.0
    device.get_master_clock_rate.return_value = 125000000.0
    calls = 0

    def receive(sink, config, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            next_hop.set()
            assert allow_capture.wait(3)
        sink.write(bytes(8192))
        return SimpleNamespace(overflow_count=0)

    device.receive.side_effect = receive
    prefix = f"flrc_modem.sdr.{backend}"
    monkeypatch.setattr(f"{prefix}.device.{'HackRFDevice' if backend == 'hackrf' else 'USRPDevice'}", lambda *a, **kw: device)
    monkeypatch.setattr(f"{prefix}.library.{'HackRFLibrary' if backend == 'hackrf' else 'UHDLibrary'}", MagicMock())
    monkeypatch.setattr("flrc_modem.scan.engine.detect_spectral_peaks", lambda *a, **kw: [candidate(2407000000)])

    with ThreadPoolExecutor() as executor:
        scanning = executor.submit(scanner.scan_live_sdr, sdr=backend, start_freq=2400000000,
                                   stop_freq=2414000000, step_bandwidth=10000000, verify_flrc=False, passes=1)
        try:
            assert next_hop.wait(3)
            progress = scanner.progress
            assert not scanning.done()
            assert progress["stage"] == "capturing"
            assert progress["percent"] == 50
            assert progress["hops_completed"] == 1
            assert progress["hops_total"] == 2
            assert progress["report"]["scan_mode"] == f"live_sdr_{backend}"
            assert progress["report"]["start_freq_mhz"] == 2400
            assert progress["report"]["stop_freq_mhz"] == 2414
            assert progress["report"]["device_info"]["partial"] is True
            assert progress["report"]["channels"][0]["frequency_hz"] == 2407000000
        finally:
            allow_capture.set()
        result = scanning.result(timeout=3)

    assert len(result.channels) == 1  # Overlapping hops still merge the same carrier.
    assert result.channels[0].burst_count == 2
    assert scanner.progress["report"] == result.to_dict()
    assert scanner.progress["hops_completed"] == 2
    assert result.device_info["partial"] is False


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_requested_band_edges_are_inside_a_hops_usable_passband(monkeypatch):
    scanner = SpectrumScanner()
    device = MagicMock()
    device.__enter__.return_value = device
    device.receive.side_effect = lambda sink, *a, **kw: sink.write(bytes(8192))
    monkeypatch.setattr("flrc_modem.sdr.hackrf.device.HackRFDevice", lambda *a, **kw: device)
    monkeypatch.setattr("flrc_modem.sdr.hackrf.library.HackRFLibrary", MagicMock())
    edges = (2400000000, 2414000000)
    def detect(*args, **kwargs):
        center = kwargs['dc_center_freq']
        return [candidate(edge) for edge in edges if abs(edge - center) <= 4200000]
    monkeypatch.setattr("flrc_modem.scan.engine.detect_spectral_peaks", detect)
    result = scanner.scan_live_sdr('hackrf', start_freq=edges[0], stop_freq=edges[1],
                                   step_bandwidth=10000000, verify_flrc=False, passes=1)
    assert {channel.frequency_hz for channel in result.channels} == set(edges)


@pytest.mark.anyio
async def test_progress_api_exposes_partial_results_and_stop_retains_them(monkeypatch, caplog):
    started = Event()
    partial = ScanReport("live_sdr_hackrf", 2.4e9, 2.4835e9, -70, 10, channels=[
        ChannelCandidate(2407000000, 350000, -30, 40, -70, protocol_hint="待 FLRC 检验"),
    ])

    def scan(scanner, **kwargs):
        scanner._publish("verifying", 25, partial, candidates_completed=0, candidates_total=1)
        started.set()
        assert scanner.cancel_event.wait(3)
        raise ScanCancelled("stopped")

    monkeypatch.setattr(SpectrumScanner, "scan_live_sdr", scan)
    scan_id = str(uuid.uuid4())
    with caplog.at_level("INFO"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            scanning = asyncio.create_task(client.post("/api/v1/scan/quick", json={"scan_id": scan_id, "sdr": "hackrf"}))
            try:
                assert await asyncio.to_thread(started.wait, 3)
                progress = await client.get(f"/api/v1/scan/{scan_id}/progress")
                assert progress.status_code == 200
                assert progress.json()["active"] is True
                assert progress.json()["percent"] == 25
                assert progress.json()["report"] == partial.to_dict()
                unchanged = await client.get(f"/api/v1/scan/{scan_id}/progress", params={"after_revision": progress.json()["revision"]})
                assert unchanged.json()["report"] is None
                assert unchanged.json()["percent"] == progress.json()["percent"]
                assert not scanning.done()
            finally:
                await client.post(f"/api/v1/scan/{scan_id}/stop")
            result = await asyncio.wait_for(scanning, 3)
            assert result.json() == {"status": "cancelled", "report": partial.to_dict()}
            finished = await client.get(f"/api/v1/scan/{scan_id}/progress")
            assert finished.json()["active"] is False
            assert finished.json()["report"] is None

    events = [record.msg for record in caplog.records if isinstance(record.msg, dict)
              and record.msg.get("event") == "scan.run"]
    assert len(events) == 1
    assert events[0]["scan_id"] == scan_id
    assert events[0]["outcome"] == "cancelled"
    assert events[0]["progress_updates"] == 1
    assert events[0]["channels_found"] == 1
