# SX1280 FLRC 成包参数与 H12 验证

解调物理层不依赖 H12 帧型、魔数、CRC-8 或通道字段。默认设置仍为 DK32S 已验证的变长包、CRC24、初值 `0xFFFFFF`；H12 特有的长度和初值候选放在 `templates/h12.toml`。

## 配置

```toml
[demod]
header_type = "fixed"
payload_lengths = [32, 42, 97, 127]
crc_bytes = 4
crc_initials = [0xFFFFFFFF, 0]
```

| 项目 | 含义 |
| --- | --- |
| `header_type = "variable"` | 默认值，对应硬件 header_type=1；同步字后读取 2B 头 `00 length` |
| `header_type = "fixed"` | 对应硬件 header_type=0；同步字后直接读取载荷 |
| `header_type = "auto"` | 先尝试合法变长头，再尝试固定长度，仅输出通过 CRC 的候选 |
| `payload_lengths` | 固定包长候选，6..127B；省略时尝试全部长度，FEC 模式下会增加运算量 |
| `crc_bytes` | 2、3、4B；分别对应多项式 `0x755B`、`0x5D6DCB`、`0xF4ACFB13` |
| `crc_initial` | 单一有效初值，默认所选 CRC 宽度的全 1 |
| `crc_initials` | 非空时替代单一初值，按列表顺序尝试；不从差分掩码推断初值 |

CRC 为 MSB-first，无输入/输出反射、无 final XOR，覆盖 `sync || header || payload`；固定长度模式的 header 为空。编码窗口、最小分块重叠根据头、最长载荷、CRC 和 FEC 自动计算。

CLI 对应 `--header-type`、可重复的 `--payload-length`、`--crc-bytes`、`--crc-initial`、可重复的 `--crc-seed`。CLI 的 `--crc-initial` 会清除模板中的初值候选列表；显式的 `--crc-seed` 列表优先。

CR、CRC 长度和接收频率仍需根据配置选择；不自动穷举所有调制方式。无 CRC 帧尚不支持。前导结构和同步字长度仍为 32+21+32bit。底层 `encode_packet(..., header_type="fixed", crc_bytes=4, crc_initial=0)` 可生成对应包；发射 CLI 暂未暴露这些新成包选项。

## H12 捕获

```powershell
python -m flrc_modem demod --iq "G:\Workspace\Obsidian-Company\IoT\无人机\H12\hackrf\fresh_pairing_2403_8.iq" --config templates/h12.toml --output outputs/h12_fixed_frames.jsonl --quiet
```

零帧原因不止硬件头和 CRC：原统计 `candidate_groups=0` 表明检测阶段已无候选。原 H12 模板继承了 350kHz 低通；实测 1.3Mbps 捕获需要更宽的滤波器。模板改为 750kHz，并按这份文件配置三个数字频点约 2399.38、2402.38、2404.38MHz，允许最多 2bit 定时前导误码。

模板的 `allow_frequency_wrap=true` 允许 Nyquist 边缘信号的频谱在离散采样带内环绕。下变频后再滤波恢复；输出频率表示数字域位置，不能唯一确定实际 RF。默认关闭此选项；其他录制需调整采样率、中心频率和目标频点。

2026-10-20 实测恢复 119 帧，全部通过硬件 CRC32。与 `h12rf.py analyze` 的 69 个合法帧按时间（容差 100µs）及完整载荷逐帧比较，69/69 命中。额外 50 帧经独立 H12 原解析器复核也合法：

| 帧型 | h12rf.py | flrc-modem |
| --- | ---: | ---: |
| normal | 31 | 50 |
| pairing_request | 1 | 1 |
| pairing_ack | 4 | 4 |
| extended_fragment | 33 | 64 |

仓库只保存四种帧的接收判决比特及 CRC 金标准 `tests/fixtures/h12_packets.json`，不复制 72MB IQ。设置 `FLRC_TEST_H12_IQ` 为原文件路径可重跑外部 IQ 集成测试。

## 同步字与有效 CRC 初值的歧义

对于当前无反射、无 final XOR 的 CRC32，4B 同步字满足严格恒等式：

```text
CRC32(init=FFFFFFFF, sync || payload)
  = CRC32(init=0, bitwise_not(sync) || payload)
```

差分同步字恢复包含互补候选，因此盲模式下两个初值命中并不能证明芯片寄存器存在两个配置。JSONL 的 `crc_initial` 只是当前解码表示的有效初值；若多个组合命中，会输出 `sync_word_ambiguous=true` 和全部 `sync_crc_candidates`。主字段 `sync_word_hex` 是其中一个代表值，不应在有歧义时直接作为唯一设备地址。

H12 对频同步字已知为 `T12S`（`54313253`）。可固定它，排除反码解释：

```powershell
python -m flrc_modem demod --iq "G:\Workspace\Obsidian-Company\IoT\无人机\H12\hackrf\fresh_pairing_2403_8.iq" --config templates/h12.toml --frequency 2404380000 --sync 54313253 --output outputs/h12_pairing_t12s.jsonl --quiet
```

已恢复载荷和接收 CRC 的计算结果如下，两个方向都固定相同同步字 `54313253`：

| 帧 | 接收 CRC | init=FFFFFFFF | init=0 |
| --- | --- | --- | --- |
| 对频请求，42B | `10e44b64` | `10e44b64` | `c7a08942` |
| 对频 ACK，32B | `f5af9707` | `4269cb4c` | `f5af9707` |

进一步单独固定初值 `0`，清除模板双初值候选后重跑：固定 `--sync 54313253` 得到 4 个 ACK；不指定 `--sync` 的盲恢复得到 29 帧，其中同样有 4 个同步字 `54313253` 的 ACK，载荷以 `66778899aa` 开头、CRC 为 `f5af9707`。请求在初值 0 的盲恢复结果中对应反码同步字 `abcecdac`，不作为 T12S 请求。这两次单频点运行各约 13s，输出分别为 `outputs/h12_pairing_t12s_init0.jsonl`、`outputs/h12_pairing_blind_init0.jsonl`。

```powershell
# 仅初值 0，并盲恢复同步字：检查输出中 sync_word_hex=54313253 的 4 个 ACK
python -m flrc_modem demod --iq "G:\Workspace\Obsidian-Company\IoT\无人机\H12\hackrf\fresh_pairing_2403_8.iq" --config templates/h12.toml --frequency 2404380000 --crc-initial 0 --output outputs/h12_pairing_blind_init0.jsonl --quiet
```

这说明在上述 CRC 输入和计算模型下需要两个有效初值，不能仅靠同步字互补解释。将 ACK 的同步字改为 `ABCECDAC` 时，用 `FFFFFFFF` 又会得到 `f5af9707`，所以不能在盲解调时仅凭 CRC 指认方向。芯片寄存器的真实 seed、内部处理和同步字选择仍需独立固件/SPI 证据，候选解码本身不证明寄存器写入。

CRC 多项式及长度定义参考 [Semtech SX1280/SX1281 数据手册 §7.3、§14.5](https://shop.richardsonrfpd.com/docs/rfpd/DS_SX1280-1_V3_3.pdf)。
