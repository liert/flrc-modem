"""SQLite 数据持久化层：任务历史、日志、事件、产物元数据及预设配置。"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any
from ..paths import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "outputs/webui.db"


def artifact_path_key(path: str) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return os.path.normcase(str(candidate.resolve()))


class Database:
    """线程安全的 SQLite 访问封装。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    command_id TEXT NOT NULL,
                    command_args TEXT NOT NULL,
                    cli_argv TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    exit_code INTEGER,
                    error TEXT,
                    requires_usrp INTEGER DEFAULT 0,
                    sdr_type TEXT,
                    pipeline_id TEXT,
                    summary TEXT
                );

                CREATE TABLE IF NOT EXISTS run_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    stream TEXT NOT NULL,
                    line TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    stage_id TEXT,
                    timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );

                CREATE TABLE IF NOT EXISTS run_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    stage_id TEXT,
                    path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );

                CREATE TABLE IF NOT EXISTS presets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    values_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_run_logs_run_id ON run_logs(run_id);
                CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events(run_id);
                CREATE INDEX IF NOT EXISTS idx_run_artifacts_run_id ON run_artifacts(run_id);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(run_artifacts)")}
            if "path_key" not in columns:
                conn.execute("ALTER TABLE run_artifacts ADD COLUMN path_key TEXT")
            for row in conn.execute("SELECT id, path FROM run_artifacts WHERE path_key IS NULL").fetchall():
                conn.execute("UPDATE run_artifacts SET path_key=? WHERE id=?", (artifact_path_key(row["path"]), row["id"]))
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_path_key ON run_artifacts(path_key, id)")
            run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
            if "name" not in run_columns:
                conn.execute("ALTER TABLE runs ADD COLUMN name TEXT NOT NULL DEFAULT ''")
            if "deleted_at" not in run_columns:
                conn.execute("ALTER TABLE runs ADD COLUMN deleted_at TEXT")
            conn.commit()

    def mark_lost_processes_on_startup(self) -> int:
        """后端重启时，将未完成的历史任务标记为失败。"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE runs
                SET status = 'FAILED', error = '服务重启，子进程已终止或失联'
                WHERE status IN ('QUEUED', 'RUNNING')
                """
            )
            count = cursor.rowcount
            conn.commit()
            return count

    def insert_run(self, run: dict[str, Any]) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    id, command_id, command_args, cli_argv, status,
                    created_at, started_at, completed_at, exit_code,
                    error, requires_usrp, sdr_type, pipeline_id, summary, name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["id"],
                    run["command_id"],
                    json.dumps(run["command_args"], ensure_ascii=False),
                    json.dumps(run["cli_argv"], ensure_ascii=False),
                    run["status"],
                    run["created_at"],
                    run.get("started_at"),
                    run.get("completed_at"),
                    run.get("exit_code"),
                    run.get("error"),
                    1 if run.get("requires_usrp") else 0,
                    run.get("sdr_type"),
                    run.get("pipeline_id"),
                    json.dumps(run.get("summary") or {}, ensure_ascii=False),
                    run.get("name") or "",
                ),
            )
            conn.commit()

    def update_run_status(
        self,
        run_id: str,
        status: str,
        started_at: str | None = None,
        completed_at: str | None = None,
        exit_code: int | None = None,
        error: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._get_connection() as conn:
            fields = ["status = ?"]
            params: list[Any] = [status]
            if started_at is not None:
                fields.append("started_at = ?")
                params.append(started_at)
            if completed_at is not None:
                fields.append("completed_at = ?")
                params.append(completed_at)
            if exit_code is not None:
                fields.append("exit_code = ?")
                params.append(exit_code)
            if error is not None:
                fields.append("error = ?")
                params.append(error)
            if summary is not None:
                fields.append("summary = ?")
                params.append(json.dumps(summary, ensure_ascii=False))

            params.append(run_id)
            query = f"UPDATE runs SET {', '.join(fields)} WHERE id = ?"
            conn.execute(query, params)
            conn.commit()

    def insert_log(self, run_id: str, stream: str, line: str, timestamp: str) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                "INSERT INTO run_logs (run_id, stream, line, timestamp) VALUES (?, ?, ?, ?)",
                (run_id, stream, line, timestamp),
            )
            conn.commit()

    def insert_event(
        self, run_id: str, seq: int, type_: str, stage_id: str | None, timestamp: str, payload: dict[str, Any]
    ) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                "INSERT INTO run_events (run_id, seq, type, stage_id, timestamp, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, seq, type_, stage_id, timestamp, json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()

    def insert_artifact(
        self, run_id: str, stage_id: str | None, path: str, kind: str, metadata: dict[str, Any], created_at: str
    ) -> None:
        with self._lock, self._get_connection() as conn:
            # 同一任务先登记正在写入的文件，结束时更新其统计，不产生重复条目。
            path = os.path.normpath(path)
            path_key = artifact_path_key(path)
            previous = conn.execute("SELECT id FROM run_artifacts WHERE run_id = ? AND path_key = ?",
                                    (run_id, path_key)).fetchone()
            if previous is not None:
                conn.execute("UPDATE run_artifacts SET stage_id=?, kind=?, metadata=?, created_at=? WHERE id=?",
                             (stage_id, kind, json.dumps(metadata, ensure_ascii=False), created_at, previous["id"]))
                return
            conn.execute(
                "INSERT INTO run_artifacts (run_id, stage_id, path, path_key, kind, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, stage_id, path, path_key, kind, json.dumps(metadata, ensure_ascii=False), created_at),
            )
            conn.commit()

    def get_runs(self, limit: int = 100, offset: int = 0, *, status: str | None = None,
                 search: str = "") -> list[dict[str, Any]]:
        with self._lock, self._get_connection() as conn:
            conditions, params = ["deleted_at IS NULL"], []
            if status:
                conditions.append("status = ?")
                params.append(status)
            if search:
                conditions.append("instr(lower(id || ' ' || command_id || ' ' || name || ' ' || command_args || ' ' || created_at), lower(?)) > 0")
                params.append(search)
            cursor = conn.execute(
                f"SELECT * FROM runs WHERE {' AND '.join(conditions)} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["command_args"] = json.loads(d["command_args"])
                d["cli_argv"] = json.loads(d["cli_argv"])
                d["summary"] = json.loads(d["summary"]) if d.get("summary") else {}
                d["requires_usrp"] = bool(d["requires_usrp"])
                results.append(d)
            return results

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM runs WHERE id = ? AND deleted_at IS NULL", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            d["command_args"] = json.loads(d["command_args"])
            d["cli_argv"] = json.loads(d["cli_argv"])
            d["summary"] = json.loads(d["summary"]) if d.get("summary") else {}
            d["requires_usrp"] = bool(d["requires_usrp"])
            return d

    def rename_run(self, run_id: str, name: str) -> None:
        with self._lock, self._get_connection() as conn:
            result = conn.execute("UPDATE runs SET name=? WHERE id=? AND deleted_at IS NULL", (name, run_id))
            if not result.rowcount:
                raise KeyError(run_id)

    def delete_runs(self, run_ids: list[str]) -> list[str]:
        ids = list(dict.fromkeys(run_ids))
        if not ids:
            return []
        placeholders = ','.join('?' for _ in ids)
        with self._lock, self._get_connection() as conn:
            # 批量删除为一个事务，包含活动任务或不存在的任务时不做部分删除。
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(f"SELECT id, status FROM runs WHERE id IN ({placeholders}) AND deleted_at IS NULL", ids).fetchall()
            if len(rows) != len(ids):
                raise KeyError("部分任务不存在或已删除")
            if any(row["status"] in ("QUEUED", "RUNNING") for row in rows):
                raise ValueError("请先停止运行中或排队中的任务，再删除")
            conn.execute(f"UPDATE runs SET deleted_at=? WHERE id IN ({placeholders})",
                         (datetime.now(timezone.utc).isoformat(), *ids))
            for table in ("run_logs", "run_events"):
                conn.execute(f"DELETE FROM {table} WHERE run_id IN ({placeholders})", ids)
            # 保留内部文件归属记录，防止删除最新任务后把其同名输出误认作旧任务的文件。
            return ids

    def get_logs(self, run_id: str, limit: int = 2000) -> list[dict[str, Any]]:
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT stream, line, timestamp FROM (SELECT id, stream, line, timestamp FROM run_logs WHERE run_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                (run_id, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_events(self, run_id: str, limit: int = 2000) -> list[dict[str, Any]]:
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT seq, type, stage_id, timestamp, payload FROM (SELECT id, seq, type, stage_id, timestamp, payload FROM run_events WHERE run_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                (run_id, limit),
            )
            rows = []
            for r in cursor.fetchall():
                d = dict(r)
                d["payload"] = json.loads(d["payload"])
                rows.append(d)
            return rows

    def get_artifacts(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock, self._get_connection() as conn:
            if run_id:
                cursor = conn.execute(
                    "SELECT id, run_id, stage_id, path, kind, metadata, created_at FROM run_artifacts WHERE run_id = ? AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.id=run_id AND r.deleted_at IS NOT NULL) ORDER BY id DESC",
                    (run_id,),
                )
            else:
                cursor = conn.execute(
                    "SELECT id, run_id, stage_id, path, kind, metadata, created_at FROM run_artifacts WHERE NOT EXISTS (SELECT 1 FROM runs r WHERE r.id=run_id AND r.deleted_at IS NOT NULL) ORDER BY id DESC LIMIT 200"
                )
            rows = []
            for r in cursor.fetchall():
                d = dict(r)
                d["metadata"] = json.loads(d["metadata"])
                rows.append(d)
            return rows

    def get_presets(self, command_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock, self._get_connection() as conn:
            if command_id:
                cursor = conn.execute(
                    "SELECT * FROM presets WHERE command_id = ? ORDER BY updated_at DESC", (command_id,)
                )
            else:
                cursor = conn.execute("SELECT * FROM presets ORDER BY updated_at DESC")
            results = []
            for r in cursor.fetchall():
                d = dict(r)
                d["values"] = json.loads(d["values_json"])
                results.append(d)
            return results

    def get_artifact(self, artifact_id: int) -> dict[str, Any] | None:
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT * FROM run_artifacts WHERE id = ? AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.id=run_id AND r.deleted_at IS NOT NULL)", (artifact_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["metadata"] = json.loads(result["metadata"])
            return result

    def get_latest_writers(self, paths) -> dict[str, dict[str, Any]]:
        keys = list(dict.fromkeys(artifact_path_key(path) for path in paths if path))
        if not keys:
            return {}
        with self._lock, self._get_connection() as conn:
            placeholders = ','.join('?' for _ in keys)
            rows = conn.execute(f"""
                SELECT a.*, r.status AS run_status, r.deleted_at AS run_deleted_at FROM run_artifacts a
                JOIN (SELECT path_key, MAX(id) AS id FROM run_artifacts
                      WHERE path_key IN ({placeholders}) AND kind != 'input_iq' GROUP BY path_key) latest
                  ON a.id = latest.id
                LEFT JOIN runs r ON a.run_id = r.id
            """, keys).fetchall()
            return {row["path_key"]: {**dict(row), "metadata": json.loads(row["metadata"])} for row in rows}

    def save_preset(self, preset_id: str, name: str, command_id: str, values: dict[str, Any], updated_at: str) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO presets (id, name, command_id, values_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    command_id = excluded.command_id,
                    values_json = excluded.values_json,
                    updated_at = excluded.updated_at
                """,
                (preset_id, name, command_id, json.dumps(values, ensure_ascii=False), updated_at),
            )
            conn.commit()


_DB: Database | None = None


def get_db() -> Database:
    global _DB
    if _DB is None:
        _DB = Database()
    return _DB
