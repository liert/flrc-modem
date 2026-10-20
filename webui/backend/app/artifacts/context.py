"""按任务关联输入、输出和实际采样参数；兼容未登记输入产物的历史任务。"""
from contextlib import redirect_stderr
import io
from pathlib import Path

from flrc_modem.cli import make_argument_parser
from flrc_modem.cli_options import demod_overrides
from flrc_modem.config import load_config

from ..paths import PROJECT_ROOT


def analysis_context(run: dict, artifacts: list[dict]) -> dict:
    parameters = run.get("command_args") or {}
    command = run["command_id"]
    iq_path = parameters.get("iq") if command in ("demod", "tx") else None
    frames_path = parameters.get("output") if command in ("demod", "live", "sync") else None
    if command in ("modulate", "rx"):
        iq_path = parameters.get("output")
    metadata = {}
    for item in artifacts:
        values = item.get("metadata") or {}
        if item["kind"] == "frames_jsonl" and not frames_path:
            frames_path = item["path"]
        if item["kind"] == "input_iq":
            iq_path = item["path"]
        if not iq_path and item["path"].lower().endswith((".iq", ".cs8", ".cu8", ".cf32")):
            iq_path = item["path"]
        # 先使用录制时的参数快照，不能让之后编辑的 TOML 改写历史任务的参数。
        if not metadata and all(key in values for key in ("sample_rate", "center_frequency", "iq_format")):
            metadata = values
    error = None
    if command == "demod" and not metadata:
        try:
            argv = run.get("cli_argv") or []
            if not argv:
                from ..commands.registry import get_command_registry
                argv = get_command_registry().build_cli_argv(command, parameters, False)
            with redirect_stderr(io.StringIO()):
                args = make_argument_parser().parse_args(argv[4:])
            config_path = args.config
            if config_path is not None and not config_path.is_absolute():
                config_path = PROJECT_ROOT / config_path
            config = load_config(config_path, demod_overrides(args))
            metadata = {key: getattr(config, key) for key in ("sample_rate", "center_frequency", "iq_format")}
        except (OSError, ValueError, TypeError, SystemExit) as err:
            error = f"无法恢复历史任务的采样参数：{err}"
    if not metadata:
        metadata = {"sample_rate": parameters.get("sample_rate"),
                    "center_frequency": parameters.get("center_frequency") or parameters.get("frequency"),
                    "iq_format": parameters.get("iq_format")}
    return {"run_id": run["id"], "command_id": command, "status": run["status"],
            "iq_path": str(Path(iq_path)) if iq_path else None,
            "frames_path": str(Path(frames_path)) if frames_path else None,
            "sample_rate": metadata.get("sample_rate"), "center_frequency": metadata.get("center_frequency"),
            "iq_format": metadata.get("iq_format"), "metadata_error": error,
            "frame_time_reference": "iq_start" if command == "demod" and iq_path else None}
