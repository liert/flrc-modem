"""测试调制发射物理空口帧剖析与预览 API (/api/v1/modulate/preview)。"""
import pytest
from httpx import ASGITransport, AsyncClient

from webui.backend.app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_modulate_preview_success():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/modulate/preview",
            json={
                "payload_hex": "0102030405060708090a",
                "sync_word": "2bd36a07",
                "coding_rate": "3/4",
                "header_type": "variable",
                "bit_rate": 325000,
                "sample_rate": 2000000,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_bits"] > 100
        assert data["duration_us"] > 500
        assert data["sync_word_hex"] == "2bd36a07"
        assert data["payload_hex"] == "0102030405060708090a"
        assert len(data["crc_hex"]) == 6  # 3 bytes CRC24 = 6 hex chars

        slice_names = [s["name"] for s in data["slices"]]
        assert "AGC Preamble" in slice_names
        assert "Timing Preamble" in slice_names
        assert "Sync Word" in slice_names
        assert "Hardware Header" in slice_names
        assert "Payload Data" in slice_names
        assert "CRC Checksum" in slice_names
        assert "FEC Tail Bits" in slice_names

        assert len(data["envelope_samples"]) > 50
        assert len(data["frequency_samples"]) > 50
        assert data["rf_budget"]["bit_rate"] == 325000


@pytest.mark.anyio
async def test_modulate_preview_fixed_header():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/modulate/preview",
            json={
                "payload_hex": "112233445566",
                "sync_word": "b42b2bb4",
                "coding_rate": "1",  # No FEC tail bits
                "header_type": "fixed",
            },
        )
        assert res.status_code == 200
        data = res.json()
        slice_names = [s["name"] for s in data["slices"]]
        assert "Hardware Header" not in slice_names
        assert "FEC Tail Bits" not in slice_names
        assert data["header_hex"] == ""


@pytest.mark.anyio
async def test_modulate_preview_payload_validation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Payload too short (< 6 bytes)
        res = await client.post(
            "/api/v1/modulate/preview",
            json={"payload_hex": "1122334455"},  # 5 bytes
        )
        assert res.status_code == 400
        assert "6..127" in res.json()["detail"]

        # Invalid hex
        res = await client.post(
            "/api/v1/modulate/preview",
            json={"payload_hex": "not_a_hex_payload_xyz"},
        )
        assert res.status_code == 400

        # Invalid sync word
        res = await client.post(
            "/api/v1/modulate/preview",
            json={
                "payload_hex": "112233445566",
                "sync_word": "1234",  # Only 2 bytes instead of 4
            },
        )
        assert res.status_code == 400
