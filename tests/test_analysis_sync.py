"""任务与信号分析的关联回归；独立数据库，不改写用户运行记录。"""
import asyncio
import json
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.io.iq import write_iq
from flrc_modem.modulation import ModulationConfig, iter_signal
from flrc_modem.phy.encoder import encode_packet
from webui.backend.app.artifacts.context import analysis_context
from webui.backend.app.artifacts.lifecycle import file_version
from webui.backend.app.commands.registry import CommandRegistry
from webui.backend.app.events.hub import EventHub
from webui.backend.app.jobs.manager import JobManager
from webui.backend.app.main import app
from webui.backend.app.paths import PROJECT_ROOT
from webui.backend.app.storage.db import Database


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from webui.backend.app.storage import db as db_module
    from webui.backend.app.events import hub as hub_module
    from webui.backend.app.jobs import manager as jobs_module
    from webui.backend.app.artifacts import manager as artifacts_module
    database = Database(tmp_path / "webui.db")
    jobs = JobManager()
    monkeypatch.setattr(db_module, "_DB", database)
    monkeypatch.setattr(jobs_module, "_JOB_MGR", jobs)
    monkeypatch.setattr(hub_module, "_EVENT_HUB", EventHub())
    monkeypatch.setattr(artifacts_module, "ALLOWED_DIRS", [tmp_path])
    return database, jobs


def historical_run(iq_path, frames_path):
    parameters = {"iq": str(iq_path), "output": str(frames_path), "config": str(PROJECT_ROOT / "templates/physical/h12.toml")}
    return {"id": "historical_h12", "command_id": "demod", "status": "SUCCEEDED",
            "command_args": parameters, "created_at": "2026-10-20T00:00:00+00:00",
            "cli_argv": CommandRegistry().build_cli_argv("demod", parameters)}


@pytest.mark.anyio
async def test_historical_task_recovers_iq_output_and_h12_settings(tmp_path, isolated):
    database, _ = isolated
    iq_path, output = tmp_path / "fresh_pairing_2403_8.iq", tmp_path / "decoded_frames.jsonl"
    record = historical_run(iq_path, output)
    database.insert_run(record)
    # 模拟修复前的任务：只有输出登记，输入名和输出名也不同。
    database.insert_artifact(record["id"], "output", str(output), "frames_jsonl", {"decoded_frames": 119}, "now")
    frame = {"time_s": 1.0, "frequency_hz": 2404380000, "crc_ok": True, "payload_hex": "66778899aa"}
    output.write_text("\n".join(json.dumps(frame) for _ in range(119)), encoding="utf-8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/runs/{record['id']}/analysis")
        assert response.status_code == 200
        context = response.json()
        assert Path(context["iq_path"]) == iq_path
        assert Path(context["frames_path"]) == output
        assert context["sample_rate"] == 8000000
        assert context["center_frequency"] == 2403000000
        assert context["iq_format"] == "cs8"
        assert context["metadata_error"] is None
        frames = await client.get("/api/v1/analysis/frames", params={"path": context["frames_path"], "limit": 500})
        assert frames.status_code == 200
        assert frames.json()["total"] == 119
        assert (await client.get("/api/v1/runs/missing/analysis")).status_code == 404


def test_recorded_sampling_snapshot_survives_template_changes(tmp_path):
    record = historical_run(tmp_path / "source.iq", tmp_path / "frames.jsonl")
    # 实际使用的模板即使后来丢失，也应使用产物里保存的参数。
    record["cli_argv"] = ["python", "-m", "flrc_modem", "demod", "--iq", "source.iq", "--config", "missing.toml"]
    metadata = {"sample_rate": 8000000, "center_frequency": 2403000000, "iq_format": "cs8"}
    context = analysis_context(record, [{"path": str(tmp_path / "source.iq"), "kind": "input_iq", "metadata": metadata}])
    assert context["sample_rate"] == 8000000
    assert context["metadata_error"] is None


def test_output_artifact_is_updated_instead_of_duplicated(tmp_path):
    database = Database(tmp_path / "webui.db")
    path = str(tmp_path / "decoded_frames.jsonl")
    database.insert_artifact("run", "output", path, "frames_jsonl", {"decoded_frames": 0}, "start")
    database.insert_artifact("run", "output", path, "frames_jsonl", {"decoded_frames": 119}, "end")
    artifacts = database.get_artifacts("run")
    assert len(artifacts) == 1
    assert artifacts[0]["metadata"]["decoded_frames"] == 119


