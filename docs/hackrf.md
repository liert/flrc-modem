# 独立 HackRF 后端

`sdr/hackrf` 直接通过 `ctypes` 调用 libhackrf，独立于 FLRC 编解码和 SIYI 业务协议。Windows x64 DLL 及依赖随项目和 wheel 保存，默认直接加载项目内文件；也可指定其他 DLL。无需安装第三方 Python HackRF 封装。

## 命令行

```powershell
# 枚举设备和读取库版本，不启动收发
python -m flrc_modem hackrf info

# 发送已生成的 cs8 IQ；频率为 HackRF 中心，数字频移由 IQ 本身携带
python -m flrc_modem hackrf tx --iq outputs/generated.iq --frequency 2480000000 --sample-rate 10000000 --tx-gain 0

# 采集 1 秒 cs8 IQ，已有输出文件会报错
python -m flrc_modem hackrf rx --frequency 2480000000 --sample-rate 10000000 --duration 1 --output outputs/capture.cs8
```

独立入口为 `flrc hackrf`，也支持 `python -m flrc_modem.sdr.hackrf.cli`。`--dll <绝对路径>` 选择自定义库。导入后端模块本身不会打开设备或启动射频流。

## 多设备选择

`python -m flrc_modem hackrf info` 枚举设备，输出 `devices` 中的 `index` 和 `serial`；该命令不打开设备，也不判断是否空闲。

- 不指定设备：按枚举顺序逐台打开，跳过打开失败的设备，选择第一个成功项。
- `--serial 2a5faf67`：指定完整序列号或唯一后缀，不区分大小写；后缀匹配多台时明确报错。
- `--device-index 1`：指定从 0 开始的设备索引；序列号读不到时也可按索引选择。
- 两个选择参数互斥；显式指定的目标不可用时直接报错，不改用其他设备。

这些参数统一适用于 `transmit`、`live` 和 `hackrf tx/rx`。开始收发前打印最终选中的设备，实时结束统计的 `device` 字段也记录索引和序列号。索引取决于当前 USB 枚举顺序，插拔后可能改变；固定设备优先用序列号。

枚举中的 `serial=null` 只表示无法读出序列号，不直接判定为占用。自动选择以实际打开结果为准；全部失败时列出逐台错误，便于区分占用、驱动及 USB 访问问题。

Python 接口对应 `HackRFDevice(serial="2a5faf67")` 或 `HackRFDevice(device_index=1)`，成功后可读取 `device.selected_device`。使用 `LiveTransmitter` / `LiveReceiver` 时同样可传 `serial` 或 `device_index`。

| 参数 | 含义 |
| --- | --- |
| `--sample-rate` | HackRF IQ 采样率，2..20MSps；应与文件采样率一致 |
| `--baseband-bandwidth-hz` | HackRF 模拟基带带宽，默认取不超过 0.75×采样率的最大档位；低于最小档时取 1.75MHz |
| `--tx-gain` | TX IF 增益 0..47dB，默认 0 |
| `--lna-gain` / `--vga-gain` | RX 增益，分别为 8dB / 2dB 步长，默认 16 |
| `--amp` / `--bias-tee` | 射频放大器 / 天线口供电，默认关闭 |
| `--queue-depth` | 固定缓冲池数量，默认 8 |
| `--block-bytes` | 槽位字节数，512 的倍数；默认取 DLL 的 USB 缓冲大小 |
| `--prefill-blocks` | TX 启动前预填充块数，默认与 queue-depth 相同 |
| `--repeat` | TX 文件重复次数，默认 1；不修改内部序号或时间戳 |
| `--iq-format` | TX 输入 cs8 / cu8 / cf32，默认 cs8；RX 输出 cs8 |
| `--timeout` | 流式超时，默认文件/采集时长加 10 秒 |
| `--underrun-policy` | TX 主机队列欠载时 stop 或 zeros，默认 stop |
| `--allow-device-shortfalls` | 显式诊断模式：固件报告欠载/溢出时仍返回统计 |

HackRF 模拟基带带宽与 SX1280 的 FLRC 标称 BW 分别设置。例如 FLRC 的 300kHz BW 不能直接传给 HackRF 的模拟滤波接口。

cs8 文件走 `FileIO.readinto`，直接读入固定槽位。cu8 / cf32 在生产线程转换。正常结束时最后一个 USB 块补零到 512 字节边界，最多增加 510 字节；另有 libhackrf 自身的 flush 传输。

## Python 接口

```python
from pathlib import Path
from flrc_modem.sdr.hackrf import HackRFConfig, HackRFDevice, IQFileSource

config = HackRFConfig(frequency_hz=2_480_000_000, sample_rate=10_000_000, tx_gain=0)
with IQFileSource(Path("outputs/generated.iq"), repeat=3) as source:
    with HackRFDevice(serial="2a5faf67") as device:
        stats = device.transmit(source, config, queue_depth=16, prefill_blocks=16,
                                block_bytes=262144, timeout=10)
        print(stats.to_dict())
```

