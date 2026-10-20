"""任务整理功能回归，所有读写使用临时数据库和临时文件。"""
import sqlite3
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from webui.backend.app.main import app
from webui.backend.app.storage.db import Database
from webui.backend.app.artifacts.lifecycle import file_state, file_version


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def database(tmp_path, monkeypatch):
    from webui.backend.app.storage import db
    database = Database(tmp_path / "tasks.db")
    monkeypatch.setattr(db, "_DB", database)
    return database


def record(id, status="SUCCEEDED", **kwargs):
    return {"id": id, "command_id": "demod", "command_args": {"iq": "outputs/对频.iq"},
            "cli_argv": [], "created_at": "2026-10-20T01:00:00+00:00", "status": status, **kwargs}


@pytest.mark.anyio
async def test_rename_persists_searches_and_resets(database):
    database.insert_run(record("task"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/runs/task", json={"name": "  H12 首次对频  "})
        assert response.status_code == 200
        assert response.json()["name"] == "H12 首次对频"
        assert Database(database.db_path).get_run("task")["name"] == "H12 首次对频"
        assert len((await client.get("/api/v1/runs", params={"search": "首次对频"})).json()) == 1
        assert len((await client.get("/api/v1/runs", params={"search": "对频.iq"})).json()) == 1
        assert (await client.patch("/api/v1/runs/task", json={"name": "x" * 121})).status_code == 422
        assert (await client.patch("/api/v1/runs/missing", json={"name": "name"})).status_code == 404
        assert (await client.patch("/api/v1/runs/task", json={"name": ""})).json()["name"] == ""


@pytest.mark.anyio
async def test_delete_clears_history_and_artifact_links_but_keeps_files(database, tmp_path):
    database.insert_run(record("task"))
    output = tmp_path / "decoded.jsonl"
    output.write_text('{}\n', encoding="utf-8")
    database.insert_artifact("task", None, str(output), "frames_jsonl", {}, "now")
    artifact_id = database.get_artifacts("task")[0]["id"]
    database.insert_log("task", "stdout", "log", "now")
    database.insert_event("task", 0, "event", None, "now", {})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/runs/task")
        assert response.status_code == 200
        assert response.json() == {"deleted_run_ids": ["task"], "files_retained": True}
        assert (await client.get("/api/v1/runs")).json() == []
        for suffix in ("", "/logs", "/events", "/analysis"):
            assert (await client.get(f"/api/v1/runs/task{suffix}")).status_code == 404
        assert (await client.get(f"/api/v1/artifacts/{artifact_id}")).status_code == 404
        assert (await client.get("/api/v1/artifacts")).json() == []
        assert (await client.delete("/api/v1/runs/task")).status_code == 404
    assert output.read_text(encoding="utf-8") == '{}\n'
    assert database.get_logs("task") == database.get_events("task") == database.get_artifacts("task") == []


@pytest.mark.anyio
async def test_batch_delete_is_atomic_and_protects_active_tasks(database):
    for status in ("SUCCEEDED", "FAILED", "CANCELLED", "RUNNING", "QUEUED"):
        database.insert_run(record(status, status))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for blocked in ("RUNNING", "QUEUED", "missing"):
            response = await client.post("/api/v1/runs/delete", json={"run_ids": ["SUCCEEDED", blocked]})
            assert response.status_code == (404 if blocked == "missing" else 409)
            assert database.get_run("SUCCEEDED") is not None
        response = await client.post("/api/v1/runs/delete", json={"run_ids": ["SUCCEEDED", "FAILED", "CANCELLED", "SUCCEEDED"]})
        assert response.status_code == 200
        assert len(response.json()["deleted_run_ids"]) == 3
        assert (await client.post("/api/v1/runs/delete", json={"run_ids": []})).status_code == 422
        assert {run["id"] for run in database.get_runs()} == {"RUNNING", "QUEUED"}


def test_deleting_latest_writer_never_reassigns_same_file_to_old_task(database, tmp_path):
    path = tmp_path / "same.jsonl"
    path.write_text('{}\n', encoding="utf-8")
    # 即便文件内容及 stat 相同，新的写入归属也不能因删除任务而丢失。
    for id in ("old", "new"):
        database.insert_run(record(id))
        database.insert_artifact(id, None, str(path), "frames_jsonl", {"file_version": file_version(path)}, "now")
    database.delete_runs(["new"])
    writer = next(iter(database.get_latest_writers([str(path)]).values()))
    state = file_state(str(path), database.get_artifacts("old")[0], run_id="old", run_status="SUCCEEDED", writer=writer, output=True)
    assert state["status"] == "overwritten"
    assert state["readable"] is False
    assert state["current_run_id"] is None
    assert "已删除" in state["message"]
    assert len(database.get_artifacts()) == 1


@pytest.mark.anyio
async def test_search_and_pagination_cover_tasks_beyond_first_hundred(database):
    for index in range(125):
        database.insert_run(record(f"run_{index:03}", name=f"历史测试 {index:03}"))
    database.insert_run(record("cancelled", "CANCELLED", name="已取消测试"))
    database.delete_runs(["run_123"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.get("/api/v1/runs", params={"limit": 50})).json()
        second = (await client.get("/api/v1/runs", params={"limit": 50, "offset": 50})).json()
        last = (await client.get("/api/v1/runs", params={"limit": 50, "offset": 100})).json()
        assert len({run["id"] for run in first + second + last}) == 125
        found = (await client.get("/api/v1/runs", params={"search": "历史测试 000"})).json()
        assert [run["id"] for run in found] == ["run_000"]
        assert len((await client.get("/api/v1/runs", params={"status": "CANCELLED"})).json()) == 1
        assert (await client.get("/api/v1/runs", params={"search": "run_123"})).json() == []
        assert (await client.get("/api/v1/runs", params={"status": "BAD"})).status_code == 422


def test_old_database_migrates_without_losing_history(tmp_path):
    path = tmp_path / "legacy.db"
    Database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE runs DROP COLUMN name")
        connection.execute("ALTER TABLE runs DROP COLUMN deleted_at")
        connection.execute("INSERT INTO runs (id, command_id, command_args, cli_argv, status, created_at) VALUES ('old', 'demod', '{}', '[]', 'SUCCEEDED', 'now')")
    upgraded = Database(path)
    assert upgraded.get_run("old")["name"] == ""
    upgraded.rename_run("old", "旧任务")
    assert Database(path).get_run("old")["name"] == "旧任务"


@pytest.mark.anyio
async def test_changes_notify_other_pages_after_persistence(database, monkeypatch):
    from webui.backend.app.events.hub import get_event_hub
    notifications = []
    async def broadcast(run_id, event):
        current = database.get_run(run_id)
        if event["type"] == "task.updated":
            assert current["name"] == "对频确认"
        else:
            assert current is None
            assert database.get_events(run_id) == []
            assert event["payload"]["run_ids"] == ["task", "second"]
        notifications.append(event["type"])
    monkeypatch.setattr(get_event_hub(), "_broadcast", broadcast)
    for id in ("task", "second"):
        database.insert_run(record(id))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.patch("/api/v1/runs/task", json={"name": "对频确认"})).status_code == 200
        assert (await client.post("/api/v1/runs/delete", json={"run_ids": ["task", "second"]})).status_code == 200
    assert notifications == ["task.updated", "task.deleted"]


@pytest.mark.anyio
async def test_create_with_task_name_keeps_metadata_out_of_cli_and_persists_output(database, monkeypatch, tmp_path):
    from webui.backend.app.jobs import manager as jobs_module
    jobs = jobs_module.JobManager()
    monkeypatch.setattr(jobs_module, "_JOB_MGR", jobs)
    executed = []
    async def fake_execute(run_id, argv, sdr_type):
        executed.append(argv)
    monkeypatch.setattr(jobs, "_execute_process", fake_execute)
    output = str(tmp_path / "H12首次对频.jsonl")
    parameters = {"iq": str(tmp_path / "input.iq"), "output": output}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/runs", json={"command_id": "demod", "name": "  H12首次对频  ", "parameters": parameters})
        assert response.status_code == 200
        run = response.json()
        assert run["name"] == "H12首次对频"
        assert run["command_args"] == parameters
        assert run["cli_argv"][run["cli_argv"].index("--output") + 1] == output
        assert "--name" not in run["cli_argv"]
        assert database.get_run(run["id"])["name"] == run["name"]
        assert (await client.get(f"/api/v1/runs/{run['id']}/analysis")).json()["frames_path"] == output
        renamed = await client.patch(f"/api/v1/runs/{run['id']}", json={"name": "重新标注"})
        assert renamed.status_code == 200 and renamed.json()["command_args"]["output"] == output
        assert (await client.post("/api/v1/runs", json={"command_id": "demod", "name": "x" * 121, "parameters": parameters})).status_code == 422
        # 原有调用不提供名称仍可创建。
        legacy = await client.post("/api/v1/runs", json={"command_id": "demod", "parameters": {"iq": parameters["iq"]}})
        assert legacy.status_code == 200 and legacy.json()["name"] == ""
    await asyncio.gather(*jobs._tasks.values())
    assert len(executed) == 2
