"""测试 FHSS + TDD 同步可视化 API (preview-schedule, parse-timeline)。"""
import json
import pytest
from httpx import ASGITransport, AsyncClient

from webui.backend.app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_sync_preview_schedule_default():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/v1/sync/preview-schedule", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["total_slots"] == 20
        assert data["slot_dwell_ms"] == 50.0
        assert data["cycle_duration_ms"] == 1000.0
        tb = data["timing_breakdown"]
        assert tb["ground_tx_ms"] > 0
        assert tb["turnaround_guard_ms"] == 0.810
        assert tb["air_rx_ms"] > 0
        assert tb["slot_guard_ms"] > 0
        # Dwell should equal sum
        total_calc = tb["ground_tx_ms"] + tb["turnaround_guard_ms"] + tb["air_rx_ms"] + tb["slot_guard_ms"]
        assert abs(total_calc - 50.0) < 0.01

        sim = data["simulated_slots"]
        assert len(sim) == 40  # 2 cycles * 20 slots
        assert sim[0]["slot_index"] == 0
        assert sim[0]["state"] == "searching"
        assert sim[2]["state"] == "locked"


@pytest.mark.anyio
async def test_sync_preview_schedule_custom():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/sync/preview-schedule",
            json={
                "frequencies": [2410000000, 2430000000, 2450000000],
                "dwell_ms": 25.0,
                "coding_rate": "1/2",
                "payload_bytes": 16,
                "cycles": 1,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_slots"] == 3
        assert data["slot_dwell_ms"] == 25.0
        assert len(data["simulated_slots"]) == 3
        assert data["simulated_slots"][0]["frequency_hz"] == 2410000000
        assert data["simulated_slots"][1]["frequency_hz"] == 2430000000


@pytest.mark.anyio
async def test_sync_parse_timeline(tmp_path):
    # Prepare dummy sync_frames.jsonl
    test_file = tmp_path / "sync_frames.jsonl"
    line1 = {
        "time_s": 0.0512,
        "cfo_hz": -120.5,
        "sample_power_db": -42.1,
        "sync": {
            "state": "locked",
            "confidence": 0.96,
            "slot_index": 2,
            "frequency_hz": 2418060000,
            "tdd_phase": "ground_tx",
            "clock_drift_ppm": 11.2,
            "hw_time_s": 0.0512,
        },
        "application": {"direction": "ground_to_air", "sequence": 1},
    }
    line2 = {
        "time_s": 0.0535,
        "cfo_hz": -118.0,
        "sample_power_db": -45.0,
        "sync": {
            "state": "locked",
            "confidence": 0.98,
            "slot_index": 2,
            "frequency_hz": 2418060000,
            "tdd_phase": "air_rx",
            "clock_drift_ppm": 10.8,
            "hw_time_s": 0.0535,
        },
        "application": {"direction": "air_to_ground", "sequence": 1},
    }
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(line1) + "\n" + json.dumps(line2) + "\n")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/sync/parse-timeline",
            json={"file_path": str(test_file)},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_frames"] == 2
        assert data["state_counts"]["locked"] == 2
        assert data["tdd_counts"]["ground_tx"] == 1
        assert data["tdd_counts"]["air_rx"] == 1
        assert len(data["drift_series"]) == 2
        assert data["latest_snapshot"]["clock_drift_ppm"] == 10.8


@pytest.mark.anyio
async def test_sync_parse_timeline_not_found():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/sync/parse-timeline",
            json={"file_path": "non_existent_timeline_file_xyz.jsonl"},
        )
        assert res.status_code == 404
