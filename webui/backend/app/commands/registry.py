"""命令注册中心：集中管理现有 CLI 命令元数据、参数 Schema 及命令行转换规则。"""
from __future__ import annotations

import re
import sys
from math import isfinite
from typing import Any

from ..schemas import CommandDefinition, CommandParameter
from .supplemental import SUPPLEMENTAL_PARAMETERS


class CommandRegistry:
    UI_EVENT_COMMANDS = ("demod", "modulate", "rx", "tx", "sync", "duplex")
    def __init__(self):
        self._commands: dict[str, CommandDefinition] = {}
        self._register_all()

    def get(self, command_id: str) -> CommandDefinition | None:
        return self._commands.get(command_id)

    def list_all(self) -> list[CommandDefinition]:
        return list(self._commands.values())

    def build_cli_argv(
        self, command_id: str, params: dict[str, Any], enable_ui_events: bool = True
    ) -> list[str]:
        cmd = self.get(command_id)
        if not cmd:
            raise ValueError(f"未知命令 ID: {command_id}")

        argv = [sys.executable, "-m", "flrc_modem", cmd.subcommand]

        params = dict(params)
        if command_id in ("transmit", "duplex", "modulate", "exchange"):
            if params.get("session"):
                if command_id != "duplex":
                    raise ValueError("仅同步收发支持常驻会话")
                params.pop("message_file", None)
                params.pop("payload_hex", None)
                params.pop("payload_file", None)
            elif params.get("payload_file"):
                params.pop("message_file", None)
                params.pop("payload_hex", None)
                if command_id == "transmit":
                    params.pop("protocol", None)
            elif params.get("payload_hex"):
                params.pop("message_file", None)
                # Raw physical payload bypasses the application encoder.
                if command_id == "transmit":
                    params.pop("protocol", None)
        if command_id in ("demod", "live") and params.get("parser"):
            params.pop("protocol", None)
        if command_id == "live" and params.get("max_seconds") not in (None, ""):
            params.pop("duration", None)
        param_map = {p.name: p for p in cmd.parameters}

        unknown = set(params) - param_map.keys()
        if unknown:
            raise ValueError(f"未知参数: {', '.join(sorted(unknown))}")
        if command_id in ('transmit', 'duplex', 'modulate', 'exchange') and not params.get('session') and not (params.get('message_file') or params.get('payload_hex') or params.get('payload_file')):
            raise ValueError('请指定原始载荷十六进制、载荷文件或支持的 JSON 报文模板')
        if params.get('serial') and params.get('device_index') is not None and params.get('device_index') != '':
            raise ValueError('serial 与 device_index 互斥')
        if command_id in ('rx', 'tx', 'sync') and params.get('sdr', 'usrp') != 'hackrf' and (params.get('serial') or params.get('device_index') not in (None, '')):
            raise ValueError('USRP 模式请使用 args 定位设备；serial/device_index 仅用于 HackRF')
        for definition in cmd.parameters:
            value = params.get(definition.name)
            if definition.required and (value is None or value == ""):
                if params.get("config") and definition.name in ("frequency", "sample_rate", "sync"):
                    continue
                if command_id == "transmit" and definition.name == "sample_rate":
                    continue
                raise ValueError(f"缺少必填参数: {definition.name}")

        for name in param_map:
            value = params.get(name)
            if name == "action" and value in (None, ""):
                value = param_map[name].default
            if value is None or value == "":
                continue
            param_def = param_map.get(name)
            if param_def.type in ("list_number", "list_string"):
                if isinstance(value, str):
                    value = [item for item in re.split(r"[,，\s]+", value.strip()) if item]
                elif not isinstance(value, list):
                    value = [value]
                if not value:
                    continue
            if param_def.type == "boolean":
                if type(value) is not bool:
                    raise ValueError(f"{name} 必须为布尔值")
            elif param_def.type in ("number", "integer", "list_number"):
                values = value if isinstance(value, list) and param_def.type == "list_number" else [value]
                for item in values:
                    try:
                        number = float(item)
                    except (ValueError, TypeError):
                        raise ValueError(f"{name} 必须为数值") from None
                    if isinstance(item, bool) or not isfinite(number):
                        raise ValueError(f"{name} 必须为有限数值")
                    if param_def.type == "integer" and not number.is_integer():
                        raise ValueError(f"{name} 必须为整数")
                    if param_def.min_value is not None and number < param_def.min_value:
                        raise ValueError(f"{name} 小于允许的最小值")
                    if param_def.max_value is not None and number > param_def.max_value:
                        raise ValueError(f"{name} 超过允许的最大值")
                if param_def.type == "integer":
                    value = int(float(value))
            elif param_def.type == "list_string":
                if any(not isinstance(item, str) for item in value):
                    raise ValueError(f"{name} 必须为字符串列表")
            elif not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ValueError(f"{name} 参数类型错误")
            if param_def.choices is not None:
                matched = None
                for choice in param_def.choices:
                    if str(choice) == str(value):
                        matched = choice
                        break
                    if type(choice) in (int, float) and not isinstance(value, bool):
                        try:
                            if float(value) == choice:
                                matched = choice
                                break
                        except (ValueError, TypeError):
                            pass
                if matched is None:
                    raise ValueError(f"{name} 不是允许的选项")
                value = matched
            flag = param_def.cli_flag
            if not flag:
                argv.append(str(value))
                continue

            if param_def.type == "boolean":
                if bool(value):
                    argv.append(flag)
            elif param_def.type in ("list_number", "list_string"):
                if isinstance(value, list):
                    for item in value:
                        argv.extend([flag, str(item)])
                else:
                    argv.extend([flag, str(value)])
            else:
                argv.extend([flag, str(value)])

        if enable_ui_events and command_id in self.UI_EVENT_COMMANDS:
            argv.extend(["--ui-events", "ndjson"])

        return argv

    def _register_all(self):
        # 1. 离线解调
        self._commands["demod"] = CommandDefinition(
            id="demod",
            name="离线解调 (Demod)",
            category="解调与接收",
            description="对本地 IQ 录制文件执行离线 FLRC 解调，支持多频点、CRC 校验及业务插件解析",
            subcommand="demod",
            requires_usrp=False,
            requires_sdr=False,
            pipeline_id="pipe_demod",
            parameters=[
                CommandParameter(
                    name="iq",
                    type="path",
                    description="交错 I/Q 采样文件路径 (支持 cs8, cu8, cf32)",
                    cli_flag="--iq",
                    required=True,
                    default="",
                ),
                CommandParameter(
                    name="config",
                    type="path",
                    description="可选物理层 TOML 参数配置文件 (推荐使用上方模板库快速载入)",
                    cli_flag="--config",
                    default=None,
                ),
                CommandParameter(
                    name="sample_rate",
                    type="number",
                    description="采样率，单位 Hz (可选，留空由配置文件或 IQ 文件推断)",
                    cli_flag="--sample-rate",
                    default=None,
                ),
                CommandParameter(
                    name="frequency",
                    type="number",
                    description="解调中心载频，单位 Hz (可选，留空由配置文件决定)",
                    cli_flag="--frequency",
                    default=None,
                ),
                CommandParameter(
                    name="bit_rate",
                    type="choice",
                    description="FLRC 空口物理码率，bit/s (可选，留空由配置文件决定)",
                    cli_flag="--bit-rate",
                    choices=[260000, 325000, 520000, 650000, 1040000, 1300000],
                    default=None,
                ),
                CommandParameter(
                    name="cr",
                    type="choice",
                    description="信道编码率 (Coding Rate) (可选，留空由配置文件决定)",
                    cli_flag="--cr",
                    choices=["1/2", "3/4", "1"],
                    default=None,
                ),
                CommandParameter(
                    name="bt",
                    type="choice",
                    description="高斯成形因子 BT (0 关闭，或 0.5/1) (可选，留空由配置文件决定)",
                    cli_flag="--bt",
                    choices=[0.0, 0.5, 1.0],
                    default=None,
                ),
                CommandParameter(
                    name="sync",
                    type="string",
                    description="4B 同步字十六进制 (可选，留空自动盲恢复或由配置文件决定)",
                    cli_flag="--sync",
                    default=None,
                ),
                CommandParameter(
                    name="iq_format",
                    type="choice",
                    description="IQ 采样数据格式 (可选，留空由文件扩展名或配置文件推断)",
                    cli_flag="--iq-format",
                    choices=["cs8", "cu8", "cf32"],
                    default=None,
                ),
                CommandParameter(
                    name="protocol",
                    type="choice",
                    description="业务协议解析插件 (可选，留空默认解析)",
                    cli_flag="--protocol",
                    choices=["dk32s", "dk32s-multi", "datalink"],
                    default=None,
                ),
                CommandParameter(
                    name="output",
                    type="path",
                    description="成功解码物理帧 JSONL 输出文件路径",
                    cli_flag="--output",
                    default="outputs/decoded_frames.jsonl",
                ),
                CommandParameter(
                    name="max_seconds",
                    type="number",
                    description="处理最长输入时间 (0 或留空为不限制)",
                    cli_flag="--max-seconds",
                    default=None,
                ),
            ],
        )

        # 2. 载荷调制
        self._commands["modulate"] = CommandDefinition(
            id="modulate",
            name="信号调制 (Modulate)",
            category="调制与发射",
            description="将原始物理载荷调制为符合 SX1280 物理层规范的标准 FLRC IQ 文件",
            subcommand="modulate",
            requires_usrp=False,
            requires_sdr=False,
            pipeline_id="pipe_modulate",
            parameters=[
                CommandParameter(
                    name="payload_hex",
                    type="string",
                    description="待发送物理载荷十六进制 (6..127 字节)",
                    cli_flag="--payload-hex",
                    required=True,
                    default="0011223344556677",
                ),
                CommandParameter(
                    name="sync",
                    type="string",
                    description="4B 物理同步字十六进制",
                    cli_flag="--sync",
                    required=True,
                    default="b42b2bb4",
                ),
                CommandParameter(
                    name="output",
                    type="path",
                    description="生成的目标 IQ 文件路径",
                    cli_flag="--output",
                    required=True,
                    default="outputs/generated.iq",
                ),
                CommandParameter(
                    name="sample_rate",
                    type="number",
                    description="生成 IQ 的采样率，单位 Hz",
                    cli_flag="--sample-rate",
                    default=10000000.0,
                ),
                CommandParameter(
                    name="bit_rate",
                    type="choice",
                    description="FLRC 空口物理码率，bit/s",
                    cli_flag="--bit-rate",
                    choices=[260000, 325000, 520000, 650000, 1040000, 1300000],
                    default=325000,
                ),
                CommandParameter(
                    name="cr",
                    type="choice",
                    description="信道编码率",
                    cli_flag="--cr",
                    choices=["1/2", "3/4", "1"],
                    default="3/4",
                ),
                CommandParameter(
                    name="bt",
                    type="choice",
                    description="高斯成形因子 BT",
                    cli_flag="--bt",
                    choices=[0.0, 0.5, 1.0],
                    default=1.0,
                ),
                CommandParameter(
                    name="repeat",
                    type="integer",
                    description="突发重复次数",
                    cli_flag="--repeat",
                    default=2,
                    min_value=1,
                ),
                CommandParameter(
                    name="amplitude",
                    type="number",
                    description="归一化输出幅度 (0..1]",
                    cli_flag="--amplitude",
                    default=0.7,
                ),
                CommandParameter(
                    name="iq_format",
                    type="choice",
                    description="IQ 存储格式",
                    cli_flag="--iq-format",
                    choices=["cs8", "cu8", "cf32"],
                    default="cs8",
                ),
                CommandParameter(
                    name="verify",
                    type="boolean",
                    description="落盘后立即执行回读解调与 CRC 校验验证",
                    cli_flag="--verify",
                    default=True,
                ),
            ],
        )

        # 3. 统一 IQ 采集 (rx)
        self._commands["rx"] = CommandDefinition(
            id="rx",
            name="射频采集 (RX Capture)",
            category="SDR 采集与发射",
            description="统一 IQ 采样采集：支持 USRP 与 HackRF 硬件，采集指定时长并保存为原始 IQ 文件",
            subcommand="rx",
            requires_usrp=False,
            requires_sdr=True,
            pipeline_id="pipe_rx",
            parameters=[
                CommandParameter(
                    name="sdr",
                    type="choice",
                    description="SDR 硬件类型 (USRP N310 或 HackRF)",
                    cli_flag="--sdr",
                    choices=["usrp", "hackrf"],
                    default="usrp",
                ),
                CommandParameter(
                    name="frequency",
                    type="number",
                    description="接收中心射频频率，单位 Hz",
                    cli_flag="--frequency",
                    required=True,
                    default=2480000000.0,
                ),
                CommandParameter(
                    name="duration",
                    type="number",
                    description="采集持续时间，单位 秒",
                    cli_flag="--duration",
                    required=True,
                    default=1.0,
                    min_value=0.01,
                ),
                CommandParameter(
                    name="output",
                    type="path",
                    description="输出 IQ 文件路径",
                    cli_flag="--output",
                    required=True,
                    default="outputs/captured.cs8",
                ),
                CommandParameter(
                    name="sample_rate",
                    type="number",
                    description="采样率，单位 Sps (N310 推荐 2500000)",
                    cli_flag="--sample-rate",
                    default=2500000.0,
                ),
                CommandParameter(
                    name="gain",
                    type="number",
                    description="接收射频增益，单位 dB",
                    cli_flag="--gain",
                    default=30.0,
                ),
                CommandParameter(
                    name="iq_format",
                    type="choice",
                    description="输出格式",
                    cli_flag="--iq-format",
                    choices=["cs8", "cu8", "cf32"],
                    default="cs8",
                ),
                CommandParameter(
                    name="args",
                    type="string",
                    description="USRP 定位参数 (如 addr=192.168.10.2)",
                    cli_flag="--args",
                    default="addr=192.168.10.2",
                    category="USRP 硬件参数",
                ),
                CommandParameter(
                    name="antenna",
                    type="string",
                    description="USRP 接收天线端口",
                    cli_flag="--antenna",
                    default="RX2",
                    category="USRP 硬件参数",
                ),
            ],
        )

        # 4. 统一 IQ 发射 (tx)
        self._commands["tx"] = CommandDefinition(
            id="tx",
            name="射频发射 (TX Transmit)",
            category="SDR 采集与发射",
            description="统一 IQ 采样发射：通过 USRP 或 HackRF 硬件，读取本地 IQ 文件并重复发射",
            subcommand="tx",
            requires_usrp=False,
            requires_sdr=True,
            pipeline_id="pipe_tx",
            parameters=[
                CommandParameter(
                    name="sdr",
                    type="choice",
                    description="SDR 硬件类型",
                    cli_flag="--sdr",
                    choices=["usrp", "hackrf"],
                    default="usrp",
                ),
                CommandParameter(
                    name="iq",
                    type="path",
                    description="待发射的 IQ 文件路径",
                    cli_flag="--iq",
                    required=True,
                    default="outputs/generated.iq",
                ),
                CommandParameter(
                    name="frequency",
                    type="number",
                    description="发射中心频率，单位 Hz",
                    cli_flag="--frequency",
                    required=True,
                    default=2480000000.0,
                ),
                CommandParameter(
                    name="sample_rate",
                    type="number",
                    description="发射采样率，单位 Sps",
                    cli_flag="--sample-rate",
                    default=2500000.0,
                ),
                CommandParameter(
                    name="gain",
                    type="number",
                    description="发射增益，单位 dB",
                    cli_flag="--gain",
                    default=30.0,
                ),
                CommandParameter(
                    name="repeat",
                    type="integer",
                    description="重复发射次数",
                    cli_flag="--repeat",
                    default=1,
                    min_value=1,
                ),
                CommandParameter(
                    name="args",
                    type="string",
                    description="USRP 定位参数",
                    cli_flag="--args",
                    default="addr=192.168.10.2",
                    category="USRP 硬件参数",
                ),
            ],
        )

        # 5. 实时 FHSS + TDD 同步分析 (sync)
        self._commands["sync"] = CommandDefinition(
            id="sync",
            name="同步分析 (Sync Engine)",
            category="同步与协议分析",
            description="基于 USRP N310 硬件时间戳的 FHSS 跳频与 TDD 双工实时同步引擎与时间轴视图",
            subcommand="sync",
            requires_usrp=False,
            requires_sdr=True,
            pipeline_id="pipe_sync",
            parameters=[
                CommandParameter(
                    name="sdr",
                    type="choice",
                    description="SDR 硬件类型",
                    cli_flag="--sdr",
                    choices=["usrp", "hackrf"],
                    default="usrp",
                ),
                CommandParameter(
                    name="frequency",
                    type="number",
                    description="初始监听中心频率，单位 Hz",
                    cli_flag="--frequency",
                    default=2471660000.0,
                ),
                CommandParameter(
                    name="duration",
                    type="number",
                    description="监听持续时间，秒 (0 为持续运行至手动停止)",
                    cli_flag="--duration",
                    default=5.0,
                ),
                CommandParameter(
                    name="args",
                    type="string",
                    description="USRP 定位参数",
                    cli_flag="--args",
                    default="addr=192.168.10.2",
                ),
                CommandParameter(
                    name="protocol",
                    type="choice",
                    description="协议解析插件",
                    cli_flag="--protocol",
                    choices=["dk32s"],
                    default="dk32s",
                ),
                CommandParameter(
                    name="output",
                    type="path",
                    description="解调帧输出文件",
                    cli_flag="--output",
                    default="outputs/sync_frames.jsonl",
                ),
                CommandParameter(
                    name="output_html",
                    type="path",
                    description="交互式 HTML 时间轴输出路径",
                    cli_flag="--output-html",
                    default="outputs/live_sync_timeline.html",
                ),
                CommandParameter(
                    name="timed_hopping",
                    type="boolean",
                    description="启用 USRP N310 UHD Timed Commands 硬件跳频调度",
                    cli_flag="--timed-hopping",
                    default=False,
                ),
            ],
        )

        # 6. 硬件诊断: USRP
        self._commands["usrp"] = CommandDefinition(
            id="usrp",
            name="USRP 硬件诊断 (USRP Probe)",
            category="硬件诊断",
            description="探测网络型/USB 型 USRP 设备树结构与射频子板属性",
            subcommand="usrp",
            requires_usrp=True,
            requires_sdr=True,
            parameters=[
                CommandParameter(
                    name="action",
                    type="choice",
                    description="诊断操作",
                    cli_flag="",
                    choices=["info", "probe"],
                    default="info",
                ),
                CommandParameter(
                    name="args",
                    type="string",
                    description="设备定位参数",
                    cli_flag="--args",
                    default="addr=192.168.10.2",
                ),
            ],
        )

        # 7. 硬件诊断: HackRF
        self._commands["hackrf"] = CommandDefinition(
            id="hackrf",
            name="HackRF 硬件诊断 (HackRF Info)",
            category="硬件诊断",
            description="探测本机连接的 HackRF 设备序列号、驱动版本及固件状态",
            subcommand="hackrf",
            requires_usrp=False,
            requires_sdr=True,
            parameters=[
                CommandParameter(
                    name="action",
                    type="choice",
                    description="诊断操作",
                    cli_flag="",
                    choices=["info"],
                    default="info",
                ),
            ],
        )

        # 8. 实时解调 (live)
        self._commands["live"] = CommandDefinition(
            id="live",
            name="实时解调 (Live RX & Demod)",
            category="SDR 采集与发射",
            description="HackRF 实时空中信号接收与流式解调，沿用协议插件与校验机制",
            subcommand="live",
            requires_usrp=False,
            requires_sdr=True,
            parameters=[
                CommandParameter(
                    name="frequency",
                    type="number",
                    description="接收中心载频，单位 Hz",
                    cli_flag="--frequency",
                    default=2480000000.0,
                ),
                CommandParameter(
                    name="config",
                    type="path",
                    description="物理层 TOML 预设配置文件 (如 templates/physical/dk32s_live_2m.toml)",
                    cli_flag="--config",
                    default="templates/physical/dk32s_live_2m.toml",
                ),
                CommandParameter(
                    name="duration",
                    type="number",
                    description="接收时长，秒 (0 为持续接收至手动停止)",
                    cli_flag="--duration",
                    default=10.0,
                ),
                CommandParameter(
                    name="protocol",
                    type="choice",
                    description="协议解析插件",
                    cli_flag="--protocol",
                    choices=["dk32s", "dk32s-multi", "datalink"],
                    default="dk32s",
                ),
                CommandParameter(
                    name="output",
                    type="path",
                    description="实时解码物理帧 JSONL 输出路径",
                    cli_flag="--output",
                    default="outputs/live_decoded.jsonl",
                ),
                CommandParameter(
                    name="lna_gain",
                    type="integer",
                    description="LNA 接收增益 (0..40, 步进 8)",
                    cli_flag="--lna-gain",
                    default=16,
                ),
                CommandParameter(
                    name="vga_gain",
                    type="integer",
                    description="VGA 接收增益 (0..62, 步进 2)",
                    cli_flag="--vga-gain",
                    default=16,
                ),
                CommandParameter(
                    name="amp",
                    type="boolean",
                    description="启用 HackRF 14dB RF 前置放大器",
                    cli_flag="--amp",
                    default=False,
                ),
            ],
        )

        # 9. 实时报文发送 (transmit)
        self._commands["transmit"] = CommandDefinition(
            id="transmit",
            name="盲发（HackRF）",
            category="调制与发射",
            description="将 JSON 业务模板调制后通过 HackRF 直接发送，不等待同步锁定或接收对端回复。",
            subcommand="transmit",
            requires_usrp=False,
            requires_sdr=True,
            parameters=[
                CommandParameter(
                    name="message_file",
                    type="path",
                    description="协议业务字段 JSON 模板文件",
                    cli_flag="--message-file",
                    required=True,
                    default="templates/messages/dk32s_tx_channels.json",
                ),
                CommandParameter(
                    name="frequency",
                    type="integer",
                    description="HackRF 发射中心频率，单位 Hz",
                    cli_flag="--frequency",
                    required=True,
                    default=2480000000,
                ),
                CommandParameter(
                    name="protocol",
                    type="choice",
                    description="发送编码插件",
                    cli_flag="--protocol",
                    choices=["dk32s"],
                    default="dk32s",
                ),
                CommandParameter(
                    name="repeat",
                    type="integer",
                    description="重复发射次数 (0 为持续循环发送)",
                    cli_flag="--repeat",
                    default=10,
                    min_value=0,
                ),
                CommandParameter(
                    name="tx_gain",
                    type="integer",
                    description="HackRF 发射增益 (0..47 dB)",
                    cli_flag="--tx-gain",
                    default=40,
                ),
                CommandParameter(
                    name="bit_rate",
                    type="choice",
                    description="FLRC 空口物理码率，bit/s",
                    cli_flag="--bit-rate",
                    choices=[260000, 325000, 520000, 650000, 1040000, 1300000],
                    default=325000,
                ),
                CommandParameter(
                    name="cr",
                    type="choice",
                    description="信道编码率 (Coding Rate)",
                    cli_flag="--cr",
                    choices=["1/2", "3/4", "1"],
                    default="3/4",
                ),
                CommandParameter(
                    name="bt",
                    type="choice",
                    description="高斯成形因子 BT",
                    cli_flag="--bt",
                    choices=[0.0, 0.5, 1.0],
                    default=1.0,
                ),
                CommandParameter(
                    name="gap_ms",
                    type="number",
                    description="每个突发后最小静默时间，毫秒",
                    cli_flag="--gap-ms",
                    default=10.0,
                ),
                CommandParameter(
                    name="amp",
                    type="boolean",
                    description="启用 HackRF 发射后置放大器",
                    cli_flag="--amp",
                    default=False,
                ),
            ],
        )

        self._commands["duplex"] = CommandDefinition(
            id="duplex",
            name="同步时隙收发（USRP）",
            category="调制与发射",
            description="USRP 持续接收并锁定 FHSS/TDD 时隙后，精准发送一次报文并等待匹配回复；同步未锁定时不发送。",
            subcommand="duplex",
            requires_usrp=True,
            requires_sdr=True,
            parameters=[
                CommandParameter(name="message_file", type="path", cli_flag="--message-file", required=True,
                                 default="templates/messages/dk32s_tx_get_hops.json", description="本次发送的协议 JSON 模板"),
                CommandParameter(name="protocol", type="choice", cli_flag="--protocol", choices=["dk32s"],
                                 default="dk32s", description="业务报文编码与回复匹配协议"),
                CommandParameter(name="frequency", type="integer", cli_flag="--frequency", default=2471660000,
                                 min_value=1, description="初始接收中心频率，Hz；后续由跳频状态机调度"),
                CommandParameter(name="args", type="string", cli_flag="--args", default="addr=192.168.10.2",
                                 description="USRP 设备定位参数"),
                CommandParameter(name="sync", type="string", cli_flag="--sync", default="",
                                 description="4B 同步字（十六进制）；留空从射频配置或接收信号恢复"),
                CommandParameter(name="rf_config", type="path", cli_flag="--rf-config", default="",
                                 description="包含 hopping_table 与可选同步字的射频 JSON 配置"),
                CommandParameter(name="cr", type="choice", cli_flag="--cr", choices=["1/2", "3/4", "1"],
                                 default="3/4", description="FLRC 信道编码率"),
                CommandParameter(name="sample_rate", type="number", cli_flag="--sample-rate", default=None,
                                 min_value=1, description="采样率，S/s；留空使用同步收发机默认值"),
                CommandParameter(name="master_clock_rate", type="choice", cli_flag="--master-clock-rate",
                                 choices=[125000000, 122880000, 153600000], default=None,
                                 description="USRP N310 主时钟频率，Hz；留空使用默认值"),
                CommandParameter(name="gain", type="number", cli_flag="--gain", default=30.0,
                                 description="USRP 接收增益，dB"),
                CommandParameter(name="tx_gain", type="number", cli_flag="--tx-gain", default=30.0,
                                 description="USRP 发射增益，dB"),
                CommandParameter(name="channel", type="integer", cli_flag="--channel", default=0, min_value=0,
                                 description="USRP 物理通道号"),
                CommandParameter(name="antenna", type="string", cli_flag="--antenna", default="RX2",
                                 description="USRP 接收天线端口"),
                CommandParameter(name="timed_hopping", type="boolean", cli_flag="--timed-hopping", default=False,
                                 description="启用 N310 硬件定时跳频跟踪"),
                CommandParameter(name="sync_timeout", type="number", cli_flag="--sync-timeout", default=5.0,
                                 min_value=0.001, description="等待同步锁定的最长时间，秒；弱信号可增加到 30"),
                CommandParameter(name="timeout", type="number", cli_flag="--timeout", default=1.0,
                                 min_value=0.001, description="发送后等待匹配回复的超时时间，秒"),
                CommandParameter(name="no_wait", type="boolean", cli_flag="--no-wait", default=False,
                                 description="不等待回复（仍需同步锁定并按时隙发送）"),
                CommandParameter(name="json", type="boolean", cli_flag="--json", default=True,
                                 description="在任务日志中输出结构化收发结果"),
            ],
        )
        for command_id in ("transmit", "duplex", "modulate"):
            command = self._commands[command_id]
            for parameter in command.parameters:
                if parameter.name == "message_file" or (parameter.name == "frequency" and command_id != "transmit"):
                    parameter.required = False
                if parameter.name in ("bit_rate", "cr", "bt") or (parameter.name == "frequency" and command_id != "transmit"):
                    parameter.default = None
            additions = [
                CommandParameter(name="config", type="path", cli_flag="--config", description="共用物理 TOML；CLI 覆盖 [transmit]，再覆盖 [demod]"),
                CommandParameter(name="payload_hex", type="string", cli_flag="--payload-hex", description="原始物理载荷；填写时替代 JSON 模板，支持任意业务协议"),
                CommandParameter(name="header_type", type="choice", cli_flag="--header-type", choices=["variable", "fixed"], description="硬件头；留空使用模板或默认 variable"),
                CommandParameter(name="crc_bytes", type="choice", cli_flag="--crc-bytes", choices=[2,3,4], description="硬件 CRC 字节数；留空使用模板或默认 3"),
                CommandParameter(name="crc_initial", type="string", cli_flag="--crc-initial", description="唯一发送 CRC 初值，如 0xFFFFFFFF；多个接收初值不自动推断发送方向"),
                CommandParameter(name="payload_length", type="integer", cli_flag="--payload-length", min_value=6, max_value=127, description="固定头包长；必须与实际载荷一致，不自动填充"),
            ]
            command.parameters.extend(p for p in additions if p.name not in {item.name for item in command.parameters})
            if command_id == "modulate":
                for parameter in command.parameters:
                    if parameter.name == "sync": parameter.required = False
                    if parameter.name == "sample_rate": parameter.default = None
        self._commands["transmit"].parameters.extend([
            CommandParameter(name="sample_rate", type="number", cli_flag="--sample-rate", min_value=1, required=True, default=2000000, description="采样率；留空由模板或码率决定"),
            CommandParameter(name="sync", type="string", cli_flag="--sync", required=True, default="54313253", description="必须明确指定 4B 同步字；H12 对频为 54313253 (T12S)"),
            CommandParameter(name="serial", type="string", cli_flag="--serial", description="HackRF 序列号；可从探测列表选择，留空自动选择"),
            CommandParameter(name="device_index", type="integer", cli_flag="--device-index", min_value=0, description="HackRF 枚举索引；与 serial 互斥，优先推荐序列号"),
        ])
        self._commands["duplex"].parameters.extend([
            CommandParameter(name="session", type="boolean", cli_flag="--session", default=False, description="常驻会话：启动后只接收和同步，在任务中心按需投递多条报文"),
            CommandParameter(name="bit_rate", type="choice", cli_flag="--bit-rate", choices=[260000,325000,520000,650000,1040000,1300000], description="空口码率；模板或默认 325000"),
            CommandParameter(name="bt", type="choice", cli_flag="--bt", choices=[0,0.5,1], description="高斯成形 BT；模板或默认 1"),
        ])
        for parameter in self._commands["sync"].parameters:
            if parameter.name == "frequency":
                parameter.type = "integer"
                parameter.default = None
                parameter.description = "初始监听频率，Hz；留空从所选跳频配置选取，未配置时使用 CLI 默认值"
        self._commands["duplex"].description = "USRP 持续接收并锁定 DK32S FHSS/TDD 时隙；支持单次交互或常驻会话按需投递，失锁时拒绝使用旧时序发送。"
        for command_id in ("demod", "live"):
            self._commands[command_id].parameters.extend([
                CommandParameter(name="header_type", type="choice", cli_flag="--header-type", choices=["variable", "fixed", "auto"], description="接收硬件头类型；auto 仅用于接收搜索"),
                CommandParameter(name="crc_bytes", type="choice", cli_flag="--crc-bytes", choices=[2,3,4], description="硬件 CRC 字节数；模板或默认 3"),
                CommandParameter(name="crc_initial", type="string", cli_flag="--crc-initial", description="指定唯一接收 CRC 初值；覆盖模板中的候选初值"),
                CommandParameter(name="payload_length", type="integer", cli_flag="--payload-length", min_value=6, max_value=127, description="固定头候选包长；多个候选请在 TOML payload_lengths 中配置"),
            ])
        for command_id in ("live", "rx", "tx", "sync"):
            command = self._commands[command_id]
            command.parameters.extend([
                CommandParameter(name="serial", type="string", cli_flag="--serial", description="HackRF 序列号；仅在 HackRF 模式生效"),
                CommandParameter(name="device_index", type="integer", cli_flag="--device-index", min_value=0, description="HackRF 设备索引，与 serial 互斥"),
            ])
            if command_id != "live":
                command.parameters.append(CommandParameter(name="channel", type="integer", cli_flag="--channel", min_value=0, default=0, description="USRP 物理通道号；按整台设备互斥"))
        for order, command_id in enumerate(("duplex", "transmit")):
            command = self._commands[command_id]
            command.group_id = "message_io"
            command.group_name = "报文收发"
            command.group_description = "选择 USRP 同步时隙收发或 HackRF 盲发，按所选模式配置硬件与报文。"
            command.mode_order = order

        self._commands["exchange"] = CommandDefinition(
            id="exchange", name="双 HackRF 收发诊断 (Exchange)", category="SDR 采集与发射",
            description="两台 HackRF 提前接收、有限发送并保留尾部采样，输出 IQ、解码帧与诊断报告。",
            subcommand="exchange", requires_sdr=True, parameters=[],
        )
        for command_id, parameters in SUPPLEMENTAL_PARAMETERS.items():
            self._commands[command_id].parameters.extend(p.model_copy(deep=True) for p in parameters)
        for command_id in ("demod", "live"):
            for parameter in self._commands[command_id].parameters:
                if parameter.name == "frequency":
                    parameter.type = "list_number"
                    parameter.default = None
                    parameter.description = "解调目标载频，Hz；多个值用逗号或空格分隔。与采集中心频率不同，目标频率及滤波通带须位于采样带内。"
                elif parameter.name == "payload_length":
                    parameter.type = "list_number"
                    parameter.description = "固定头候选包长，6..127 字节；多个值用逗号或空格分隔。"
            # 采集中心紧邻目标载频，便于同时调整采样频带。
            parameters = self._commands[command_id].parameters
            center = next(p for p in parameters if p.name == "center_frequency")
            center.description = "采集中心频率，Hz；离线须与 IQ 录制中心一致，实时用于硬件调谐。留空使用模板或 CLI 默认值。"
            parameters.remove(center)
            parameters.insert(next(i for i, p in enumerate(parameters) if p.name == "frequency"), center)
        for parameter in self._commands["modulate"].parameters:
            if parameter.name == "payload_hex":
                parameter.required = False


_REGISTRY: CommandRegistry | None = None


def get_command_registry() -> CommandRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = CommandRegistry()
    return _REGISTRY
