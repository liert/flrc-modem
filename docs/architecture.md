# 模块边界与扩展接口

```text
IQ 文件 → io.iq → dsp.frontend → dsp.detector → phy.packet
                                            ├─ phy.sync
                                            ├─ phy.fec
                                            └─ phy.crc
                  pipeline 去重 → DecodedFrame → io.output → JSONL / 控制台
                                                   └─ 可选 parsing 插件
```

- `phy.packet` 只检查 FLRC 16bit 变长头、6..127B 硬件载荷及 CRC24。载荷的全部字节保持原样。
- `dsp` 不解释频率的厂商信道编号。CLI 目标用 Hz 表示；特定设备的信道换算可由上层工具或参数预设生成。
- `pipeline` 按物理包时间、频率及内容合并重叠窗口重复命中；间隔 200ms 的业务重传仍分别输出。
- `parsing` 在物理帧成功后调用；异常只追加 `application_error`，不删除该帧。
- 所有输入/输出都独立于业务规则。后续规则文件加载器实现 `FrameParser` 接口即可复用相同输出流程。

调制链路独立于解析器：

```text
原始载荷 → phy.encoder（头、CRC、FEC、前导）
        → modulation.waveform（高斯成形、相位积分、数字频移）
        → io.iq（量化、流式文件写入）→ IQ + 参数 JSON
```

`modulate_cli` 仅编排原始载荷输入、参数记录和可选回读；`--verify` 复用原接收链路逐帧检查。实时发送入口另支持独立业务编码模块。离线调制使用方法见 [调制说明](modulation.md)。

`sdr/hackrf` 作为独立设备适配层接收 cs8 数据源或复数样本迭代器：生产线程转换/读文件 → 固定池 → USB 回调复制 → libhackrf；RX 按相反方向复制到池，再由工作线程写入消费者。DLL、接口和硬件测试见 [HackRF 后端说明](hackrf.md)。

`realtime` 将连续 RX 交给有界缓冲池，按原有分块规则生成重叠窗口，默认送入独立 DSP 进程。离线和实时共用 `pipeline.WindowDecoder`，维持相同的前导搜索、CRC 检查及去重状态。插件和输出由调用线程执行；队列积压、固件丢样或输出失败均停止采集并清理设备和进程。见 [实时解调](realtime.md)。

`transmission` 接收原始载荷或业务模板，`PacketSource` 使用有界请求队列和已有编码/调制模块，转换为连续的 cs8 `readinto` 数据源。没有报文时生成静默 IQ，输入结束后排空队列；`LiveTransmitter` 负责设备、取消、时限与健康检查。`transmit_cli` 编排载荷/模板输入及 JSONL，整个链路不依赖 IQ 文件。见 [实时发送](transmission.md)。

`encoding` 与 `parsing` 分别提供发送和解析插件契约。发送链路：`MessageTemplate → encoding.rules → FrameEncoder(message, EncodeContext) → 完整物理载荷 → phy.encoder → modulation → HackRF`。字段变化在 IQ 生产线程逐突发执行，使用全局包号及前导起点的样本时钟，避免队列提前构造时产生过期时间戳。内置 DK32S 和 DataLink；接口见 [协议发送插件](protocol-transmission.md)。

WebUI 的设备列表通过独立短进程分别探测 USRP 和 HackRF，API 进程不加载用于枚举的 UHD/libhackrf。探测超时（10 秒）或原生异常仅将该设备标为探测异常；正常结果缓存 5 秒，异常结果缓存 60 秒，已被任务占用的设备跳过探测。探测子进程启用 `faulthandler`，原生崩溃堆栈输出到父进程捕获的 stderr，异常时连同设备类型、PID、退出码和耗时统一写入 `logs/webui.log`，不再按每次探测创建故障文件；单次诊断文本最多保留末尾 64Ki 字符，并标记是否截断。服务启动会清理超过 60 秒、且内容为空或仅含启动头的旧 `device-probe-<kind>-<pid>.fault.log`；有实际堆栈的历史文件保留。发送等 CLI 任务仍使用独立子进程，并开启 `PYTHONFAULTHANDLER`，堆栈进入任务终端日志；Windows 原生异常退出会显示十六进制退出码。服务生命周期及子进程启动/退出记录见 `logs/webui.log`。

## JSONL 字段

一行一个已校验物理帧，`schema_version=1`。主要字段：

| 字段 | 含义 |
| --- | --- |
| `time_s` | 文件或本次接收首采样之后的突发前导位置，秒；补偿 FIR 延迟 |
| `frequency_hz` | 用户指定的目标载频，不是自动验证的发射机绝对载频 |
| `cfo_hz` | 相对目标载频的估计残余频偏 |
| `sample_power_db` | 归一化 I/Q 样本幅度的相对功率，非 dBm；cf32 保留文件本身幅度标度 |
| `sync_word_hex` | CRC 验证通过的 4B 同步字，字节顺序保持恢复结果 |
| `hardware_header_hex` | 2B FLRC 变长硬件头 |
| `payload_length / payload_hex` | 全部硬件载荷，不剥离任何应用字段 |
| `crc_hex / crc_ok` | 3B CRC 及校验状态；只输出 `crc_ok=true` 的帧 |
| `fec_path_metric` | 硬判决 Viterbi 路径距离；不等同于信道真实 BER |
| `application` | 可选插件追加的字段；默认没有 |
| `application_error` | 插件执行或序列化失败信息，物理数据仍完整 |

## 业务插件

内置协议集中注册在 `parsing/protocols/__init__.py`，`dk32s.py` 和 `datalink.py` 各自处理封装及业务字段，`common.py` 只共享紧凑帧/校验工具。CLI 使用 `--protocol dk32s` 或 `--protocol datalink`，与外部 `--parser` 互斥；两者最终都通过相同的 `FrameParser` 接口调用。

协议的长度与 sum8 检查属于 `application` 层，不改变物理 CRC 的结果。未知命令保留公共帧头、命令号及原始业务字节；不能确认的字段不生成猜测值。

插件为可导入的 Python 函数，接收不可变 `DecodedFrame`，返回字典或 `None`：

```python
from flrc_modem.models import DecodedFrame

def parse(frame: DecodedFrame):
    payload = frame.packet.payload
    return {"length": len(payload)}
```

使用 `--parser your_package.your_module:parse`。示例插件：`flrc_modem.parsing.example:describe_payload`。Python 插件会在本机执行，只加载自己信任的模块。

规则文件引擎尚未实现；计划作为独立解析适配器处理偏移、字节序、位域和应用校验，保持核心物理层无厂商业务依赖。
