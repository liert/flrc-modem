# 不落盘实时发送

`transmit` 将 6..127B 原始物理载荷编码、调制，通过内存缓冲直接送给 HackRF，不生成 IQ 或参数文件。支持静态重复、标准输入 JSONL 动态报文、Python 有界队列。

## 命令行

```powershell
cd flrc-modem

# 直接发送三次；示例使用 TEST 同步字和自定义载荷
.\.venv\Scripts\python.exe -m flrc_modem transmit --frequency 2480000000 --sync 54455354 --payload-hex 000102030405 --repeat 3

# 持续重复，2 秒后立即停止；省略 duration 则持续至 Ctrl+C
python -m flrc_modem transmit --frequency 2480000000 --sync 54455354 --payload-hex 000102030405 --repeat 0 --duration 2

# 从二进制文件读取载荷，仍然不生成 IQ
flrc transmit --frequency 2480000000 --sync 54455354 --payload-file payload.bin
```

原始载荷输入不包含 FLRC 硬件头、FEC 或物理 CRC24；它们自动生成。业务协议头、命令、校验等应已包含在原始载荷中。也可使用 `--protocol dk32s --message-file 模板.json` 自动构造 DK32S 业务帧，并在每个突发更新字段；见 [协议发送插件](protocol-transmission.md)。目前只内置 DK32S 发送编码器。

首包通过校验后、打开设备前，终端会打印一次实际生效配置：中心频率、数字偏移及实际载频、采样率、bitrate/FLRC BW、CR、BT、RampTime 及实际采样数、IQ 幅度、HackRF 带宽/增益、同步字/CRC 初值、重复/间隔/时限和缓冲设置。参数摘要写入 stderr 并立即刷新；stdout 仍仅输出结束统计 JSON。JSONL 显示首包同步字和重复次数，后续报文可逐条覆盖。

多台 HackRF 时默认自动逐台尝试，选择第一个可打开的设备；打开失败的候选会跳过。也可在原命令后添加 `--serial 2a5faf67`（完整序列号或唯一后缀）或 `--device-index 1`，两者互斥。显式指定失败时不会切换设备。运行 `python -m flrc_modem hackrf info` 查看当前索引及序列号；最终选中项会在收发前显示，并写入结束统计的 `device` 字段。

`--frequency` 是 HackRF 中心频率，`--offset-hz` 默认 +60000，因此示例信号中心为 2480.060MHz。当前 325kb/s 默认 2MSps、CR=3/4、BT=1、300kHz 标称 BW、RampTime=12µs。采样率按码率及偏移自动选择，可显式指定；支持 `--bit-rate`、`--bandwidth-hz`、`--cr`、`--bt 0|0.5|1`、`--ramp-time-us`、`--amplitude`、`--crc-initial`，其中 `--bt 0` 关闭高斯成形。bitrate/BW 沿用 SX1280 的绑定档位。

## 动态报文

使用 `--stdin-jsonl`，每行一个对象：

```json
{"payload_hex":"000102030405"}
{"payload_hex":"101112131415","sync":"4e455854","repeat":2}
```

`payload_hex` 必填；`sync`、`repeat` 可覆盖命令行默认值。同步字必须在命令行或每条报文中提供，每条报文的重复次数必须为正整数。空行跳过，非法字段、坏 JSON 和超长输入均报错停止。

PowerShell 示例：

```powershell
@(
  '{"payload_hex":"000102030405"}'
  '{"payload_hex":"101112131415","repeat":2}'
) | .\.venv\Scripts\python.exe -m flrc_modem transmit --frequency 2480000000 --sync 54455354 --stdin-jsonl
```

也可由其他程序持续向标准输入管道写入 JSONL；每条后需要刷新管道。首条有效报文到达并通过校验后才打开设备，后续暂时没有报文时输出静默 IQ，保持 USB 流连续。输入 EOF 后排空队列、等待 TX flush 再关闭设备。输入错误会立即停止；Ctrl+C 返回 130。

