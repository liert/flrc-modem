"""同名结果覆盖后的自动清理；临时数据库、临时文件及模拟子进程。"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from webui.backend.app.artifacts.cleanup import prune_overwritten_runs
from webui.backend.app.artifacts.lifecycle import file_version
from webui.backend.app.jobs.manager import JobManager
from webui.backend.app.storage.db import Database


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def database(tmp_path, monkeypatch):
    from webui.backend.app.storage import db as module
    database = Database(tmp_path / "cleanup.db")
    monkeypatch.setattr(module, "_DB", database)
    return database


def result(database, id, path, *, status="SUCCEEDED", command="demod", legacy=False):
    database.insert_run({"id": id, "command_id": command, "status": status,
                         "command_args": {"output": str(path)}, "cli_argv": [], "created_at": "now"})
    path.write_text(json.dumps({"payload_hex": id}) + "\n", encoding="utf-8")
    database.insert_artifact(id, None, str(path), "frames_jsonl",
                             {} if legacy else {"file_version": file_version(path)}, "now")


def test_successful_replacement_cleans_history_and_links_but_keeps_files(database, tmp_path):
    output, iq = tmp_path / "same.jsonl", tmp_path / "shared.iq"
    iq.write_bytes(b'\0' * 32)
    result(database, "old", output)
    database.insert_artifact("old", None, str(iq), "input_iq", {}, "now")
    database.insert_log("old", "stdout", "old log", "now")
    database.insert_event("old", 1, "event", None, "now", {})
    result(database, "new", output)
    assert prune_overwritten_runs(database, "new") == ["old"]
    assert database.get_run("old") is None
    assert database.get_run("new")["status"] == "SUCCEEDED"
    assert database.get_logs("old") == database.get_events("old") == database.get_artifacts("old") == []
    assert json.loads(output.read_text())["payload_hex"] == "new"
    assert iq.read_bytes() == b'\0' * 32
    assert prune_overwritten_runs(database, "new") == []


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "FAILED", "CANCELLED"])
def test_incomplete_or_failed_replacement_keeps_old_task(database, tmp_path, status):
    path = tmp_path / "same.jsonl"
    result(database, "old", path)
    result(database, "new", path, status=status)
    assert prune_overwritten_runs(database, "new") == []
    assert database.get_run("old") is not None


def test_missing_or_externally_changed_new_output_does_not_trigger_deletion(database, tmp_path):
    path = tmp_path / "same.jsonl"
    result(database, "old", path)
    result(database, "new", path)
    path.write_text("external modification with a different size", encoding="utf-8")
    assert prune_overwritten_runs(database) == []
    path.unlink()
    assert prune_overwritten_runs(database) == []
    assert database.get_run("old") is not None


def test_remaining_independent_output_keeps_old_task(database, tmp_path):
    frames, timeline = tmp_path / "same.jsonl", tmp_path / "timeline.html"
    result(database, "old", frames, command="sync")
    timeline.write_text("old timeline", encoding="utf-8")
    database.insert_artifact("old", None, str(timeline), "sync_timeline_html", {"file_version": file_version(timeline)}, "now")
    result(database, "new", frames, command="sync")
    assert prune_overwritten_runs(database, "new") == []
    timeline.write_text("new timeline", encoding="utf-8")
    database.insert_artifact("new", None, str(timeline), "sync_timeline_html", {"file_version": file_version(timeline)}, "now")
    assert prune_overwritten_runs(database, "new") == ["old"]
    assert timeline.read_text() == "new timeline"


def test_shared_input_and_unrelated_task_do_not_trigger_cleanup(database, tmp_path):
    result(database, "old", tmp_path / "old.jsonl")
    result(database, "new", tmp_path / "new.jsonl")
    shared = str(tmp_path / "shared.iq")
    for id in ("old", "new"):
        database.insert_artifact(id, None, shared, "input_iq", {}, "now")
    assert prune_overwritten_runs(database, "new") == []
    assert {run["id"] for run in database.get_runs()} == {"old", "new"}


def test_auto_cleanup_rechecks_status_and_handles_concurrent_deletions(database, tmp_path):
    result(database, "old", tmp_path / "same.jsonl")
    result(database, "new", tmp_path / "same.jsonl")
    database.update_run_status("old", "RUNNING")
    assert database.delete_runs(["old", "missing"], skip_unavailable=True) == []
    assert database.get_run("old")["status"] == "RUNNING"


@pytest.mark.anyio
async def test_startup_prunes_existing_overwritten_legacy_history(database, tmp_path, monkeypatch):
    from webui.backend.app.main import app, lifespan
    from webui.backend.app.jobs import manager as jobs_module
    for id in ("old", "older", "latest"):
        result(database, id, tmp_path / "same.jsonl", legacy=True)
    jobs = SimpleNamespace(shutdown=AsyncMock())
    monkeypatch.setattr(jobs_module, "_JOB_MGR", jobs)
    async with lifespan(app):
        assert [run["id"] for run in database.get_runs()] == ["latest"]
        assert (tmp_path / "same.jsonl").is_file()
    jobs.shutdown.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("exit_code", [0, 2])
async def test_job_prunes_only_after_success_and_notifies_replacement(database, tmp_path, monkeypatch, exit_code):
    from webui.backend.app.jobs import manager as jobs_module
    from webui.backend.app.events import hub as hub_module
    from webui.backend.app.devices import manager as devices_module
    from webui.backend.app.devices.manager import DeviceManager
    from webui.backend.app.events.hub import EventHub
    path = tmp_path / "same.jsonl"
    result(database, "old", path)
    jobs, hub = JobManager(), EventHub()
    monkeypatch.setattr(hub_module, "_EVENT_HUB", hub)
    monkeypatch.setattr(devices_module, "_DEVICE_MGR", DeviceManager())
    changes = []
    async def broadcast(run_id, event):
        if event["type"] == "task.deleted":
            assert database.get_run("old") is None
            assert database.get_run(event["payload"]["replacement_run_id"])["status"] == "SUCCEEDED"
            changes.append(event)
    monkeypatch.setattr(hub, "_broadcast", broadcast)
    async def spawn(*args, **kwargs):
        path.write_text('{"payload_hex":"new"}\n', encoding="utf-8")
        out, err = asyncio.StreamReader(), asyncio.StreamReader()
        event = {"type": "artifact.created", "seq": 1, "payload": {"path": str(path), "kind": "frames_jsonl"}}
        out.feed_data(json.dumps(event).encode() + b'\n')
        out.feed_eof()
        err.feed_eof()
        return SimpleNamespace(stdout=out, stderr=err, returncode=exit_code, wait=AsyncMock(return_value=exit_code))
    monkeypatch.setattr(jobs_module.asyncio, "create_subprocess_exec", spawn)
    run = await jobs.create_and_start_run("demod", {"iq": str(tmp_path / "input.iq"), "output": str(path)})
    await jobs._tasks[run.id]
    assert database.get_run(run.id)["status"] == ("SUCCEEDED" if exit_code == 0 else "FAILED")
    if exit_code == 0:
        assert len(changes) == 1
        assert changes[0]["payload"] == {"run_ids": ["old"], "replacement_run_id": run.id, "reason": "outputs_overwritten"}
    else:
        assert changes == []
        assert database.get_run("old") is not None
