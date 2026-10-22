"""测试 SDR 设备探测与硬件健康诊断 API (/api/v1/devices/diagnostics & /api/v1/devices/probe)。"""
import pytest
from httpx import ASGITransport, AsyncClient

from webui.backend.app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_get_device_diagnostics():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/devices/diagnostics")
        assert res.status_code == 200
        data = res.json()
        assert "drivers" in data
        assert "hackrf" in data["drivers"]
        assert "usrp" in data["drivers"]
        assert "devices" in data
        assert isinstance(data["devices"], list)
        assert "active_locks" in data
        assert "timestamp" in data


@pytest.mark.anyio
async def test_post_device_probe():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/v1/devices/probe")
        assert res.status_code == 200
        data = res.json()
        assert "drivers" in data
        assert "devices" in data
        # Check that both usrp and hackrf devices exist in list
        dev_types = [d["device_type"] for d in data["devices"]]
        assert "usrp" in dev_types
        assert "hackrf" in dev_types