@pytest.mark.anyio
async def test_artifact_is_persisted_before_notifying_browser(isolated, monkeypatch):
    database, jobs = isolated
    from webui.backend.app.events.hub import get_event_hub
    checked = []
    async def broadcast(run_id, message):
        if message["type"] == "artifact.created":
            checked.append(database.get_artifacts(run_id))
    monkeypatch.setattr(get_event_hub(), "_broadcast", broadcast)
    event = {"type": "artifact.created", "seq": 1, "payload": {"path": "outputs/frames.jsonl", "kind": "frames_jsonl"}}
    await jobs._handle_line("run", json.dumps(event).encode(), True)
    assert len(checked) == 1 and len(checked[0]) == 1


@pytest.mark.anyio
async def test_h12_run_registers_input_and_output_for_analysis(tmp_path, isolated):
    database, jobs = isolated
    iq_path, output = tmp_path / "capture.iq", tmp_path / "different_name.jsonl"
    payload = bytes.fromhex("66778899aa") + bytes(27)
    packet = encode_packet(payload, b"T12S", 0, "1", header_type="fixed", crc_bytes=4)
    modulation = ModulationConfig(sample_rate=8000000, bit_rate=1300000, coding_rate="1", bt=0,
                                  offset_hz=1380000, repeat=2)
    write_iq(iq_path, iter_signal(packet.bits, modulation), "cs8")
    record = await jobs.create_and_start_run("demod", {
        "iq": str(iq_path), "config": str(PROJECT_ROOT / "templates/physical/h12.toml"), "output": str(output),
    })
    await asyncio.wait_for(jobs._tasks[record.id], 15)
    assert database.get_run(record.id)["status"] == "SUCCEEDED"
    artifacts = database.get_artifacts(record.id)
    assert len(artifacts) == 2
    assert {item["kind"] for item in artifacts} == {"input_iq", "frames_jsonl"}
    result = next(item for item in artifacts if item["kind"] == "frames_jsonl")
    assert result["metadata"]["decoded_frames"] == 2
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        context = (await client.get(f"/api/v1/runs/{record.id}/analysis")).json()
        assert context["sample_rate"] == 8000000
        assert Path(context["iq_path"]) == iq_path
        frames = (await client.get("/api/v1/analysis/frames", params={"path": context["frames_path"]})).json()
        assert frames["total"] == 2
        assert all(row["payload_hex"] == payload.hex() for row in frames["frames"])


def tracked_run(database, iq_path, output, run_id, payload="66778899aa"):
    run = historical_run(iq_path, output)
    run["id"] = run_id
    database.insert_run(run)
    output.write_text(json.dumps({"time_s": 1, "frequency_hz": 2404380000,
                                  "crc_ok": True, "payload_hex": payload}) + "\n", encoding="utf-8")
    database.insert_artifact(run_id, "output", str(output), "frames_jsonl",
                             {"file_version": file_version(output), "size_bytes": output.stat().st_size}, "now")
    return run


@pytest.mark.anyio
async def test_same_output_name_marks_old_task_overwritten(tmp_path, isolated):
    database, _ = isolated
    iq, output = tmp_path / "capture.iq", tmp_path / "decoded_frames.jsonl"
    iq.write_bytes(b"\0" * 400)
    tracked_run(database, iq, output, "old", "001122334455")
    # 文件名及内容长度均相同，也必须按写入任务区分。
    tracked_run(database, iq, output, "new", "66778899aabb")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        old = (await client.get("/api/v1/runs/old/analysis")).json()
        new = (await client.get("/api/v1/runs/new/analysis")).json()
        assert old["file_states"]["frames"]["status"] == "overwritten"
        assert old["file_states"]["frames"]["current_run_id"] == "new"
        assert new["file_states"]["frames"]["status"] == "available"
        params = {"path": str(output), "run_id": "old"}
        assert (await client.get("/api/v1/analysis/frames", params=params)).status_code == 409
        params["run_id"] = "new"
        frames = (await client.get("/api/v1/analysis/frames", params=params)).json()
        assert frames["frames"][0]["payload_hex"] == "66778899aabb"
        old_artifact = database.get_artifacts("old")[0]
        assert (await client.get(f"/api/v1/artifacts/{old_artifact['id']}")).status_code == 409


