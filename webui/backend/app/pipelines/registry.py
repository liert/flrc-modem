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


        # 6. 2.4GHz 全频段自动盲扫与信道感知流水线 (scan)
        self._pipelines["pipe_scan"] = PipelineDefinition(
            id="pipe_scan",
            name="2.4GHz 全频段自动盲扫与信道感知流水线",
            description="流式调度 SDR 硬件执行 2.4GHz 全频段快速步进扫频或离线分析，自适应估计噪声基底与能量峰，并发执行 FLRC 前导/同步字盲恢复与信道质量评估",
            command_id="scan",
            nodes=[
                PipelineNode(
                    id="node_scan_source",
                    type="source",
                    label="扫频采样源",
                    description="SDR 步进频点驻留采集 (HackRF / USRP) 或离线宽带原始 IQ 文件",
                    stage_id="starting",
                    outputs=[PipelinePort(id="p_scan_iq", label="步进采样", data_type="complex_stream")],
                    position={"x": 50, "y": 150},
                ),
                PipelineNode(
                    id="node_scan_psd",
                    type="processor",
                    label="宽带频谱能量检测",
                    description="分段快速傅里叶变换 (FFT)、功率谱密度 (PSD) 积分与自适应 CFAR 底噪门限判决",
                    stage_id="detecting",
                    inputs=[PipelinePort(id="p_in_iq", label="步进采样", data_type="complex_stream")],
                    outputs=[PipelinePort(id="p_peaks", label="能量峰候选", data_type="spectral_peaks")],
                    position={"x": 300, "y": 150},
                ),
                PipelineNode(
                    id="node_scan_flrc_verify",
                    type="processor",
                    label="FLRC 物理特征验证",
                    description="提取中心频偏 (CFO)、粗判决解调、搜索 32bit AGC/定时前导及物理同步字",
                    stage_id="analyzing",
                    inputs=[PipelinePort(id="p_in_peaks", label="能量峰候选", data_type="spectral_peaks")],
                    outputs=[PipelinePort(id="p_verified_channels", label="已验证信道", data_type="flrc_channels")],
                    position={"x": 570, "y": 150},
                ),
                PipelineNode(
                    id="node_scan_classifier",
                    type="processor",
                    label="信道质量与协议归类",
                    description="计算 SNR 信噪比、带宽占用度、突发占空比，匹配候选物理层预设配置",
                    stage_id="hop_completed",
                    inputs=[PipelinePort(id="p_in_channels", label="已验证信道", data_type="flrc_channels")],
                    outputs=[PipelinePort(id="p_classified", label="结构化信道", data_type="channel_report")],
                    position={"x": 840, "y": 150},
                ),
                PipelineNode(
                    id="node_scan_sink",
                    type="sink",
                    label="信道感知报告与联动",
                    description="生成全频段信道建议清单与 JSON 结构化报告，支持一键带入物理帧与解调工作台",
                    stage_id="completed",
                    inputs=[PipelinePort(id="p_in_report", label="结构化信道", data_type="channel_report")],
                    position={"x": 1110, "y": 150},
                ),
            ],
            edges=[
                PipelineEdge(id="e1", source="node_scan_source", target="node_scan_psd", source_handle="p_scan_iq", target_handle="p_in_iq", data_type="complex_stream", label="步进样点"),
                PipelineEdge(id="e2", source="node_scan_psd", target="node_scan_flrc_verify", source_handle="p_peaks", target_handle="p_in_peaks", data_type="spectral_peaks", label="活跃频点"),
                PipelineEdge(id="e3", source="node_scan_flrc_verify", target="node_scan_classifier", source_handle="p_verified_channels", target_handle="p_in_channels", data_type="flrc_channels", label="FLRC 候选"),
                PipelineEdge(id="e4", source="node_scan_classifier", target="node_scan_sink", source_handle="p_classified", target_handle="p_in_report", data_type="channel_report", label="信道拓扑"),
            ],
        )

        # 7. 时隙同步与交互双工收发流水线 (duplex)
        self._pipelines["pipe_duplex"] = PipelineDefinition(
            id="pipe_duplex",
            name="时隙同步与交互双工收发流水线",
            description="基于 USRP N310 纳秒级硬件时间戳与 TDD/FHSS 时钟锁定，在指定时隙对齐发射业务报文并实时匹配对端回复与往返时延",
            command_id="duplex",
            nodes=[
                PipelineNode(
                    id="node_duplex_usrp_clock",
                    type="source",
                    label="USRP 硬件时钟与持续接收",
                    description="UHD 纳秒级硬件时钟源，持续流式接收并维持对端时钟漂移对齐",
                    stage_id="live_sync",
                    outputs=[PipelinePort(id="p_clock_sync", label="时钟基准", data_type="sync_clock")],
                    position={"x": 50, "y": 150},
                ),
                PipelineNode(
                    id="node_duplex_rx_listen",
                    type="processor",
                    label="在线解调与回复捕获",
                    description="低延迟多采样相位解调、解交织与硬件 CRC24 校验，持续捕获空中突发",
                    stage_id="live_sync",
                    inputs=[PipelinePort(id="p_in_clock", label="时钟基准", data_type="sync_clock")],
                    outputs=[PipelinePort(id="p_rx_frames", label="空中接收帧", data_type="flrc_frames")],
                    position={"x": 300, "y": 150},
                ),
                PipelineNode(
                    id="node_duplex_slot_scheduler",
                    type="processor",
                    label="精准时隙发射调度",
                    description="依据 TDD/FHSS 时隙模型与当前保护间隔，在目标时隙时刻精准对齐触发发射",
                    stage_id="live_sync",
                    inputs=[PipelinePort(id="p_in_clock2", label="时钟基准", data_type="sync_clock")],
                    outputs=[PipelinePort(id="p_tx_burst", label="对齐发射突发", data_type="tx_burst")],
                    position={"x": 570, "y": 150},
                ),
                PipelineNode(
                    id="node_duplex_rtt_matcher",
                    type="processor",
                    label="报文匹配与 RTT 评估",
                    description="DK32S / DataLink 业务请求编号关联、对端回复校验与物理空口往返时延精确计算",
                    stage_id="live_sync",
                    inputs=[
                        PipelinePort(id="p_in_rx", label="空中接收帧", data_type="flrc_frames"),
                        PipelinePort(id="p_in_tx", label="对齐发射突发", data_type="tx_burst"),
                    ],
                    outputs=[PipelinePort(id="p_rtt_records", label="交互记录", data_type="duplex_records")],
                    position={"x": 840, "y": 150},
                ),
                PipelineNode(
                    id="node_duplex_session_sink",
                    type="sink",
                    label="双工会话流与统计落盘",
                    description="持久化会话交互历史、实时呈现响应时间与丢包率、支持单步/周期报文交互",
                    stage_id="live_sync",
                    inputs=[PipelinePort(id="p_in_records", label="交互记录", data_type="duplex_records")],
                    position={"x": 1110, "y": 150},
                ),
            ],
            edges=[
                PipelineEdge(id="e1", source="node_duplex_usrp_clock", target="node_duplex_rx_listen", source_handle="p_clock_sync", target_handle="p_in_clock", data_type="sync_clock", label="持续监听"),
                PipelineEdge(id="e2", source="node_duplex_usrp_clock", target="node_duplex_slot_scheduler", source_handle="p_clock_sync", target_handle="p_in_clock2", data_type="sync_clock", label="时隙锁定"),
                PipelineEdge(id="e3", source="node_duplex_rx_listen", target="node_duplex_rtt_matcher", source_handle="p_rx_frames", target_handle="p_in_rx", data_type="flrc_frames", label="收到回复"),
                PipelineEdge(id="e4", source="node_duplex_slot_scheduler", target="node_duplex_rtt_matcher", source_handle="p_tx_burst", target_handle="p_in_tx", data_type="tx_burst", label="发射时刻"),
                PipelineEdge(id="e5", source="node_duplex_rtt_matcher", target="node_duplex_session_sink", source_handle="p_rtt_records", target_handle="p_in_records", data_type="duplex_records", label="交互统计"),
            ],
        )

        # 8. 实时流式解调与空口捕获流水线 (live)
        self._pipelines["pipe_live"] = PipelineDefinition(
            id="pipe_live",
            name="实时流式解调与空口捕获流水线",
            description="流式摄取 SDR 硬件基带采样，通过有界环形缓冲池并发多进程重叠解调，实现零丢帧实时捕获与双通道落盘/广播",
            command_id="live",
            nodes=[
                PipelineNode(
                    id="node_live_sdr_source",
                    type="source",
                    label="SDR 实时硬件采集",
                    description="HackRF / USRP 连续高速基带流式输入，底层生产线程有界零拷贝写入环形缓冲池",
                    stage_id="device_init",
                    outputs=[PipelinePort(id="p_live_raw", label="基带采样流", data_type="complex_stream")],
                    position={"x": 50, "y": 150},
                ),
                PipelineNode(
                    id="node_live_ring_buffer",
                    type="processor",
                    label="有界环形缓冲与重叠分块",
                    description="滑动窗口重叠切片、丢样/溢出监测与独立工作子进程流控派发",
                    stage_id="realtime_stream",
                    inputs=[PipelinePort(id="p_in_raw", label="基带采样流", data_type="complex_stream")],
                    outputs=[PipelinePort(id="p_overlapped_blocks", label="重叠分块", data_type="complex_blocks")],
                    position={"x": 320, "y": 150},
                ),
                PipelineNode(
                    id="node_live_demod_cluster",
                    type="processor",
                    label="多进程并发解调引擎",
                    description="数字下变频、粗频偏补偿、快速相关前导、FEC 解卷积与硬件 CRC24 校验",
                    stage_id="realtime_stream",
                    inputs=[PipelinePort(id="p_in_blocks", label="重叠分块", data_type="complex_blocks")],
                    outputs=[PipelinePort(id="p_valid_frames", label="合法物理帧", data_type="flrc_frames")],
                    position={"x": 600, "y": 150},
                ),
                PipelineNode(
                    id="node_live_dedup_router",
                    type="processor",
                    label="跨窗口去重与协议解析",
                    description="基于突发到达时间与硬件载荷合并重复帧，挂载可选 DK32S / DataLink 协议插件解析",
                    stage_id="realtime_stream",
                    inputs=[PipelinePort(id="p_in_frames", label="合法物理帧", data_type="flrc_frames")],
                    outputs=[PipelinePort(id="p_structured_packets", label="结构化报文", data_type="app_record")],
                    position={"x": 880, "y": 150},
                ),
                PipelineNode(
                    id="node_live_dual_sink",
                    type="sink",
                    label="双通道落盘与 WebSocket 广播",
                    description="实时广播推送至 WebUI 瀑布图与协议剖析抽屉，同时落盘持久化至 JSONL 结果文件",
                    stage_id="realtime_stream",
                    inputs=[PipelinePort(id="p_in_packets", label="结构化报文", data_type="app_record")],
                    position={"x": 1160, "y": 150},
                ),
            ],
            edges=[
                PipelineEdge(id="e1", source="node_live_sdr_source", target="node_live_ring_buffer", source_handle="p_live_raw", target_handle="p_in_raw", data_type="complex_stream", label="流式基带"),
                PipelineEdge(id="e2", source="node_live_ring_buffer", target="node_live_demod_cluster", source_handle="p_overlapped_blocks", target_handle="p_in_blocks", data_type="complex_blocks", label="滑动分块"),
                PipelineEdge(id="e3", source="node_live_demod_cluster", target="node_live_dedup_router", source_handle="p_valid_frames", target_handle="p_in_frames", data_type="flrc_frames", label="已校验帧"),
                PipelineEdge(id="e4", source="node_live_dedup_router", target="node_live_dual_sink", source_handle="p_structured_packets", target_handle="p_in_packets", data_type="app_record", label="协议载荷"),
            ],
        )

        # 9. 实时空口盲发流水线 (transmit)
        self._pipelines["pipe_transmit"] = PipelineDefinition(
            id="pipe_transmit",
            name="HackRF 实时空口盲发流水线",
            description="基于 JSON 业务报文模板或原始物理载荷，经协议编码、高斯脉冲成形与爬升包络连续流式送入 HackRF 发射",
            command_id="transmit",
            nodes=[
                PipelineNode(
                    id="node_tx_source_template",
                    type="source",
                    label="报文模板与载荷源",
                    description="加载业务 JSON 模板或原始 Hex 载荷，动态递增序列号与时间戳",
                    stage_id="message_encode",
                    outputs=[PipelinePort(id="p_raw_message", label="业务报文", data_type="json_or_hex")],
                    position={"x": 50, "y": 150},
                ),
                PipelineNode(
                    id="node_tx_phy_assembler",
                    type="processor",
                    label="协议编码与成包",
                    description="附加变长硬件头、计算 CRC24、FEC 卷积编码、位交织与 32bit 同步字拼接",
                    stage_id="packet_assemble",
                    inputs=[PipelinePort(id="p_in_message", label="业务报文", data_type="json_or_hex")],
                    outputs=[PipelinePort(id="p_phy_bits", label="物理比特", data_type="bit_stream")],
                    position={"x": 320, "y": 150},
                ),
                PipelineNode(
                    id="node_tx_burst_modulator",
                    type="processor",
                    label="实时波形成形与包络",
                    description="高斯低通滤波、连续相位积分与 2..20µs 功率平滑爬升包络注入",
                    stage_id="waveform_modulate",
                    inputs=[PipelinePort(id="p_in_bits", label="物理比特", data_type="bit_stream")],
                    outputs=[PipelinePort(id="p_modulated_iq", label="调制 IQ 块", data_type="complex_stream")],
                    position={"x": 600, "y": 150},
                ),
                PipelineNode(
                    id="node_tx_hackrf_sink",
                    type="sink",
                    label="HackRF 硬件 DAC 流式发射",
                    description="有界无锁缓冲池流控、USB 连续传输并驱动 HackRF 射频发射前端",
                    stage_id="sdr_transmit",
                    inputs=[PipelinePort(id="p_in_iq", label="调制 IQ 块", data_type="complex_stream")],
                    position={"x": 880, "y": 150},
                ),
            ],
            edges=[
                PipelineEdge(id="e1", source="node_tx_source_template", target="node_tx_phy_assembler", source_handle="p_raw_message", target_handle="p_in_message", data_type="json_or_hex", label="业务载荷"),
                PipelineEdge(id="e2", source="node_tx_phy_assembler", target="node_tx_burst_modulator", source_handle="p_phy_bits", target_handle="p_in_bits", data_type="bit_stream", label="已编码帧"),
                PipelineEdge(id="e3", source="node_tx_burst_modulator", target="node_tx_hackrf_sink", source_handle="p_modulated_iq", target_handle="p_in_iq", data_type="complex_stream", label="射频 DAC"),
            ],
        )

_PIPELINES: PipelineRegistry | None = None


def get_pipeline_registry() -> PipelineRegistry:
    global _PIPELINES
    if _PIPELINES is None:
        _PIPELINES = PipelineRegistry()
    return _PIPELINES
