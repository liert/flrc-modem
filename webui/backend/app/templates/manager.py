"""配置与业务模板管理模块：动态扫描、全自动识别并提供 templates/ 目录下的 TOML 物理预设与 JSON 发送报文模板。"""
from __future__ import annotations

import json
from pathlib import Path
import tomllib
from typing import Any

from ..schemas import TemplateItem

TEMPLATES_DIR = Path("templates").resolve()


def infer_template_metadata(
    file_path: Path, raw_text: str, parsed: dict[str, Any]
) -> dict[str, Any]:
    """根据文件后缀、顶部注释行及内部数据结构，全自动推断模板的分类、标题、描述与适用命令。

    设计原则：
    1. 零硬编码：放入 templates/ 的任意 .toml 或 .json 文件均可被 WebUI 自动识别并呈现。
    2. 优雅提取：从文件头部注释行提取标题与说明，从字段结构自动推断属于物理配置还是发送报文。
    3. 智能关联：自动绑定到适用的 CLI 子命令（如 demod、live、transmit、duplex 等）。
    """
    name = file_path.name
    suffix = file_path.suffix.lower()

    # 1. 尝试从文件顶部提取多行注释（尤其适用于 TOML / 带有头部说明的文件）
    comment_lines: list[str] = []
    for line in raw_text.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("#"):
            cleaned = line_stripped.lstrip("#").strip()
            if cleaned:
                comment_lines.append(cleaned)
        elif line_stripped:
            # 遇到首个非空且非注释的代码行，停止扫描
            break

    title = ""
    description = ""
    if comment_lines:
        title = comment_lines[0]
        if len(comment_lines) > 1:
            description = " ".join(comment_lines[1:])

    # 2. 如果文件内容本身显式声明了 title / name / description 字段，以其为准
    if isinstance(parsed, dict):
        if parsed.get("title"):
            title = str(parsed["title"])
        elif parsed.get("name"):
            title = str(parsed["name"])
        if parsed.get("description"):
            description = str(parsed["description"])

    # 3. 自动识别分类与适用命令
    category = "physical_config" if suffix == ".toml" else "message_payload"
    applicable_commands: list[str] = []

    if suffix == ".toml":
        category = "physical_config"
        demod_sec = parsed.get("demod") if isinstance(parsed, dict) else None
        mod_sec = parsed.get("modulate") if isinstance(parsed, dict) else None

        if demod_sec or any(k in parsed for k in ("sample_rate", "frequencies", "bit_rate")):
            applicable_commands = ["demod", "live"]
        elif mod_sec or "payload_hex" in parsed:
            applicable_commands = ["modulate", "tx"]
        else:
            applicable_commands = ["demod", "live"]

        if not title:
            stem_clean = file_path.stem.replace("_", " ").title()
            title = f"{stem_clean} 物理层预设"
        if not description:
            cfg = demod_sec if isinstance(demod_sec, dict) else parsed
            details: list[str] = []
            if isinstance(cfg, dict):
                sr = cfg.get("sample_rate")
                br = cfg.get("bit_rate")
                cr = cfg.get("coding_rate")
                if sr:
                    details.append(f"采样率 {sr/1e6:.1f}MSps")
                if br:
                    details.append(f"码率 {br/1e3:.0f}kbps")
                if cr:
                    details.append(f"CR={cr}")
            desc_detail = "、".join(details) if details else "物理层射频参数"
            description = f"物理层参数配置 ({desc_detail})"

    elif suffix == ".json":
        category = "message_payload"
        applicable_commands = ["transmit", "duplex"]

        cmd = parsed.get("command") if isinstance(parsed, dict) else None
        fields = parsed.get("fields") if isinstance(parsed, dict) else {}
        has_channels = isinstance(fields, dict) and "channels_raw" in fields

        if has_channels:
            if not title:
                title = f"{file_path.stem.replace('_', ' ').title()} 16通道摇杆报文 ({cmd or ''})".strip()
            if not description:
                description = f"16通道遥控量发送报文 (指令 {cmd or '0x11'}, 中立位 1024)"
        elif cmd:
            if not title:
                title = f"{file_path.stem.replace('_', ' ').title()} 控制指令 ({cmd})"
            if not description:
                description = f"协议指令交互模板 (指令代码 {cmd})"
        else:
            if not title:
                title = file_path.stem.replace("_", " ").title()
            if not description:
                description = f"{title} 业务发送报文模板"

    return {
        "title": title or name,
        "description": description,
        "category": category,
        "applicable_commands": applicable_commands,
    }


