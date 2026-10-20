"""命令注册中心：集中管理现有 CLI 命令元数据、参数 Schema 及命令行转换规则。"""
from __future__ import annotations

import sys
from typing import Any

from ..schemas import CommandDefinition, CommandParameter


class CommandRegistry:
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

        param_map = {p.name: p for p in cmd.parameters}

        # 处理特殊子命令参数 (如 usrp info / hackrf info 中的 action)
        if "action" in params:
            argv.append(str(params["action"]))

        for name, value in params.items():
            if name == "action" or value is None or value == "":
                continue
            param_def = param_map.get(name)
            if not param_def:
                continue

            flag = param_def.cli_flag
            if not flag:
                continue

            if param_def.type == "boolean":
                if bool(value):
                    argv.append(flag)
            elif param_def.type == "list_number":
                if isinstance(value, list):
                    for item in value:
                        argv.extend([flag, str(item)])
                else:
                    argv.extend([flag, str(value)])
            else:
                argv.extend([flag, str(value)])

        if enable_ui_events and command_id in ("demod", "modulate", "rx", "tx", "sync"):
            argv.extend(["--ui-events", "ndjson"])

        return argv

    def _register_all(self):
        # 1. 离线解调
        self._commands["demod"] = CommandDefinition(
            id="demod",
            name="离线解调 (Demod)",
            category="解调与接收",
            description="对本地 IQ 录制文件执行离线 FLRC 解调，支持多频点、CRC24 校验及业务插件解析",
            subcommand="demod",
            requires_usrp=False,
            requires_sdr=False,
            pipeline_id="pipe_demod",
            parameters=[
                CommandParameter(
                    name="iq",
                    type="path",
                    description="交错 I/Q 采样文件路径",
                    cli_flag="--iq",
                    required=True,
                    default="outputs/test_ui.iq",
                ),
                CommandParameter(
                    name="sample_rate",
                    type="number",
                    description="采样率，单位 Hz",
                    cli_flag="--sample-rate",
                    default=10000000.0,
                ),
                CommandParameter(
                    name="frequency",
                    type="number",
                    description="解调中心载频，单位 Hz",
                    cli_flag="--frequency",
                    default=2480000000.0,
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
                    description="高斯成形因子 BT (0 关闭，或 0.5/1)",
                    cli_flag="--bt",
                    choices=[0.0, 0.5, 1.0],
                    default=1.0,
                ),
                CommandParameter(
                    name="sync",
                    type="string",
                    description="4B 同步字十六进制 (留空自动盲恢复)",
                    cli_flag="--sync",
                    default="b42b2bb4",
                ),
                CommandParameter(
                    name="iq_format",
                    type="choice",
                    description="IQ 采样数据格式",
                    cli_flag="--iq-format",
                    choices=["cs8", "cu8", "cf32"],
                    default="cs8",
                ),
                CommandParameter(
                    name="protocol",
                    type="choice",
                    description="业务协议解析插件",
                    cli_flag="--protocol",
                    choices=["dk32s", "datalink", "hex"],
                    default="dk32s",
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
                    description="处理最长输入时间 (0 为不限制)",
                    cli_flag="--max-seconds",
                    default=0.0,
                ),
                CommandParameter(
                    name="config",
                    type="path",
                    description="可选物理层 TOML 参数配置文件",
                    cli_flag="--config",
                    category="高级选项",
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
                    description="物理层 TOML 预设配置文件 (如 templates/dk32s_live_2m.toml)",
                    cli_flag="--config",
                    default="templates/dk32s_live_2m.toml",
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
                    choices=["dk32s", "datalink", "hex"],
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
            name="报文发送 (Live Transmit)",
            category="调制与发射",
            description="载荷或 JSON 业务模板即时调制并经由 HackRF 发射，支持遥控通道与控制指令",
            subcommand="transmit",
            requires_usrp=False,
            requires_sdr=True,
            parameters=[
                CommandParameter(
                    name="message_file",
                    type="path",
                    description="协议业务字段 JSON 模板文件",
                    cli_flag="--message-file",
                    default="templates/dk32s_tx_channels.json",
                ),
                CommandParameter(
                    name="frequency",
                    type="number",
                    description="HackRF 发射中心频率，单位 Hz",
                    cli_flag="--frequency",
                    required=True,
                    default=2480000000.0,
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
                    default=20,
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


_REGISTRY: CommandRegistry | None = None


def get_command_registry() -> CommandRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = CommandRegistry()
    return _REGISTRY
