# SX1280 发射参数

## bitrate / BW

调制器按以下六档配对。`--bit-rate` 指定码率，BW 自动确定；可选 `--bandwidth-hz`（别名 `--bandwidth`）用于显式校验。单位分别为 bit/s、Hz，非法组合在写 IQ 前报错。

| bitrate | 标称双边 BW | SetModulationParams 第一个参数 |
| --- | --- | --- |
| 260000 | 300000 | 0xEB |
| 325000 | 300000 | 0xC7 |
| 520000 | 600000 | 0xAA |
| 650000 | 600000 | 0x86 |
| 1040000 | 1200000 | 0x69 |
| 1300000 | 1200000 | 0x45 |

BW 表示芯片调制解调器的标称档位，不等于生成信号实测的占用带宽。当前 IQ 波形继续由码率、BT 和 h=0.5 生成，不额外施加以 BW 为边界的砖墙滤波。采样率仍需至少为码率的四倍，并容纳数字频移。

## RampTime

`--ramp-time-us` 支持芯片的八档：

| 时间 µs | SetTxParams 的 rampTime 参数 |
| --- | --- |
| 2 | 0x00 |
| 4 | 0x20 |
| 6 | 0x40 |
| 8 | 0x60 |
| 10 | 0x80 |
| 12 | 0xA0 |
| 16 | 0xC0 |
| 20 | 0xE0 |

默认 12µs，与已有 DK32S 射频记录一致。软件采用线性幅度包络，先爬升再进入完整前导，编码数据结束后保留八个比特周期的稳定区，再下降。爬升和下降均保持相位连续、延续边界符号的频率，不压缩前导或编码数据。

```text
首部静默 → Ramp Up → 完整前导/同步/编码数据 → 8bit 时间稳定区 → Ramp Down → 静默间隙
```

爬升和下降各增加 `round(sample_rate × ramp_time_us / 1e6)` 个采样点，时间按最近的采样间隔量化。首帧前导的位置为首部静默时间加爬升时间；重复间隙从前一帧下降结束算到下一帧爬升开始。

这是对指定 RampTime 的软件 IQ 包络近似，芯片模拟 PA 的精确爬升曲线尚未实测。寄存器值仅记录在元数据中，不会向设备发送命令。

## 示例

```powershell
# 自动配对 650kb/s 与 BW=600kHz，20µs 爬升，回读验证
python -m flrc_modem modulate --payload-hex 0011223344556677 --sync b42b2bb4 --bit-rate 650000 --ramp-time-us 20 --repeat 3 --output outputs/ramp20.iq --verify

# 显式指定绑定带宽，DK32S 默认码率和爬升时间
python -m flrc_modem modulate --payload-hex 0011223344556677 --sync b42b2bb4 --bit-rate 325000 --bandwidth-hz 300000 --ramp-time-us 12 --output outputs/ramp12.iq --verify
```

Python 配置示例：`ModulationConfig(bit_rate=650_000, bandwidth_hz=600_000, ramp_time_us=20)`。改变码率后，`--verify` 自动使用对应码率和接收低通设置；手动使用解调 CLI 时，还需在 TOML 中设置适合该码率的 `filter_cutoff`，不能将 325kb/s 预设直接用于所有码率。

元数据的 `config` 保存码率、解析后的 BW、指定 RampTime；另记录 `ramp_samples`、`effective_ramp_time_us`、`preamble_offset_samples`、`envelope_model`，以及对应的芯片参数字节。

档位依据：[Semtech SX1280/SX1281 数据手册表 14-31、表 11-49](https://device.report/m/ff541551f0d9ac7cb84c96d0ac0e364c68689fdf4ba3d38b0ad5562b45e1fa5c)。软件验证覆盖六档码率与八档 RampTime，各使用 6B、127B 载荷并重复两帧；硬件空口互通尚未实测。
