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
        connection.execute("ALTER TABLE runs DROP COLUMN attempt")
        connection.execute("ALTER TABLE runs DROP COLUMN attempt_created_at")
        connection.execute("DROP TABLE run_history")
        for table in ('run_logs', 'run_events', 'run_artifacts'):
            connection.execute(f"DROP INDEX idx_{table}_attempt")
            connection.execute(f"ALTER TABLE {table} DROP COLUMN attempt")
        connection.execute("INSERT INTO runs (id, command_id, command_args, cli_argv, status, created_at) VALUES ('old', 'demod', '{}', '[]', 'SUCCEEDED', 'now')")
    upgraded = Database(path)
    assert upgraded.get_run("old")["name"] == ""
    assert upgraded.get_run("old")["attempt"] == 1
    assert [run['attempt'] for run in upgraded.get_run_attempts('old')] == [1]
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


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELLED"])
@pytest.mark.parametrize("ui_events", [True, False])
async def test_rerun_keeps_one_task_and_archives_previous_execution(database, monkeypatch, tmp_path, status, ui_events):
    from webui.backend.app.jobs import manager as jobs_module
    jobs = jobs_module.JobManager()
    monkeypatch.setattr(jobs_module, "_JOB_MGR", jobs)
    executed = []
    async def fake_execute(run_id, argv, sdr_type):
        executed.append((run_id, argv))
    monkeypatch.setattr(jobs, "_execute_process", fake_execute)
    parameters = {"iq": str(tmp_path / "输入.iq"), "output": str(tmp_path / "结果.jsonl"), "frequency": [2480000000, 2481000000]}
    # 历史 argv 不是可执行来源；只保留事件开关。
    original = record("original", status, name="重新对频", command_args=parameters,
                      cli_argv=["obsolete-python", "untrusted-command"] + (["--ui-events", "ndjson"] if ui_events else []),
                      exit_code=7, error="previous error", completed_at="yesterday", summary={"frames": 10})
    database.insert_run(original)
    database.insert_log("original", "stdout", "old log", "now")
    database.insert_event("original", 0, "stage.started", None, "now", {})
    database.insert_artifact("original", None, parameters["output"], "frames_jsonl", {}, "now")
    before = database.get_run("original")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/runs/original/rerun")
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["id"] == "original"
        assert run["attempt"] == 2
        assert run["created_at"] == before["created_at"]
        assert run["attempt_created_at"] != before["attempt_created_at"]
        assert len(database.get_runs()) == 1
        assert run["name"] == original["name"]
        assert run["command_id"] == original["command_id"]
        assert run["command_args"] == parameters
        assert run["status"] == "QUEUED"
        assert run["exit_code"] is run["error"] is run["started_at"] is run["completed_at"] is None
        assert run["summary"] == {}
        assert run["cli_argv"][1:4] == ["-m", "flrc_modem", "demod"]
        assert ("--ui-events" in run["cli_argv"]) is ui_events
        assert database.get_run_attempt("original", 1) == before
        attempts = (await client.get("/api/v1/runs/original/attempts")).json()
        assert [item["attempt"] for item in attempts] == [2, 1]
        assert attempts[1]["status"] == status and attempts[1]["error"] == "previous error"
        assert (await client.get("/api/v1/runs/original?attempt=1")).json()["cli_argv"] == before["cli_argv"]
        assert (await client.get("/api/v1/runs/original/logs?attempt=1")).json()[0]["line"] == "old log"
        assert (await client.get("/api/v1/runs/original/events?attempt=1")).json()[0]["type"] == "stage.started"
        assert (await client.get("/api/v1/runs/original/artifacts?attempt=1")).json()[0]["attempt"] == 1
        assert database.get_run(run["id"])["command_args"] == parameters
        assert database.get_logs(run["id"]) == database.get_events(run["id"]) == database.get_artifacts(run["id"]) == []
        for suffix in ("", "/logs", "/events", "/artifacts", "/session"):
            assert (await client.get(f"/api/v1/runs/original{suffix}?attempt=99")).status_code == 404
        reloaded = Database(database.db_path)
        assert reloaded.get_run_attempt("original", 1) == before
        assert len(reloaded.get_run_attempts("original")) == 2
    await asyncio.gather(*jobs._tasks.values())
    assert executed == [(run["id"], run["cli_argv"])]