@pytest.mark.anyio
async def test_deleted_result_removes_readability_but_keeps_task(tmp_path, isolated):
    database, _ = isolated
    iq, output = tmp_path / "capture.iq", tmp_path / "decoded_frames.jsonl"
    iq.write_bytes(b"\0" * 400)
    tracked_run(database, iq, output, "deleted")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        before = (await client.get("/api/v1/runs/deleted/analysis")).json()
        output.unlink()
        after = (await client.get("/api/v1/runs/deleted/analysis")).json()
        assert before["file_states"]["frames"]["readable"]
        assert after["file_states"]["frames"]["status"] == "missing"
        assert after["file_states"]["frames"]["revision"] != before["file_states"]["frames"]["revision"]
        assert after["file_states"]["iq"]["readable"]
        assert database.get_run("deleted")["status"] == "SUCCEEDED"
        artifacts = (await client.get("/api/v1/runs/deleted/artifacts")).json()
        assert len(artifacts) == 1 and artifacts[0]["file_state"]["status"] == "missing"
        assert (await client.get("/api/v1/analysis/frames", params={"path": str(output), "run_id": "deleted"})).status_code == 404
        assert (await client.get(f"/api/v1/artifacts/{artifacts[0]['id']}")).status_code == 404


@pytest.mark.anyio
async def test_external_changes_block_historical_result(tmp_path, isolated):
    database, _ = isolated
    iq, output = tmp_path / "capture.iq", tmp_path / "frames.jsonl"
    tracked_run(database, iq, output, "modified")
    signature = file_version(output)
    # 即使内容大小没变，外部修改也会改变版本。
    output.write_text(output.read_text().replace("66778899aa", "0011223344"), encoding="utf-8")
    os.utime(output, ns=(signature["mtime_ns"] + 1000000000, signature["mtime_ns"] + 1000000000))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        context = (await client.get("/api/v1/runs/modified/analysis")).json()
        assert context["file_states"]["frames"]["status"] == "modified"
        assert (await client.get("/api/v1/analysis/frames", params={"path": str(output), "run_id": "modified"})).status_code == 409


@pytest.mark.anyio
async def test_missing_iq_does_not_hide_existing_jsonl(tmp_path, isolated):
    database, _ = isolated
    iq, output = tmp_path / "missing.iq", tmp_path / "frames.jsonl"
    tracked_run(database, iq, output, "keep_frames")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        context = (await client.get("/api/v1/runs/keep_frames/analysis")).json()
        assert context["file_states"]["iq"]["status"] == "missing"
        assert context["file_states"]["frames"]["readable"]
        assert (await client.get("/api/v1/analysis/frames", params={"path": str(output), "run_id": "keep_frames"})).status_code == 200


@pytest.mark.anyio
async def test_active_output_is_readable_as_it_grows(tmp_path, isolated):
    database, _ = isolated
    iq, output = tmp_path / "input.iq", tmp_path / "growing.jsonl"
    tracked_run(database, iq, output, "growing")
    database.update_run_status("growing", "RUNNING")
    with output.open("a") as stream:
        stream.write(output.read_text())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        context = (await client.get("/api/v1/runs/growing/analysis")).json()
        assert context["file_states"]["frames"]["readable"]
        frames = (await client.get("/api/v1/analysis/frames", params={"path": str(output), "run_id": "growing"})).json()
        assert frames["total"] == 2


@pytest.mark.anyio
async def test_same_output_cannot_be_written_by_two_web_tasks(tmp_path, isolated):
    _, jobs = isolated
    params = {"iq": str(tmp_path / "input.iq"), "output": str(tmp_path / "shared.jsonl")}
    first = await jobs.create_and_start_run("demod", params)
    with pytest.raises(RuntimeError, match="输出文件"):
        await jobs.create_and_start_run("demod", params)
    await jobs.stop_run(first.id)
    assert not jobs._output_claims
    second = await jobs.create_and_start_run("demod", params)
    await jobs.stop_run(second.id)


@pytest.mark.anyio
async def test_replacement_during_frame_read_is_rejected(tmp_path, isolated, monkeypatch):
    database, _ = isolated
    iq, output = tmp_path / "input.iq", tmp_path / "frames.jsonl"
    tracked_run(database, iq, output, "read_old")
    from webui.backend.app.artifacts.manager import get_artifact_manager
    manager = get_artifact_manager()
    read = manager.read_jsonl_frames
    def replace_during_read(*args, **kwargs):
        frames = read(*args, **kwargs)
        tracked_run(database, iq, output, "write_new", "001122334455")
        return frames
    monkeypatch.setattr(manager, "read_jsonl_frames", replace_during_read)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/analysis/frames", params={"path": str(output), "run_id": "read_old"})
        assert response.status_code == 409