class TemplateManager:
    """管理 templates/ 目录下的所有配置文件与业务模板，支持零硬编码全自动发现。"""

    def __init__(self, templates_dir: Path = TEMPLATES_DIR):
        self.templates_dir = templates_dir
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    def list_templates(self, command_id: str | None = None) -> list[TemplateItem]:
        items: list[TemplateItem] = []
        if not self.templates_dir.is_dir():
            return items

        for file_path in sorted(self.templates_dir.iterdir()):
            if not file_path.is_file():
                continue
            name = file_path.name
            suffix = file_path.suffix.lower()
            if suffix not in (".toml", ".json"):
                continue

            raw_text = file_path.read_text(encoding="utf-8", errors="replace")
            parsed_data: dict[str, Any] = {}
            try:
                if suffix == ".toml":
                    parsed_data = tomllib.loads(raw_text)
                else:
                    parsed_data = json.loads(raw_text)
            except Exception:
                pass

            # 全自动推断元数据（标题、说明、分类、适用命令）
            meta = infer_template_metadata(file_path, raw_text, parsed_data)

            applicable = meta.get("applicable_commands", [])
            if command_id and command_id not in applicable:
                continue

            items.append(
                TemplateItem(
                    name=name,
                    path=str(file_path.relative_to(Path.cwd())),
                    format="toml" if suffix == ".toml" else "json",
                    category=meta.get("category", "physical_config"),
                    title=meta.get("title", name),
                    description=meta.get("description", ""),
                    applicable_commands=applicable,
                    raw_content=raw_text,
                    parsed=parsed_data,
                )
            )
        return items

    def get_template(self, name: str) -> TemplateItem | None:
        target = (self.templates_dir / name).resolve()
        if not target.is_file() or target.parent != self.templates_dir:
            return None

        suffix = target.suffix.lower()
        raw_text = target.read_text(encoding="utf-8", errors="replace")
        parsed_data: dict[str, Any] = {}
        try:
            if suffix == ".toml":
                parsed_data = tomllib.loads(raw_text)
            else:
                parsed_data = json.loads(raw_text)
        except Exception:
            pass

        meta = infer_template_metadata(target, raw_text, parsed_data)

        return TemplateItem(
            name=name,
            path=str(target.relative_to(Path.cwd())),
            format="toml" if suffix == ".toml" else "json",
            category=meta.get("category", "physical_config"),
            title=meta.get("title", name),
            description=meta.get("description", ""),
            applicable_commands=meta.get("applicable_commands", []),
            raw_content=raw_text,
            parsed=parsed_data,
        )

    def save_template(self, name: str, content: str) -> TemplateItem:
        target = (self.templates_dir / name).resolve()
        if target.parent != self.templates_dir:
            raise PermissionError("非法模板文件路径")

        target.write_text(content, encoding="utf-8")
        item = self.get_template(name)
        if not item:
            raise RuntimeError("模板保存后读取失败")
        return item


_TEMPLATE_MGR: TemplateManager | None = None


def get_template_manager() -> TemplateManager:
    global _TEMPLATE_MGR
    if _TEMPLATE_MGR is None:
        _TEMPLATE_MGR = TemplateManager()
    return _TEMPLATE_MGR