自定义 TX 源实现 `readinto(memoryview) -> int`，0 表示结束。源的工作线程应限制阻塞时间；数据为交错 signed-int8 I/Q。已有复数样本生成器可使用 `IQIterableSource(iter_signal(packet.bits, modulation_config))`，成形与量化在生产线程完成。

直接输入载荷或动态报文可使用 `python -m flrc_modem transmit` / `flrc transmit`，由 `transmission.PacketSource` 接入同一后端，不保存 IQ。命令、报文队列和缓冲延迟见 [实时发送](transmission.md)。

RX 使用 `device.receive(sink, config, sample_count=...)`；`sample_count=None` 持续接收至取消，需同时设置合适的 timeout（或 None）。自定义消费者实现 `write(buffer) -> int`。消费者在独立工作线程收到指向池槽位的只读用途视图，应在 `write` 返回前处理完；需要长期保留时自行复制。接口不取得传入 source/sink 的所有权。实时 FLRC 接收入口见 [实时解调](realtime.md)。

两种操作都接受 `cancel_event=threading.Event()`；取消会停止设备并抛出 `StreamCancelled`。`device.last_stats` 在失败或取消后保留部分字节数和可用的固件状态。同一设备句柄的流式操作互斥，支持停止后反复切换 RX/TX。

高级接口 `device.api.bind(name, restype, argtypes)` 可以声明并调用额外导出函数，`device.handle` 为当前设备指针。调用者应保持 ABI 正确，并在主线程执行设备控制函数。

## 回调与性能边界

- 固定缓冲池 + 有界队列；默认约 2MiB，每个槽位 262144B。设置的总池大小不能超过 256MiB。
- USB 回调只做长度检查、队列交接和 bulk `memmove`；文件 I/O、NumPy 转换和 DSP 都在工作线程。
- TX 先提交最后一个非空块，下一次回调再返回结束；主线程等 flush 成功后停止，核对 USB 提交/完成字节数。
- stop 成功后注销 TX completion/flush 指针，再允许重用设备；停止失败时保留回调引用至关闭设备。
- RX 缓冲耗尽默认报错；TX 生产队列欠载默认报错。显式 zeros 策略会插入静默，并记录 `zero_fill_bytes`。
- 固件支持时读取 M0 统计，`num_shortfalls>0` 默认报错，避免 USB 字节数正确却误报数据连续。旧固件无法读取时记录 `device_state_error`。

`completed_bytes` 表示 TX USB 完成字节，`flush_complete` 表示库报告发送排空；两者都不证明接收端正确收到 FLRC 数据。回调仍有 Python/GIL 与队列开销，实际吞吐受 USB、驱动、设备及生产/消费速度限制。

## 本机真实设备结果

2026-10-16，Windows x64、Python 3.14.6；libhackrf 0.9.2 / 2026.01.3，设备固件 2024.02.1，USB API 0x0108。频率 2480MHz，TX 增益 0dB，RF 放大器关闭。吞吐验证发送零 IQ，另完成了一次 TEST 同步字的 FLRC 短帧发送。

| 采样率 | RX 固件溢出 | TX 固件欠载 | 备注 |
| --- | --- | --- | --- |
| 2MSps | 0 | 0 | 各 1 秒 |
| 10MSps | 0 | 0 | 各 1 秒；TX 20,000,002B，总耗时约 1.0072s |
| 12MSps | 0 | 2 | 各 0.5 秒 |
| 14MSps | 0 | 17 | 各 0.5 秒 |
| 16MSps | 15 | 359 | 各 0.5 秒 |
| 18MSps | 76 | 970 | 各 0.5 秒 |
| 20MSps | 50 | 610 | 各 0.25 秒 |

原生 `hackrf_transfer` 对照：20MSps RX 38 次溢出、TX 606 次欠载；12/14MSps TX 也出现 1/29 次欠载。扩大 Python 队列到 32 并未消除 20MSps RX 问题。结果说明当前设备链路的高速丢样并非仅发生在 Python 后端；具体瓶颈仍需 USB/驱动/设备侧进一步定位。当前 325kb/s 单信道实时解调使用 2MSps；需要宽带文件收发时，10MSps 已通过上述短测。以上结果不构成长时间无丢样保证。

还验证了 2、510、512、514、262142、262144、262146、1048578B 的真实 TX 尾块、同句柄 RX→TX→RX 重用，以及取消后 `active_mode=0`。单台 HackRF 的这些测试没有完成独立接收端空口逐字节核对。

结果摘要保存于 [硬件测试数据](data/hackrf-hardware-validation.json)。复测脚本默认只做 RX，指定 `--tx` 才运行真实 TX：

```powershell
python scripts/check_hackrf_hardware.py --tx --rates 2000000 10000000 --duration 1
```

默认速率组包含 20MSps；固件丢样会使报告 `success=false` 并返回非零，供后续链路排查。原始 IQ 与完整报告位于 `outputs/hackrf-hardware-*`，不纳入 Git。

接口语义依据 [libhackrf 官方头文件](https://github.com/greatscottgadgets/hackrf/blob/main/host/libhackrf/src/hackrf.h)，随项目 DLL 的对应头文件和许可证位于 `sdr/hackrf/native/windows-x64`。