`--duration` 限制的是设备发送会话时间，首条输入之前的等待不计入。到时立即停止，可能截断当前帧或丢弃待发送数据；正常排空请使用 EOF 或 Python 的 `finish()`。动态 stdin 使用后台读取线程，时限或取消不会等待下一行输入；进程退出时不接管外部生产程序的生命周期。

## Python 接口

```python
from flrc_modem.modulation import ModulationConfig
from flrc_modem.sdr.hackrf import HackRFConfig
from flrc_modem.transmission import LiveTransmitter

modulation = ModulationConfig(sample_rate=2_000_000)
hardware = HackRFConfig(2_480_000_000, sample_rate=modulation.sample_rate, tx_gain=0)
sender = LiveTransmitter(modulation, hardware, b"TEST")
sender.submit(bytes.fromhex("000102030405"))  # 可预放首包，先校验再打开设备
with sender:
    # 可在任意业务线程中持续调用 submit，支持逐条同步字和重复次数。
    sender.submit(bytes.fromhex("101112131415"), sync_word=b"NEXT", repeat=2, timeout=1)
    sender.finish()                         # 禁止追加，排空后结束
    sender.wait()
print(sender.stats.to_dict())
```

`stop()` 立即取消；`finish()` 正常排空。正常离开 `with` 会自动 finish/wait，异常离开时立即停止。底层 `PacketSource` 独立实现 `readinto`，也可接入自己的 HackRF 调度。

## 缓冲与时序

| 参数 | 默认值 |
| --- | --- |
| `--input-queue-depth` | 64 条报文请求；满时阻塞生产者，Python submit 支持 timeout |
| `--queue-depth` | 8 个原始 IQ 缓冲 |
| `--prefill-blocks` | 4；覆盖本机 libhackrf 启动时连续请求的四个 USB 块 |
| `--block-bytes` | DLL native 大小，本机 262144B |
| `--gap-ms` | 每个物理突发后至少 10ms 静默，包含最后一个突发之后 |
| `--guard-ms` | 整个流首尾各 10ms 静默 |
| `--health-interval` | 每秒检查固件欠载状态，停止后再次检查 |
| `--tx-gain` | 0dB；amp 和 bias-tee 默认关闭 |

发送链路为：报文队列 → 编码/调制/量化生产线程 → 原始固定池 → USB 回调复制 → libhackrf。同一载荷连续发送会复用调制波形。等待业务报文产生的零 IQ 计入 `idle_samples`，与后端欠载补零 `zero_fill_bytes` 分开统计；后端欠载默认报错停止。

实时入队不等于即时到达天线：2MSps 时每个 native 块约 65.5ms，原始池及 libhackrf 已排队数据可能造成数百毫秒延迟，业务队列积压还会增加等待时间。`gap-ms` 表示最小样本间隔，不能用 stdin 写入的墙钟时间实现精确空口定时。当前没有硬件时间戳发送或精确定时调度。

stdout 输出结束统计。`bursts_generated` 表示已经生成的波形数量，可能包括尚在缓冲池中的数据；`completed_bytes` 是 USB 完成字节数，两者都不证明接收端收到报文。正常完成时检查 `flush_complete=true`；主动取消或限时停止不保证 flush。

## 验证

2026-10-19，2MSps、325kb/s、TEST 同步字、自定义测试载荷、TX 增益 0：真实设备完成三帧有限发送、等待 0.8 秒后追加不同载荷和同步字、持续发送后限时停止。三种模式固件欠载均为 0，停止后 `active_mode=0`；有限和动态模式 TX flush 成功。另通过 CLI 连续重复 2 秒验证。

统计见 [硬件复测记录](data/transmit-hardware.json)。复测命令：

```powershell
python scripts/check_transmit_hardware.py --tx
```

自动化检查使用模拟回调捕获实际发送 IQ，并在内存中解调，确认不同载荷、同步字、重复次数、CR/BT 和最大载荷能够正确恢复。真实设备测量验证 USB/固件与生命周期，尚未用独立 SX1280 接收端验证空口互通。
