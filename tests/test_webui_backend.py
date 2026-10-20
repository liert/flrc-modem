"""WebUI 后端功能与 API 自动化测试。"""
import asyncio
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from webui.backend.app.devices.manager import get_device_manager
from webui.backend.app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


@pytest.mark.anyio
async def test_list_commands():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/commands")
        assert response.status_code == 200
        commands = response.json()
        assert len(commands) >= 7
        cmd_ids = {c["id"] for c in commands}
        assert "demod" in cmd_ids
        assert "modulate" in cmd_ids
        assert "rx" in cmd_ids
        assert "tx" in cmd_ids
        assert "sync" in cmd_ids


@pytest.mark.anyio
async def test_list_pipelines():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/pipelines")
        assert response.status_code == 200
        pipelines = response.json()
        assert len(pipelines) >= 4
        pipe_ids = {p["id"] for p in pipelines}
        assert "pipe_demod" in pipe_ids
        assert "pipe_modulate" in pipe_ids


@pytest.mark.anyio
async def test_devices_probe():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/devices")
        assert response.status_code == 200
        devices = response.json()
        assert len(devices) >= 2
        types = {d["device_type"] for d in devices}
        assert "usrp" in types
        assert "hackrf" in types


@pytest.mark.anyio
async def test_presets_crud():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        save_resp = await client.post(
            "/api/v1/configs",
            json={
                "name": "测试预设 325k",
                "command_id": "demod",
                "values": {"sample_rate": 10000000, "bit_rate": 325000},
            },
        )
        assert save_resp.status_code == 200
        preset = save_resp.json()
        assert preset["name"] == "测试预设 325k"

        list_resp = await client.get("/api/v1/configs?command_id=demod")
        assert list_resp.status_code == 200
        presets = list_resp.json()
        assert any(p["id"] == preset["id"] for p in presets)


@pytest.mark.anyio
async def test_device_mutual_exclusion():
    dm = get_device_manager()
    # 模拟 run_test_1 锁定 USRP
    ok, err = dm.acquire_device("usrp", "run_test_1")
    assert ok is True
    assert err is None

    # 模拟 run_test_2 试图并发占用同一 USRP
    ok2, err2 = dm.acquire_device("usrp", "run_test_2")
    assert ok2 is False
    assert "独占锁定" in err2

    # 释放
    released = dm.release_device("run_test_1")
    assert "usrp" in released

    # 再次申请成功
    ok3, err3 = dm.acquire_device("usrp", "run_test_2")
    assert ok3 is True
    dm.release_device("run_test_2")


@pytest.mark.anyio
async def test_end_to_end_modulate_and_demod_run():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. 触发调制任务
        output_iq = "outputs/e2e_test.iq"
        mod_resp = await client.post(
            "/api/v1/runs",
            json={
                "command_id": "modulate",
                "parameters": {
                    "payload_hex": "1122334455667788",
                    "sync": "b42b2bb4",
                    "output": output_iq,
                    "repeat": 2,
                    "verify": True,
                },
                "enable_ui_events": True,
            },
        )
        assert mod_resp.status_code == 200
        run_mod = mod_resp.json()
        run_id = run_mod["id"]

        # 等待后台子进程完成
        detail = None
        for _ in range(40):
            detail_resp = await client.get(f"/api/v1/runs/{run_id}")
            detail = detail_resp.json()
            if detail["status"] in ("SUCCEEDED", "FAILED"):
                break
            await asyncio.sleep(0.2)

        assert detail is not None
        assert detail["status"] == "SUCCEEDED"
        assert detail["exit_code"] == 0

        # 检查事件与产物记录
        events_resp = await client.get(f"/api/v1/runs/{run_id}/events")
        assert events_resp.status_code == 200
        events = events_resp.json()
        assert len(events) > 0
        event_types = {e["type"] for e in events}
        assert "stage.started" in event_types
        assert "artifact.created" in event_types

        # 2. 触发解调任务
        output_jsonl = "outputs/e2e_test.jsonl"
        demod_resp = await client.post(
            "/api/v1/runs",
            json={
                "command_id": "demod",
                "parameters": {
                    "iq": output_iq,
                    "sample_rate": 10000000,
                    "frequency": 2480000000,
                    "sync": "b42b2bb4",
                    "output": output_jsonl,
                },
                "enable_ui_events": True,
            },
        )
        assert demod_resp.status_code == 200
        demod_run_id = demod_resp.json()["id"]

        demod_detail = None
        for _ in range(40):
            demod_detail_resp = await client.get(f"/api/v1/runs/{demod_run_id}")
            demod_detail = demod_detail_resp.json()
            if demod_detail["status"] in ("SUCCEEDED", "FAILED"):
                break
            await asyncio.sleep(0.2)

        assert demod_detail is not None
        assert demod_detail["status"] == "SUCCEEDED"
        assert demod_detail["exit_code"] == 0

        # 3. 测试信号分析接口 (频谱与星座图)
        spec_resp = await client.get(f"/api/v1/analysis/spectrum?path={output_iq}&sample_rate=10000000")
        assert spec_resp.status_code == 200
        spec_data = spec_resp.json()
        assert "points" in spec_data
        assert len(spec_data["points"]) > 0

        const_resp = await client.get(f"/api/v1/analysis/constellation?path={output_iq}&max_points=500")
        assert const_resp.status_code == 200
        const_data = const_resp.json()
        assert "points" in const_data
        assert len(const_data["points"]) > 0


@pytest.mark.anyio
async def test_templates_api():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. 列表所有模板
        resp = await client.get("/api/v1/templates")
        assert resp.status_code == 200
        tpls = resp.json()
        assert len(tpls) >= 8
        names = {t["name"] for t in tpls}
        assert "dk32s_325k.toml" in names
        assert "dk32s_tx_channels.json" in names

        # 2. 获取特定物理预设 TOML
        toml_resp = await client.get("/api/v1/templates/dk32s_325k.toml")
        assert toml_resp.status_code == 200
        toml_item = toml_resp.json()
        assert toml_item["format"] == "toml"
        assert toml_item["category"] == "physical_config"
        assert "demod" in toml_item["parsed"]
        assert toml_item["parsed"]["demod"]["bit_rate"] == 325000.0

        # 3. 按命令过滤
        tx_resp = await client.get("/api/v1/templates?command_id=transmit")
        assert tx_resp.status_code == 200
        tx_tpls = tx_resp.json()
        assert all(t["category"] == "message_payload" for t in tx_tpls)
        assert any("channels" in t["name"] for t in tx_tpls)
