"""记录廉价文件版本，阻止历史任务误读同名的新结果；不额外备份文件。"""
from pathlib import Path

from ..paths import PROJECT_ROOT
from ..storage.db import artifact_path_key

ACTIVE = ("QUEUED", "RUNNING")


def file_version(path: str | Path) -> dict | None:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        stat = candidate.stat()
        if not candidate.is_file():
            return None
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
                "inode": stat.st_ino, "device": stat.st_dev}
    except OSError:
        return None


def file_state(path, artifact=None, *, run_id=None, run_status=None, writer=None, output=False, attempt=1) -> dict:
    def state(status, readable, message=None, version=None):
        return {"status": status, "readable": readable, "message": message,
                "revision": f"{status}:{version}", "current_run_id": writer["run_id"] if writer and not writer.get("run_deleted_at") else None}
    if not path:
        return state("not_configured", False)
    current = file_version(path)
    if current is None:
        if output and run_status in ACTIVE and artifact is None:
            return state("pending", False, "结果文件尚未生成。")
        return state("missing", False, "文件已删除或移动；任务记录保留，文件内容无法读取。")
    if output and run_status in ACTIVE and artifact is None:
        return state("pending", False, "本次执行尚未生成结果。", current)
    if output and writer and (writer["run_id"] != run_id or writer.get("attempt", 1) != attempt):
        if run_status in ACTIVE and artifact is None:
            return state("pending", False, "本次任务尚未生成结果，当前同名文件属于之前的任务。", current)
        message = ("同名文件已由此任务的后续执行覆盖，请选择最新执行查看结果。" if writer["run_id"] == run_id
                   else "同名文件已由其他任务覆盖；覆盖它的任务记录已删除。" if writer.get("run_deleted_at")
                   else "同名文件已由其他任务覆盖，请切换到该任务查看新结果。")
        return state("overwritten", False, message, current)
    metadata = artifact.get("metadata", {}) if artifact else {}
    recorded = metadata.get("file_version")
    if recorded and current != recorded and not (output and run_status in ACTIVE):
        return state("modified", False, "文件已被修改或替换，当前内容不能作为此任务的原始结果。", current)
    if not recorded:
        size = metadata.get("size_bytes")
        if output and size is not None and size != current["size"] and run_status not in ACTIVE:
            return state("modified", False, "文件大小与任务记录不符，结果可能已被覆盖。", current)
        return state("unverified", True, "历史记录没有文件版本信息，无法确认文件是否保持原样。", current)
    return state("available", True, version=current)


def context_file_states(context: dict, run: dict, artifacts: list[dict], db) -> dict:
    paths = (context.get("iq_path"), context.get("frames_path"))
    writers = db.get_latest_writers(paths)
    states = {}
    for name, path in zip(("iq", "frames"), paths):
        key = artifact_path_key(path) if path else None
        artifact = next((item for item in artifacts if artifact_path_key(item["path"]) == key), None)
        states[name] = file_state(path, artifact, run_id=run["id"], run_status=run["status"],
                                  attempt=run.get("attempt", 1),
                                  writer=writers.get(key), output=name == "frames" or run["command_id"] in ("modulate", "rx"))
    return states


def describe_artifacts(artifacts: list[dict], db) -> list[dict]:
    writers = db.get_latest_writers(item["path"] for item in artifacts)
    result = []
    runs = {}
    for item in artifacts:
        writer = writers.get(artifact_path_key(item["path"]))
        key = (item["run_id"], item.get("attempt", 1))
        if key not in runs:
            runs[key] = db.get_run_attempt(*key)
        status = (runs[key] or {}).get("status")
        result.append({**item, "file_state": file_state(item["path"], item, run_id=item["run_id"],
                                                       attempt=item.get("attempt", 1),
                                                       run_status=status, writer=writer, output=item["kind"] != "input_iq")})
    return result
