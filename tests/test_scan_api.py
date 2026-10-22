"""WebUI 后端盲扫与信道感知 API 路由测试。"""
import json
from pathlib import Path
import tempfile
import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.io.iq import encode_samples
from flrc_modem.modulation import ModulationConfig, modulate_bits
from flrc_modem.phy.encoder import encode_packet
from webui.backend.app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_quick_scan_api_offline_iq():
    # 生成带 FLRC 突发的临时 IQ 文件
    sample_rate = 10_000_000.0
    center_freq = 2_405_000_000.0
    target_freq = 2_404_380_000.0
    offset_hz = target_freq - center_freq
    sync_word = bytes.fromhex("4a65b21c")

    packet = encode_packet(b"TEST_API_BURST_SCAN", sync_word=sync_word, coding_rate="3/4")
    flrc_burst = modulate_bits(packet.bits, ModulationConfig(sample_rate=sample_rate, bit_rate=325_000.0, offset_hz=offset_hz))

    import numpy as np
    num_samples = 40000
    iq = (np.random.normal(0, 0.003, num_samples) + 1j * np.random.normal(0, 0.003, num_samples)).astype(np.complex64)
    t = np.arange(len(flrc_burst), dtype=np.float64) / sample_rate
    iq[5000 : 5000 + len(flrc_burst)] += (flrc_burst * np.exp(2j * np.pi * offset_hz * t)).astype(np.complex64)

    with tempfile.NamedTemporaryFile(suffix=".cs8", delete=False) as tmp:
        tmp.write(encode_samples(iq, "cs8"))
        tmp_path = Path(tmp.name)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/v1/scan/quick", json={
                "iq": str(tmp_path),
                "sample_rate": sample_rate,
                "center_frequency": center_freq,
                "threshold_db": 10.0,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["scan_mode"] == "offline_iq"
            assert data["total_channels"] >= 1
            flrc_channels = [c for c in data["channels"] if c["is_flrc"]]
            assert len(flrc_channels) >= 1
            assert "4a65b21c" in flrc_channels[0]["sync_words"]
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.anyio
async def test_quick_scan_api_errors():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 缺少参数
        resp = await client.post("/api/v1/scan/quick", json={})
        assert resp.status_code == 400

        # 文件不存在
        resp2 = await client.post("/api/v1/scan/quick", json={
            "iq": "non_existent_file.cs8",
            "sample_rate": 10000000.0,
        })
        assert resp2.status_code == 404


@pytest.mark.anyio
async def test_export_plan_api():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = Path(tmpdir) / "test_plan.toml"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/v1/scan/export-plan", json={
                "frequencies": [2404380000.0, 2480060000.0],
                "sample_rate": 10000000.0,
                "center_frequency": 2405000000.0,
                "bit_rate": 325000.0,
                "sync_words": ["4a65b21c", "b59a4de3"],
                "output_path": str(out_file),
            })
            assert resp.status_code == 200
            data = resp.json()
            assert out_file.is_file()
            content = out_file.read_text(encoding="utf-8")
            assert "2404380000.0" in content
            assert "4a65b21c" in content
