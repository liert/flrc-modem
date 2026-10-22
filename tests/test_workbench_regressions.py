"""Regressions found while auditing the scan/sync/modulation workbenches."""
import asyncio
import json
from threading import Event
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.scan.cancellation import ScanCancelled
from flrc_modem.scan.engine import SpectrumScanner
from webui.backend.app.api import router
from webui.backend.app.devices.manager import DeviceManager
from webui.backend.app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_scan_respects_other_device_owners_and_holds_lock_until_cleanup(monkeypatch):
    manager = DeviceManager()
    monkeypatch.setattr(router, "get_device_manager", lambda: manager)
    manager.acquire_device("hackrf", "spectrum-session")
    entered, cleaning, release = Event(), Event(), Event()

    def scan(scanner, **kwargs):
        assert manager.get_lock_info("hackrf")["run_id"].startswith("scan:")
        entered.set()
        assert scanner.cancel_event.wait(3)
        cleaning.set()
        assert release.wait(3)
        raise ScanCancelled("stopped")

    monkeypatch.setattr(SpectrumScanner, "scan_live_sdr", scan)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        busy = await client.post("/api/v1/scan/quick", json={"sdr": "hackrf"})
        assert busy.status_code == 409
        assert not entered.is_set()
        assert manager.get_lock_info("hackrf")["run_id"] == "spectrum-session"
        manager.release_device("spectrum-session")

        scan_id = str(uuid.uuid4())
        scanning = asyncio.create_task(client.post("/api/v1/scan/quick", json={"sdr": "hackrf", "scan_id": scan_id}))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            stopping = asyncio.create_task(client.post(f"/api/v1/scan/{scan_id}/stop"))
            assert await asyncio.to_thread(cleaning.wait, 3)
            assert manager.get_lock_info("hackrf") is not None
            assert not stopping.done()
        finally:
            release.set()
        assert (await asyncio.wait_for(stopping, 3)).json()["stopped"] is True
        assert (await asyncio.wait_for(scanning, 3)).json()["status"] == "cancelled"
        assert manager.get_lock_info("hackrf") is None


@pytest.mark.anyio
async def test_relative_workbench_paths_use_project_root_and_playback_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "PROJECT_ROOT", tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    (tmp_path / "capture.cs8").write_bytes(bytes(8192))
    timeline = tmp_path / "sync.jsonl"
    with timeline.open("w", encoding="utf-8") as stream:
        stream.write('null\n[]\ninvalid\n')
        for index in range(300):
            stream.write(json.dumps({"time_s": index, "sync": {"state": "locked", "slot_index": 0}}) + '\n')

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        scan = await client.post("/api/v1/scan/quick", json={"iq": "capture.cs8", "no_flrc": True})
        assert scan.status_code == 200
        exported = await client.post("/api/v1/scan/export-plan", json={"frequencies": [2405000000], "output_path": "configs/scan.toml"})
        assert exported.status_code == 200
        assert (tmp_path / "configs" / "scan.toml").is_file()
        parsed = await client.post("/api/v1/sync/parse-timeline", json={"file_path": "sync.jsonl"})
        assert parsed.status_code == 200
        data = parsed.json()
        assert data["total_frames"] == 300
        assert len(data["frames"]) == 100
        assert len(data["drift_series"]) == 200
        assert data["state_counts"]["locked"] == 300
        assert data["frames"][0]["time_s"] == 200


@pytest.mark.anyio
async def test_stop_that_overtakes_start_is_not_lost(monkeypatch):
    monkeypatch.setattr(router, "get_device_manager", lambda: DeviceManager())

    def scan(scanner, **kwargs):
        assert scanner.cancel_event.wait(3)
        raise ScanCancelled("stopped")

    monkeypatch.setattr(SpectrumScanner, "scan_live_sdr", scan)
    scan_id = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        stopping = asyncio.create_task(client.post(f"/api/v1/scan/{scan_id}/stop"))
        await asyncio.sleep(0.05)
        scanning = asyncio.create_task(client.post("/api/v1/scan/quick", json={"scan_id": scan_id, "sdr": "hackrf"}))
        assert (await asyncio.wait_for(stopping, 3)).json()["stopped"] is True
        assert (await asyncio.wait_for(scanning, 3)).json()["status"] == "cancelled"


@pytest.mark.anyio
@pytest.mark.parametrize("coding_rate", ["1", "3/4", "1/2"])
async def test_modulation_slices_and_waveform_cover_the_whole_packet(coding_rate):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/modulate/preview", json={"coding_rate": coding_rate})
        assert response.status_code == 200
        data = response.json()
        assert sum(section["bits"] for section in data["slices"]) == data["total_bits"]
        assert data["duration_us"] == round(data["rf_budget"]["total_samples"] / data["rf_budget"]["sample_rate"] * 1e6, 2)
        assert data["envelope_samples"][-1] < 0.01


@pytest.mark.anyio
async def test_invalid_preview_parameters_return_validation_errors():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for body in [{"bit_rate": 0}, {"sample_rate": 0}, {"amplitude": 2}]:
            assert (await client.post("/api/v1/modulate/preview", json=body)).status_code == 422
        # Individually valid values can still form an invalid sample/bitrate pair.
        assert (await client.post("/api/v1/modulate/preview", json={"sample_rate": 1000000})).status_code == 400
        assert (await client.post("/api/v1/sync/preview-schedule", json={"dwell_ms": 0})).status_code == 422
        assert (await client.post("/api/v1/sync/preview-schedule", json={"dwell_ms": 0.1})).status_code == 400


@pytest.mark.anyio
async def test_slow_modulation_does_not_block_scan_progress_requests(monkeypatch):
    from flrc_modem.modulation import waveform
    original = waveform.modulate_bits
    entered, release = Event(), Event()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(waveform, "modulate_bits", slow)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        preview = asyncio.create_task(client.post("/api/v1/modulate/preview", json={}))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            status = await asyncio.wait_for(client.get(f"/api/v1/scan/{uuid.uuid4()}/progress"), 1)
            assert status.status_code == 200
            assert not preview.done()
        finally:
            release.set()
        assert (await asyncio.wait_for(preview, 3)).status_code == 200
