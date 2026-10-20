"""自动移除结果已完全被成功任务覆盖的旧解调任务，不删除磁盘文件。"""
from collections import defaultdict

from .lifecycle import file_version
from ..storage.db import artifact_path_key


def prune_overwritten_runs(db, replacing_run_id: str | None = None) -> list[str]:
    artifacts = db.get_cleanup_artifacts()
    writers = db.get_latest_writers(item["path"] for item in artifacts)
    versions = {}
    outputs = defaultdict(list)
    for artifact in artifacts:
        outputs[artifact["run_id"]].append(artifact)

    def covered(artifact):
        key = artifact_path_key(artifact["path"])
        writer = writers.get(key)
        if not writer or writer["run_id"] == artifact["run_id"] or writer["run_status"] != "SUCCEEDED":
            return False
        if key not in versions:
            versions[key] = file_version(writer["path"])
        current = versions[key]
        recorded = writer["metadata"].get("file_version")
        return current is not None and (not recorded or current == recorded)

    obsolete = []
    for run_id, items in outputs.items():
        frames = [item for item in items if item["kind"] == "frames_jsonl"]
        if not frames:
            continue
        if replacing_run_id and not any(
            writers.get(artifact_path_key(item["path"]), {}).get("run_id") == replacing_run_id for item in frames
        ):
            continue
        # 同步任务可能还有可用时间轴等独立结果；只有所有输出都被覆盖才清理。
        if all(covered(item) for item in items):
            obsolete.append(run_id)
    return db.delete_runs(obsolete, skip_unavailable=True)