@pytest.mark.anyio
async def test_rerun_rejects_active_missing_and_deleted_tasks(database, monkeypatch):
    from unittest.mock import AsyncMock
    from webui.backend.app.jobs import manager as jobs_module
    jobs = jobs_module.JobManager()
    start = AsyncMock()
    monkeypatch.setattr(jobs, "create_and_start_run", start)
    monkeypatch.setattr(jobs_module, "_JOB_MGR", jobs)
    for status in ("RUNNING", "QUEUED", "SUCCEEDED"):
        database.insert_run(record(status, status))
    database.delete_runs(["SUCCEEDED"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for id, code in (("RUNNING", 409), ("QUEUED", 409), ("missing", 404), ("SUCCEEDED", 404)):
            assert (await client.post(f"/api/v1/runs/{id}/rerun")).status_code == code
    start.assert_not_awaited()


@pytest.mark.anyio
async def test_rerun_revalidates_parameters_and_output_conflicts(database, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from webui.backend.app.jobs import manager as jobs_module
    from webui.backend.app.storage.db import artifact_path_key
    jobs = jobs_module.JobManager()
    execute = AsyncMock()
    monkeypatch.setattr(jobs, "_execute_process", execute)
    monkeypatch.setattr(jobs_module, "_JOB_MGR", jobs)
    output = str(tmp_path / "busy.jsonl")
    database.insert_run(record("invalid", command_args={"iq": "input.iq", "obsolete_option": True}))
    database.insert_run(record("busy", command_args={"iq": "input.iq", "output": output}))
    jobs._output_claims[artifact_path_key(output)] = "other"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/runs/invalid/rerun")
        assert response.status_code == 400 and "未知参数" in response.json()["detail"]
        response = await client.post("/api/v1/runs/busy/rerun")
        assert response.status_code == 409 and "输出文件" in response.json()["detail"]
    assert len(database.get_runs()) == 2
    execute.assert_not_awaited()


@pytest.mark.anyio
async def test_rerun_checks_device_lock_before_starting(database, monkeypatch):
    from unittest.mock import AsyncMock
    from webui.backend.app.jobs import manager as jobs_module
    from webui.backend.app.devices.manager import DeviceManager
    jobs = jobs_module.JobManager()
    devices = DeviceManager()
    monkeypatch.setattr(jobs_module, "_JOB_MGR", jobs)
    monkeypatch.setattr(jobs_module, "get_device_manager", lambda: devices)
    execute = AsyncMock()
    monkeypatch.setattr(jobs, "_execute_process", execute)
    assert devices.acquire_device("usrp", "other")[0]
    database.insert_run(record("radio", command_id="rx", command_args={
        "frequency": 2480000000, "sample_rate": 2500000, "duration": 1, "output": "outputs/rerun.iq"}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/runs/radio/rerun")
        assert response.status_code == 409, response.text
        assert "独占锁定" in response.json()["detail"]
    assert len(database.get_runs()) == 1
    execute.assert_not_awaited()


@pytest.mark.anyio
async def test_rerun_executes_real_offline_process_and_can_run_again(database, monkeypatch, tmp_path):
    from webui.backend.app.jobs import manager as jobs_module
    jobs = jobs_module.JobManager()
    monkeypatch.setattr(jobs_module, "_JOB_MGR", jobs)
    output = tmp_path / "rerun.iq"
    database.insert_run(record("failed", "FAILED", command_id="modulate", command_args={
        "payload_hex": "1122334455667788", "sync": "b42b2bb4", "output": str(output), "verify": True},
        cli_argv=["--ui-events", "ndjson"]))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            source = "failed"
            for attempt in (2, 3):
                response = await client.post(f"/api/v1/runs/{source}/rerun")
                assert response.status_code == 200, response.text
                run_id = response.json()["id"]
                assert run_id == source == "failed"
                assert response.json()["attempt"] == attempt
                assert len(database.get_runs()) == 1
                await asyncio.wait_for(asyncio.gather(*jobs._tasks.values()), timeout=20)
                run = (await client.get(f"/api/v1/runs/{run_id}")).json()
                assert run["status"] == "SUCCEEDED", run
                assert run["exit_code"] == 0
                assert output.stat().st_size > 0
                assert "artifact.created" in {event["type"] for event in database.get_events(run_id)}
                assert database.get_artifacts(run_id)[0]["run_id"] == run_id
                assert all(event['attempt'] == attempt for event in database.get_events(run_id))
                database.insert_log(run_id, 'stdout', f'finished attempt {attempt}', '2026-10-21T01:00:00Z')
                source = run_id
            history = (await client.get('/api/v1/runs/failed/attempts')).json()
            assert [item['attempt'] for item in history] == [3, 2, 1]
            old_artifact = (await client.get('/api/v1/runs/failed/artifacts?attempt=2')).json()[0]
            assert old_artifact['attempt'] == 2
            assert old_artifact['file_state']['status'] == 'overwritten'
            assert old_artifact['file_state']['readable'] is False
            assert (await client.get(f"/api/v1/artifacts/{old_artifact['id']}")).status_code == 409
            assert len(database.get_logs('failed', attempt=2)) > 0
            assert (await client.delete('/api/v1/runs/failed')).status_code == 200
            assert database.get_run_attempts('failed') == []
            assert database.get_logs('failed', attempt=2) == []
    finally:
        await jobs.shutdown()


@pytest.mark.anyio
async def test_concurrent_rerun_only_adds_one_execution(database, monkeypatch):
    from webui.backend.app.jobs import manager as jobs_module
    jobs = jobs_module.JobManager()
    monkeypatch.setattr(jobs_module, '_JOB_MGR', jobs)
    release = asyncio.Event()
    async def execute(run_id, argv, sdr_type):
        await release.wait()
        database.update_run_status(run_id, 'SUCCEEDED', exit_code=0)
    monkeypatch.setattr(jobs, '_execute_process', execute)
    database.insert_run(record('same'))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            responses = await asyncio.gather(*(client.post('/api/v1/runs/same/rerun') for _ in range(2)))
            assert sorted(response.status_code for response in responses) == [200, 409]
            assert len(database.get_runs()) == 1
            assert [run['attempt'] for run in database.get_run_attempts('same')] == [2, 1]
    finally:
        release.set()
        await asyncio.gather(*jobs._tasks.values())


def test_rerun_history_survives_output_cleanup_and_session_state_is_separate(database, tmp_path):
    from webui.backend.app.artifacts.cleanup import prune_overwritten_runs
    path = tmp_path / 'frames.jsonl'
    path.write_text('{}\n')
    database.insert_run(record('same', command_args={'output': str(path)}))
    database.insert_artifact('same', None, str(path), 'frames_jsonl', {'file_version': file_version(path)}, 'now')
    database.insert_event('same', 1, 'duplex.ready', None, 'now', {'sample_rate': 2500000})
    database.insert_event('same', 2, 'duplex.result', None, 'now', {'request_id': 'old'})
    snapshot = database.get_run('same')
    database.restart_run({**snapshot, 'attempt_created_at': 'later'})
    assert database.get_message_state('same')['ready'] is None
    assert database.get_message_state('same')['results'] == []
    assert database.get_message_state('same', 1)['results'][0]['request_id'] == 'old'
    database.insert_event('same', 1, 'duplex.result', None, 'now', {'request_id': 'new'})
    assert database.get_message_state('same')['results'][0]['request_id'] == 'new'
    database.update_run_status('same', 'SUCCEEDED')
    database.insert_run(record('other', command_args={'output': str(path)}))
    path.write_text('{"new":true}\n')
    database.insert_artifact('other', None, str(path), 'frames_jsonl', {'file_version': file_version(path)}, 'now')
    assert prune_overwritten_runs(database, 'other') == []
    assert len(database.get_run_attempts('same')) == 2
