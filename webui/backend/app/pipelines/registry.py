"""流水线注册中心：维护处理阶段、节点、连接、端口类型和事件映射。"""
from __future__ import annotations

from ..schemas import PipelineDefinition, PipelineEdge, PipelineNode, PipelinePort


class PipelineRegistry:
    def __init__(self):
        self._pipelines: dict[str, PipelineDefinition] = {}
        self._register_all()

    def get(self, pipeline_id: str) -> PipelineDefinition | None:
        return self._pipelines.get(pipeline_id)

    def list_all(self) -> list[PipelineDefinition]:
        return list(self._pipelines.values())

    def _register_all(self):
        # 1. 离线解调流水线
        self._pipelines["pipe_demod"] = PipelineDefinition(
            id="pipe_demod",
            name="FLRC 离线解调处理流水线",
            description="从本地交错 IQ 文件中提取数字信号，经过滤波、多采样相位判决、前导相关、FEC 解码与 CRC24 校验输出载荷帧",
            command_id="demod",
            nodes=[
                PipelineNode(
                    id="node_iq_source",
                    type="source",
                    label="IQ 文件源",
                    description="读取交错 CS8/CU8/CF32 原始采样文件",
                    stage_id="config",
                    outputs=[PipelinePort(id="p_iq_raw", label="IQ 采样流", data_type="complex_stream")],
                    position={"x": 50, "y": 150},
                ),
                PipelineNode(
                    id="node_frontend",
                    type="processor",
                    label="数字前端滤波",
                    description="FIR 低通抗混叠滤波、下变频抽取与粗 CFO 频偏预估",
                    stage_id="demod_pipeline",
                    inputs=[PipelinePort(id="p_in_iq", label="原始采样", data_type="complex_stream")],
                    outputs=[PipelinePort(id="p_filtered", label="滤波基带", data_type="complex_baseband")],
                    position={"x": 280, "y": 150},
                ),
                PipelineNode(
                    id="node_sync_detect",
                    type="processor",
                    label="定时与前导同步",
                    description="多采样相位搜索、AGC/定时前导相关与 32bit 同步字匹配",
                    stage_id="demod_pipeline",
                    inputs=[PipelinePort(id="p_in_bb", label="基带信号", data_type="complex_baseband")],
                    outputs=[PipelinePort(id="p_coded_bits", label="已编码比特", data_type="coded_bits")],
                    position={"x": 510, "y": 150},
                ),
                PipelineNode(
                    id="node_fec_crc",
                    type="processor",
                    label="FEC 解码与 CRC24",
                    description="解交织、Viterbi 解卷积编码 (CR 1/2, 3/4, 1) 并执行硬件 CRC24 校验",
                    stage_id="demod_pipeline",
                    inputs=[PipelinePort(id="p_in_bits", label="编码比特", data_type="coded_bits")],
                    outputs=[PipelinePort(id="p_raw_payload", label="物理载荷", data_type="payload_bytes")],
                    position={"x": 740, "y": 150},
                ),
                PipelineNode(
                    id="node_parser",
                    type="processor",
                    label="协议插件解析",
                    description="可选业务层协议解析插件 (DK32S / DataLink 遥控指令等)",
                    stage_id="demod_pipeline",
                    inputs=[PipelinePort(id="p_in_payload", label="物理载荷", data_type="payload_bytes")],
                    outputs=[PipelinePort(id="p_records", label="结构化数据", data_type="app_record")],
                    position={"x": 970, "y": 150},
                ),
                PipelineNode(
                    id="node_sink",
                    type="sink",
                    label="结果输出与落盘",
                    description="将校验成功的物理帧及解析结果持久化输出为 JSONL 文件",
                    stage_id="output",
                    inputs=[PipelinePort(id="p_in_record", label="结构化帧", data_type="app_record")],
                    position={"x": 1200, "y": 150},
                ),
            ],
            edges=[
                PipelineEdge(id="e1", source="node_iq_source", target="node_frontend", source_handle="p_iq_raw", target_handle="p_in_iq", data_type="complex_stream", label="IQ 样本"),
                PipelineEdge(id="e2", source="node_frontend", target="node_sync_detect", source_handle="p_filtered", target_handle="p_in_bb", data_type="complex_baseband", label="基带流"),
                PipelineEdge(id="e3", source="node_sync_detect", target="node_fec_crc", source_handle="p_coded_bits", target_handle="p_in_bits", data_type="coded_bits", label="前导/同步"),
                PipelineEdge(id="e4", source="node_fec_crc", target="node_parser", source_handle="p_raw_payload", target_handle="p_in_payload", data_type="payload_bytes", label="CRC24 通过"),
                PipelineEdge(id="e5", source="node_parser", target="node_sink", source_handle="p_records", target_handle="p_in_record", data_type="app_record", label="JSONL 导出"),
            ],
        )

        # 2. 信号调制流水线
        self._pipelines["pipe_modulate"] = PipelineDefinition(
            id="pipe_modulate",
            name="FLRC 物理载荷调制流水线",
            description="将原始十六进制载荷自动封装变长硬件头、CRC24 校验与 FEC 编码，经高斯滤波成形与爬升包络生成标准 SX1280 FLRC IQ 波形",
            command_id="modulate",
            nodes=[
                PipelineNode(
                    id="node_payload_source",
                    type="source",
                    label="载荷输入",
                    description="接收 6..127 字节原始物理载荷 (Hex 或二进制文件)",
                    stage_id="packet_encode",
                    outputs=[PipelinePort(id="p_payload_out", label="载荷字节", data_type="bytes")],
                    position={"x": 50, "y": 150},
                ),
                PipelineNode(
                    id="node_packet_assembler",
                    type="processor",
                    label="帧结构封装与 FEC",
                    description="附加变长包头、计算 CRC24、卷积编码 (K=7)、交织并拼接 32bit AGC + 21bit 定时前导与同步字",
                    stage_id="packet_encode",
                    inputs=[PipelinePort(id="p_in_payload", label="原始载荷", data_type="bytes")],
                    outputs=[PipelinePort(id="p_out_bits", label="物理比特流", data_type="bit_stream")],
                    position={"x": 280, "y": 150},
                ),
                PipelineNode(
                    id="node_fsk_modulator",
                    type="processor",
                    label="高斯成形与 2-FSK 调制",
                    description="按 BT 参数进行高斯脉冲滤波，积分相位生成连续相位调制 IQ 基带信号",
                    stage_id="waveform_modulate",
                    inputs=[PipelinePort(id="p_in_bits", label="物理比特", data_type="bit_stream")],
                    outputs=[PipelinePort(id="p_out_iq", label="调制基带", data_type="complex_stream")],
                    position={"x": 510, "y": 150},
                ),
                PipelineNode(
                    id="node_envelope_ramp",
                    type="processor",
                    label="线性功率爬升包络",
                    description="执行 SX1280 硬件规范的 2..20µs 线性功率爬升与下降包络成形，注入突发间隙",
                    stage_id="waveform_modulate",
                    inputs=[PipelinePort(id="p_in_iq", label="调制基带", data_type="complex_stream")],
                    outputs=[PipelinePort(id="p_out_ramped", label="成形波形", data_type="complex_stream")],
                    position={"x": 740, "y": 150},
                ),
                PipelineNode(
                    id="node_iq_writer",
                    type="sink",
                    label="IQ 文件与元数据落盘",
                    description="将复数采样按 CS8/CU8/CF32 格式写入目标文件，并生成配套 JSON 元数据描述",
                    stage_id="waveform_modulate",
                    inputs=[PipelinePort(id="p_in_ramped", label="成形波形", data_type="complex_stream")],
                    position={"x": 970, "y": 150},
                ),
                PipelineNode(
                    id="node_verify_loopback",
                    type="processor",
                    label="回读验证 (可选)",
                    description="调制落盘后立即执行内存回读与完整物理链路解调对比，验证逐位一致性",
                    stage_id="verify_demod",
                    inputs=[PipelinePort(id="p_in_ramped", label="成形波形", data_type="complex_stream")],
                    position={"x": 1200, "y": 150},
                ),
            ],
            edges=[
                PipelineEdge(id="e1", source="node_payload_source", target="node_packet_assembler", source_handle="p_payload_out", target_handle="p_in_payload", data_type="bytes", label="原始数据"),
                PipelineEdge(id="e2", source="node_packet_assembler", target="node_fsk_modulator", source_handle="p_out_bits", target_handle="p_in_bits", data_type="bit_stream", label="编码比特"),
                PipelineEdge(id="e3", source="node_fsk_modulator", target="node_envelope_ramp", source_handle="p_out_iq", target_handle="p_in_iq", data_type="complex_stream", label="基带样点"),
                PipelineEdge(id="e4", source="node_envelope_ramp", target="node_iq_writer", source_handle="p_out_ramped", target_handle="p_in_ramped", data_type="complex_stream", label="完整帧波形"),
                PipelineEdge(id="e5", source="node_iq_writer", target="node_verify_loopback", source_handle="p_in_ramped", target_handle="p_in_ramped", data_type="complex_stream", label="落盘回读"),
            ],
        )

        # 3. 统一 SDR 采集流水线 (rx)
        self._pipelines["pipe_rx"] = PipelineDefinition(
            id="pipe_rx",
            name="统一 SDR 采样采集流水线",
            description="通过 USRP N310 (UHD C API) 或 HackRF 硬件前端，以指定中心频率和采样率连续采集空口信号并落盘",
            command_id="rx",
            nodes=[
                PipelineNode(
                    id="node_sdr_frontend",
                    type="source",
                    label="SDR 硬件前端",
                    description="USRP N310 (SFP0 / UHD) 或 HackRF 射频前端混频与 ADC 采样",
                    stage_id="device_init",
                    outputs=[PipelinePort(id="p_rf_adc", label="ADC 采样", data_type="complex_stream")],
                    position={"x": 80, "y": 150},
                ),
                PipelineNode(
                    id="node_stream_supervisor",
                    type="processor",
                    label="有界流控与溢出统计",
                    description="独立生产/消费线程池、溢出保护 (Overflow Protection) 与自适应时钟对齐",
                    stage_id="stream_capture",
                    inputs=[PipelinePort(id="p_in_adc", label="ADC 采样", data_type="complex_stream")],
                    outputs=[PipelinePort(id="p_out_stream", label="校验数据流", data_type="complex_stream")],
                    position={"x": 400, "y": 150},
                ),
                PipelineNode(
                    id="node_rx_file_sink",
                    type="sink",
                    label="IQ 文件落盘",
                    description="将定长采样写入本地磁盘 (CS8/CU8/CF32)",
                    stage_id="stream_capture",
                    inputs=[PipelinePort(id="p_in_stream", label="校验数据流", data_type="complex_stream")],
                    position={"x": 720, "y": 150},
                ),
            ],
            edges=[
                PipelineEdge(id="e1", source="node_sdr_frontend", target="node_stream_supervisor", source_handle="p_rf_adc", target_handle="p_in_adc", data_type="complex_stream", label="硬件采样"),
                PipelineEdge(id="e2", source="node_stream_supervisor", target="node_rx_file_sink", source_handle="p_out_stream", target_handle="p_in_stream", data_type="complex_stream", label="流控写入"),
            ],
        )

        # 4. 统一 SDR 发射流水线 (tx)
        self._pipelines["pipe_tx"] = PipelineDefinition(
            id="pipe_tx",
            name="统一 SDR 采样发射流水线",
            description="读取本地已生成的 IQ 采样文件，经内存缓冲池送入 USRP 或 HackRF 硬件前端重复发射",
            command_id="tx",
            nodes=[
                PipelineNode(
                    id="node_tx_source_file",
                    type="source",
                    label="本地 IQ 文件",
                    description="读取预先调制的 CS8/CU8/CF32 信号文件",
                    stage_id="file_source",
                    outputs=[PipelinePort(id="p_file_bytes", label="文件内容", data_type="bytes")],
                    position={"x": 80, "y": 150},
                ),
                PipelineNode(
                    id="node_tx_device_init",
                    type="processor",
                    label="硬件连接与射频配置",
                    description="初始化 UHD / HackRF 句柄，配置载波频率、发射增益与前端滤波器",
                    stage_id="device_init",
                    inputs=[PipelinePort(id="p_in_bytes", label="文件内容", data_type="bytes")],
                    outputs=[PipelinePort(id="p_buf_pool", label="预填缓冲池", data_type="complex_stream")],
                    position={"x": 400, "y": 150},
                ),
                PipelineNode(
                    id="node_tx_stream_dac",
                    type="sink",
                    label="DAC 空口发射",
                    description="硬件流式发射、欠载监控 (Underflow) 与发射冲刷 (Flush)",
                    stage_id="stream_tx",
                    inputs=[PipelinePort(id="p_in_buf", label="预填缓冲池", data_type="complex_stream")],
                    position={"x": 720, "y": 150},
                ),
            ],
            edges=[
                PipelineEdge(id="e1", source="node_tx_source_file", target="node_tx_device_init", source_handle="p_file_bytes", target_handle="p_in_bytes", data_type="bytes", label="文件载入"),
                PipelineEdge(id="e2", source="node_tx_device_init", target="node_tx_stream_dac", source_handle="p_buf_pool", target_handle="p_in_buf", data_type="complex_stream", label="DAC 连续输出"),
            ],
        )

        # 5. 实时 FHSS + TDD 同步流水线 (sync)
        self._pipelines["pipe_sync"] = PipelineDefinition(
            id="pipe_sync",
            name="实时 FHSS 跳频与 TDD 同步分析流水线",
            description="流式接入 USRP N310 硬件时间戳，在线估计时钟漂移、跟踪 20 频点跳频时隙与 TDD 收发窗口，生成交互式时间轴视图",
            command_id="sync",
            nodes=[
                PipelineNode(
                    id="node_sync_rf_in",
                    type="source",
                    label="USRP 硬件时间戳采样流",
                    description="零落盘直接流式摄取 2.5MSps 基带，带 UHD 高精度纳秒级时间戳",
                    stage_id="live_sync",
                    outputs=[PipelinePort(id="p_ts_stream", label="时间戳采样", data_type="timed_samples")],
                    position={"x": 60, "y": 150},
                ),
                PipelineNode(
                    id="node_sync_burst_demod",
                    type="processor",
                    label="在线突发解调引擎",
                    description="低延迟多采样相位解调，捕获空中物理帧并提取真实到达硬件时刻",
                    stage_id="live_sync",
                    inputs=[PipelinePort(id="p_in_samples", label="时间戳采样", data_type="timed_samples")],
                    outputs=[PipelinePort(id="p_demod_anchors", label="解调锚点", data_type="sync_anchors")],
                    position={"x": 320, "y": 150},
                ),
                PipelineNode(
                    id="node_sync_fhss_tracker",
                    type="processor",
                    label="FHSS 跳频状态机",
                    description="20频点跳频表锁定、微秒级时钟漂移跟踪与时隙到达预测",
                    stage_id="live_sync",
                    inputs=[PipelinePort(id="p_in_anchors", label="解调锚点", data_type="sync_anchors")],
                    outputs=[PipelinePort(id="p_fhss_state", label="跳频预测", data_type="fhss_state")],
                    position={"x": 580, "y": 150},
                ),
                PipelineNode(
                    id="node_sync_tdd_aligner",
                    type="processor",
                    label="TDD 时分双工对齐",
                    description="接收触发式 / 固定周期模型对齐，计算 ~810µs 转换保护与回传对齐",
                    stage_id="live_sync",
                    inputs=[PipelinePort(id="p_in_fhss", label="跳频预测", data_type="fhss_state")],
                    outputs=[PipelinePort(id="p_tdd_state", label="TDD 相位", data_type="tdd_state")],
                    position={"x": 840, "y": 150},
                ),
                PipelineNode(
                    id="node_sync_timeline_sink",
                    type="sink",
                    label="时间轴与指标可视化",
                    description="生成交互式 HTML 物理时间轴并输出结构化 JSONL 同步快照",
                    stage_id="live_sync",
                    inputs=[PipelinePort(id="p_in_tdd", label="TDD 相位", data_type="tdd_state")],
                    position={"x": 1100, "y": 150},
                ),
            ],
            edges=[
                PipelineEdge(id="e1", source="node_sync_rf_in", target="node_sync_burst_demod", source_handle="p_ts_stream", target_handle="p_in_samples", data_type="timed_samples", label="纳秒时间戳"),
                PipelineEdge(id="e2", source="node_sync_burst_demod", target="node_sync_fhss_tracker", source_handle="p_demod_anchors", target_handle="p_in_anchors", data_type="sync_anchors", label="到达时刻"),
                PipelineEdge(id="e3", source="node_sync_fhss_tracker", target="node_sync_tdd_aligner", source_handle="p_fhss_state", target_handle="p_in_fhss", data_type="fhss_state", label="频点时隙"),
                PipelineEdge(id="e4", source="node_sync_tdd_aligner", target="node_sync_timeline_sink", source_handle="p_tdd_state", target_handle="p_in_tdd", data_type="tdd_state", label="时钟漂移/快照"),
            ],
        )


_PIPELINES: PipelineRegistry | None = None


def get_pipeline_registry() -> PipelineRegistry:
    global _PIPELINES
    if _PIPELINES is None:
        _PIPELINES = PipelineRegistry()
    return _PIPELINES
