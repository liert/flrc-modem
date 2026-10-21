"""配置与业务模板管理模块：动态扫描、全自动识别并提供 templates/ 目录下的 TOML 物理预设与 JSON 发送报文模板。"""
from __future__ import annotations

import json
from pathlib import Path
import tomllib
from tempfile import NamedTemporaryFile
from typing import Any

from ..schemas import TemplateItem
from ..paths import PROJECT_ROOT

TEMPLATES_DIR = PROJECT_ROOT / "templates"


def infer_template_metadata(
    file_path: Path, raw_text: str, parsed: dict[str, Any]
) -> dict[str, Any]:
    """根据文件后缀、顶部注释行及内部数据结构，全自动推断模板的分类、标题、描述与适用命令。

    设计原则：
    1. 零硬编码：放入 templates/ 的任意 .toml 或 .json 文件均可被 WebUI 自动识别并呈现。
    2. 优雅提取：从文件头部注释行提取标题与说明，从字段结构自动推断属于物理配置还是发送报文。
    3. 智能关联：自动绑定到适用的 CLI 子命令（如 demod、live、transmit、duplex、spectrum 等）。
    """
    name = file_path.name
    suffix = file_path.suffix.lower()
    metadata = parsed.get('template', {})
    if not isinstance(metadata, dict):
        raise ValueError('template 元数据必须为对象')
    if metadata and (type(metadata.get('schema_version')) is not int or metadata['schema_version'] != 1):
        raise ValueError('template.schema_version 必须为 1')
    kind = metadata.get('kind')
    if kind is not None and kind not in ('physical_config', 'rf_config', 'message_payload'):
        raise ValueError('未知 template.kind')
    if kind == 'physical_config' and suffix != '.toml' or kind in ('rf_config', 'message_payload') and suffix != '.json':
        raise ValueError('物理模板使用 TOML，射频与报文模板使用 JSON')
    has_rf = 'hopping_table' in parsed or isinstance(parsed.get('radio_config'), dict) and 'hopping_table' in parsed['radio_config']
    if kind == 'rf_config' and not has_rf or kind == 'message_payload' and has_rf:
        raise ValueError('template.kind 与模板内容不一致')

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
        if metadata.get('title'):
            title = str(metadata['title'])
        elif parsed.get("title"):
            title = str(parsed["title"])
        elif parsed.get("name"):
            title = str(parsed["name"])
        if metadata.get('description'):
            description = str(metadata['description'])
        elif parsed.get("description"):
            description = str(parsed["description"])

    # 3. 自动识别分类与适用命令
    category = "physical_config" if suffix == ".toml" else "message_payload"
    applicable_commands: list[str] = []

    if suffix == ".toml":
        category = "physical_config"
        demod_sec = parsed.get("demod") if isinstance(parsed, dict) else None
        mod_sec = parsed.get("modulate") if isinstance(parsed, dict) else None

        if demod_sec or any(k in parsed for k in ("sample_rate", "frequencies", "bit_rate")):
            applicable_commands = ["demod", "live", "modulate", "transmit", "duplex"]
        elif mod_sec or "payload_hex" in parsed:
            applicable_commands = ["modulate", "tx"]
        else:
            applicable_commands = ["demod", "live", "modulate", "transmit", "duplex"]

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
        radio = parsed.get("radio_config")
        if "hopping_table" in parsed or isinstance(radio, dict) and "hopping_table" in radio:
            from flrc_modem.sync.live import parse_rf_config
            table, sync = parse_rf_config(parsed)
            if not table:
                raise ValueError("射频配置需要非空 hopping_table")
            return {"title": title or f"{file_path.stem} 跳频配置",
                    "description": description or f"{len(table)} 个有序跳频点 · OTA 同步字 {sync or '从空口恢复'}",
                    "category": "rf_config", "applicable_commands": ["duplex", "sync"]}
        category = "message_payload"
        applicable_commands = ["transmit", "duplex", "exchange"]

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
        self.templates_dir = templates_dir.resolve()
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        relative = Path(name.replace('\\', '/'))
        target = (self.templates_dir / relative).resolve()
        if relative.is_absolute() or '..' in relative.parts or not target.is_relative_to(self.templates_dir) or target.suffix.lower() not in (".toml", ".json"):
            raise ValueError("模板必须为 templates 目录内的 TOML 或 JSON 文件")
        # 兼容旧根目录模板名；重名时不猜测。
        if not target.exists() and len(relative.parts) == 1:
            matches = [self.templates_dir / folder / relative for folder in ('physical', 'rf', 'messages', 'spectrum')
                       if (self.templates_dir / folder / relative).is_file()]
            if len(matches) == 1:
                target = matches[0].resolve()
                if not target.is_relative_to(self.templates_dir):
                    raise ValueError('模板路径不能指向 templates 目录之外')
        return target

    @staticmethod
    def _parse(path: Path, content: str) -> dict[str, Any]:
        if len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("模板大小不能超过 1MiB")
        parsed = tomllib.loads(content) if path.suffix.lower() == ".toml" else json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("模板顶层必须为对象")
        # 不允许 JSON NaN/Infinity 污染响应，造成序列化失败。
        try:
            json.dumps(parsed, allow_nan=False)
        except (ValueError, TypeError):
            raise ValueError("模板包含不能序列化的值") from None
        return parsed

    @staticmethod
    def _display_path(path: Path) -> str:
        return str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path)

    def list_templates(self, command_id: str | None = None) -> list[TemplateItem]:
        items: list[TemplateItem] = []
        if not self.templates_dir.is_dir():
            return items

        for file_path in sorted(self.templates_dir.rglob('*')):
            if not file_path.is_file():
                continue
            name = file_path.relative_to(self.templates_dir).as_posix()
            suffix = file_path.suffix.lower()
            if suffix not in (".toml", ".json"):
                continue
            try:
                file_path = self._path(name)
                raw_text = file_path.read_text(encoding="utf-8")
                parsed_data = self._parse(file_path, raw_text)
                meta = infer_template_metadata(file_path, raw_text, parsed_data)
            except (OSError, ValueError, TypeError):
                continue

            applicable = meta.get("applicable_commands", [])
            if command_id and command_id not in applicable:
                continue

            items.append(
                TemplateItem(
                    name=name,
                    path=self._display_path(file_path),
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
        target = self._path(name)
        if not target.is_file():
            return None

        suffix = target.suffix.lower()
        raw_text = target.read_text(encoding="utf-8", errors="replace")
        parsed_data = self._parse(target, raw_text)

        meta = infer_template_metadata(target, raw_text, parsed_data)

        return TemplateItem(
            name=target.relative_to(self.templates_dir).as_posix(),
            path=self._display_path(target),
            format="toml" if suffix == ".toml" else "json",
            category=meta.get("category", "physical_config"),
            title=meta.get("title", name),
            description=meta.get("description", ""),
            applicable_commands=meta.get("applicable_commands", []),
            raw_content=raw_text,
            parsed=parsed_data,
        )

    def save_template(self, name: str, content: str) -> TemplateItem:
        target = self._path(name)
        parsed = self._parse(target, content)
        infer_template_metadata(target, content, parsed)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=target.parent, suffix=".tmp", mode="w", encoding="utf-8",
                                    delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        item = self.get_template(name)
        if not item:
            raise RuntimeError("模板保存后读取失败")
        return item

    def delete_template(self, name: str) -> dict[str, Any]:
        target = self._path(name)
        if not target.is_file():
            raise FileNotFoundError(f"模板未找到: {name}")
        target.unlink()
        return {"name": target.relative_to(self.templates_dir).as_posix(), "deleted": True}


_TEMPLATE_MGR: TemplateManager | None = None


def get_template_manager() -> TemplateManager:
    global _TEMPLATE_MGR
    if _TEMPLATE_MGR is None:
        _TEMPLATE_MGR = TemplateManager()
    return _TEMPLATE_MGR
