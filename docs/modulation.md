# FLRC 离线调制

输入 **6..127B 完整物理载荷**，输出交错 IQ 文件。输入中保留 DK32S / DataLink 自己的业务头、校验等字节；调制器仅添加物理层封装。

## 命令行

```powershell
python -m flrc_modem modulate --payload-hex 0011223344556677 --sync b42b2bb4 --output outputs/generated.iq --repeat 3 --verify
```

或使用独立入口 `flrc modulate`、`python -m flrc_modem.modulate_cli`。二进制输入使用 `--payload-file payload.bin`，与 `--payload-hex` 二选一。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--sync` | 必填 | 4B 同步字，按字节顺序书写 |
| `--sample-rate` | 10000000 | IQ 采样率，Hz |
| `--bit-rate` | 325000 | SX1280 六档空口码率，bit/s，BW 自动配对 |
| `--bandwidth-hz` | 自动 | 芯片标称双边 BW，Hz；显式填写时必须匹配码率 |
| `--ramp-time-us` | 12 | 幅度爬升/下降时间：2、4、6、8、10、12、16、20µs |
| `--offset-hz` | 0 | 相对 IQ 中心的频率偏移，允许负数 |
| `--cr` | 3/4 | 1/2、3/4 或 1；1 为无 FEC |
| `--bt` | 1 | 0 关闭高斯成形，或选择 0.5、1 |
| `--amplitude` | 0.7 | 归一化峰值幅度，范围 `(0,1]` |
| `--config` | 无 | 共用 TOML；CLI → `[transmit]` → `[demod]` → 默认值 |
| `--header-type` | variable | variable / fixed；生成和发送不支持 auto |
| `--payload-length` | 无 | 固定包长约束，实际长度不符时拒绝，不自动填充 |
| `--crc-bytes` | 3 | 硬件 CRC 字节数：2 / 3 / 4 |
| `--crc-initial` | 所选宽度全 1 | 唯一发送 CRC 初值；多个接收候选须显式选择 |
| `--iq-format` | cs8 | cs8 / cu8 / cf32 |
| `--repeat` | 1 | 相同物理载荷重复次数 |
| `--gap-ms` | 10 | 相邻突发之间的静默间隙，不含突发时长 |
| `--guard-ms` | 10 | 文件首尾各自的静默时长 |
| `--metadata` | `<输出文件>.json` | 参数与载荷记录位置 |
| `--verify` | 关闭 | 回读文件，检查帧数、同步字、头、载荷及 CRC |

重复发送相同字节，不自动递增业务序号或更新时间戳。整数格式会量化，幅度过低可能变成全零；`--verify` 会报告无法解调的结果。静默时长和 BT 可调整，但过短的静默间隙等设置可能不适合当前接收器。

bitrate/BW 绑定档位、RampTime 的线性包络及前导时间定义见 [发射参数说明](tx-parameters.md)。当前默认爬升段位于完整前导之前，下降段位于完整数据区之后，两段计入文件时长。

`cs8` 是 signed-int8 I/Q；`cu8` 是偏移 128 的 unsigned-int8 I/Q；`cf32` 是小端 float32 I/Q。文件为原始样本，无内嵌头。元数据 JSON 记录采样率、频率偏移、格式、幅度、载荷和 CRC；解调 CLI 目前需显式传入参数，不会自动加载该 JSON。

## 回读生成文件

默认参数生成的 cs8 文件可直接使用现有预设：

```powershell
python -m flrc_modem --iq outputs/generated.iq --config templates/physical/dk32s_325k.toml --output outputs/generated-frames.jsonl
```

`--offset-hz 60000` 表示数字频移 +60kHz；解调时 `--frequency` 与 `--center-frequency` 的差值也应为 60000。IQ 文件本身没有绝对射频频率。更改采样率、码率或格式后，应同步更改解调参数。

## Python 接口

```python
from pathlib import Path
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.modulation import ModulationConfig, iter_signal
from flrc_modem.io.iq import write_iq

packet = encode_packet(bytes.fromhex("0011223344556677"), bytes.fromhex("b42b2bb4"))
config = ModulationConfig(sample_rate=10_000_000, bit_rate=325_000,
                          bandwidth_hz=300_000, ramp_time_us=12,
                          offset_hz=60_000, repeat=3)
write_iq(Path("generated.iq"), iter_signal(packet.bits, config), config.iq_format)
```

`encode_packet(..., coding_rate="1/2")` 可指定编码率；`ModulationConfig(coding_rate="1/2", bt=0)` 指定对应信号参数。两处 CR 应保持一致。编码器完成所选硬件头、CRC16/24/32、所选 FEC 和前导；CR=1/2、3/4 有六个零尾比特，CR=1 无 FEC、无尾比特。`modulation.waveform` 完成可选高斯成形、连续相位积分、幅度渐变和数字频移。`io.iq` 流式量化写文件；重复次数增加时不把所有突发累积到内存。

## 验证范围

当前采用现有解调模型的逆过程，支持 32bit AGC、21bit 定时前导、32bit 同步字、16bit 变长头或无头固定包长、CRC16/24/32。CR=1/2、3/4、1 与 BT=0、0.5、1 均可指定，详见 [CR / BT 参数说明](phy-options.md)。连续相位波形默认 BT=1、h=0.5，单侧频偏为码率的四分之一（325kb/s 时为 81.25kHz）。

已经验证捕获包的头和 CRC 金标准，以及生成 IQ 经现有软件解调后的逐字节一致性；这些检查不能替代与 SX1280 硬件的实际空口互通测试。当前实现为离线文件调制。


## 与解调共用物理模板

`modulate` 和 `transmit` 均可使用 `--config templates/physical/h12.toml`；包头、码率、CR、BT、固定长度与 CRC 宽度继承接收配置。该模板包含多个接收 CRC 初值，发送必须显式选择，例如 `--crc-initial 0`。这只是选择编码初值，不代表自动判定上下行方向。H12 对频同步字为 `--sync 54313253`（T12S），正常通信同步字需按实际设备配置。

模板可增加 `[transmit]` 表保存确定的发送设置，例如：

```toml
[transmit]
crc_initial = 0x00000000
sync_word = "54313253"
payload_lengths = [32]
# frequency = ...  # 实际 RF 中心频率（整数 Hz），确认后填写
```

`transmit` 需要显式 `--frequency` 或 `[transmit].frequency`；不会选择接收 `frequencies`、`center_frequency` 中的数字混叠频点。固定包长必须与完整物理载荷一致，应用字段和业务校验仍由协议编码插件或原始载荷提供。离线 `--verify` 使用同一包头、CRC 宽度和初值回读生成的 IQ。
