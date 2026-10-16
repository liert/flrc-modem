# CR 与 BT 参数

调制、解调均支持 `--cr 1/2|3/4|1`、`--bt no|0.5|1`。默认 CR=3/4、BT=1，保持 DK32S 捕获的已验证设置。

## CR：编码率

CR 决定纠错编码和编码区长度。指定错误时，硬件头或 CRC 通常无法通过；当前不自动猜测 CR。

| CR | 当前算法 | 尾比特 | 最大 127B 载荷的编码区长度 |
| --- | --- | --- | --- |
| 1/2 | K=7、0x6D/0x4F 卷积码，不穿孔 | 6 | 2124bit |
| 3/4 | 同一卷积码，按 `[AB, A, B]` 穿孔 | 6 | 1416bit |
| 1 | 无 FEC，直接保留数据比特 | 0 | 1056bit |

长度包含 16bit 硬件头、载荷、24bit CRC 和尾比特，未包含前导与同步字。空口比特率 `--bit-rate` 不因 CR 改变；CR 改变有效数据速率和包持续时间。

解调器默认按 CR 自动选择编码窗口及足够的分块重叠。若 TOML 显式写了 `max_coded_bits` 或 `overlap_seconds`，过小的值会报错；例如 325kb/s、CR=1/2 不能继续使用 1500bit 窗口或 6ms 重叠，可省略这两项使用自动值。

CR=1 没有纠错能力，输出的 `fec_path_metric=0` 表示没有经过 Viterbi，不能解释为测得零误码。

## BT：高斯成形

| BT | 调制端行为 |
| --- | --- |
| no | 跳过高斯滤波，仍使用连续相位积分 |
| 0.5 | 较强高斯成形 |
| 1 | 较弱高斯成形，默认值 |

BT 改变频率脉冲、旁瓣和判决裕量，不改变 FEC、载荷长度或 CRC 算法。`no` 表示关闭滤波，不是把高斯公式中的 BT 设置为 0。

本项目接收器使用多采样相位的非相干差分判决，BT 不要求像 CR 一样严格匹配。解调端的 `--bt` 按波形模型缩放前导搜索门限，不是完整匹配滤波或均衡器；最终仍由 CRC 决定是否输出。已知发射端 BT 时按实际值填写，有助于使用合适的候选门限。

## 使用示例

```powershell
# 调制：CR=1/2，BT=0.5，落盘后按相同设置回读
python -m flrc_demod modulate --payload-hex 0011223344556677 --sync b42b2bb4 --cr 1/2 --bt 0.5 --repeat 3 --output outputs/half-bt05.iq --verify

# 解调该文件；采样率和频率使用项目默认值
python -m flrc_demod --iq outputs/half-bt05.iq --cr 1/2 --bt 0.5 --output outputs/half-bt05.jsonl

# 关闭成形、无 FEC
python -m flrc_demod modulate --payload-hex 0011223344556677 --sync b42b2bb4 --cr 1 --bt no --output outputs/uncoded.iq --verify
```

解调 TOML 示例，CLI 参数仍可覆盖：

```toml
[demod]
coding_rate = "1/2"
bt = 0.5  # 关闭成形时写 bt = "no"
```

## 验证与依据

三种 CR × 三种 BT 共 9 种组合，均对 6B、34B、127B 载荷完成 cs8 落盘回读，每组重复两帧，载荷和 CRC 一致。CR=3/4、BT=1 另有真实 DK32S 捕获回归；新增模式还需要对应硬件捕获验证，软件回环不等于 SX1280 空口互通。

参数模式见 [Semtech SX1280 开发套件用户指南 §7.2](https://www.mouser.com/pdfdocs/Semtech_sx1280_81_userguide.pdf)；FEC、发射高斯成形和尾比特规则见 [Semtech SX1280/SX1281 数据手册 §6.3.2、§6.3.3、§7.3.3](https://device.report/m/ff541551f0d9ac7cb84c96d0ac0e364c68689fdf4ba3d38b0ad5562b45e1fa5c)。本项目卷积码和穿孔模型以既有 CR=3/4 捕获模型为基础扩展。
