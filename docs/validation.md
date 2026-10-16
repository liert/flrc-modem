# 验证记录

日期：2026-10-16。环境：Windows，Python 3.14.6、NumPy 2.5.0、SciPy 1.18.0。项目虚拟环境使用当前机器已有依赖，并完成 editable 安装。

## 真实 IQ 端到端

输入：`pair_2480_10.iq`（外部 DK32S 对频录制 IQ 文件）。

- 输入格式：交错 signed-int8 I/Q。
- 采样率：10MSps；中心：2480MHz；目标：2480.060MHz。
- 输入 SHA-256：`DD2D68378E4FE390FB756BB03F5057A8FC8E36F4C30004DE6A0E4AEFC7D0C032`。
- 原始输入留在原位置，没有把 68MB IQ 复制到项目。

```powershell
python -m flrc_demod --iq capture.iq --config examples/dk32s_325k.toml --output outputs/dk32s.jsonl --quiet
```

结果：**18 帧 CRC24 成功**，35 个处理窗口，17 次重叠窗口重复命中被合并。候选组计数 36 包含重叠重复；没有把未通过 CRC 的噪声候选写成成功帧。

18 帧的完整 `payload_hex` 与旧 DK32S 脚本已验证记录逐帧完全一致。首帧约 1.54365ms：

```text
Sync     b4 2b 2b b4
Header   00 22
Payload  21 2a 72 04 00 98 f0 06 13 e4 0a 2b 3f c2 bf 8d 21 2d 4f 9c 81 02 e1 57 af 66 ce 3c 34 12 6e 90 e8 46
CRC24    ce 85 19
```

项目保留 Payload 的全部 34B，不剥离 DK32S 时间戳或解析命令号。生成结果位于 `outputs/dk32s.jsonl`，该目录由 Git 忽略。

## 自动化检查

```powershell
$env:FLRC_TEST_IQ = "pair_2480_10.iq"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前 **69 项检查通过**，覆盖：

- 捕获比特的硬件头、载荷、CRC 金标准；错误同步字/CRC 初值、截断帧和 CRC 错误拒绝。
- 人工构造的不含应用长度/sum8 的任意载荷仍能通过物理层，确认核心没有厂商业务校验依赖。
- cs8、cu8、cf32 输入，非完整采样对、NaN/Inf、空文件检查。
- 2ms 窗口边界穿过真实突发时仍完整恢复，且只输出一次。
- 配置覆盖、带宽边界、窗口重叠验证。
- 默认输出无业务字段；插件异常不丢失物理帧；输出不能覆盖输入。
- 内置 DK32S/DataLink 双向字段及配对方向差异；整数/浮点时间、遥控位域、普通/多机显式布局、配置、未知字段保留及应用校验错误隔离。
- 调制编码与捕获包金标准一致；cs8/cu8/cf32、6B/127B 载荷、2MSps/10MSps 落盘回读；12 个相同突发跨读取窗口完整恢复，无误删重传。
- 调制 CLI 二进制载荷输入、自定义 CRC 初值、元数据记录、输入路径保护及写入失败保留原文件；验证失败返回非零状态。
- CR=1/2、3/4、1 与 BT=no、0.5、1 的 9 种组合，每种覆盖 6B、34B、127B 载荷并重复两帧；FEC 单位冲激金标准、错误 CR 拒绝、动态窗口/重叠、自定义 CLI/TOML 参数及元数据记录。
- SX1280 六档 bitrate/BW 与八档 RampTime 的 48 种组合，每种覆盖 6B、127B 并重复两帧；线性爬升/下降包络、完整数据区幅度、采样时间量化、前导时间位置、非法配对和元数据寄存器值。

未设置 `FLRC_TEST_IQ` 时，68 项不依赖外部 IQ 的检查仍可执行，真实 IQ 集成检查跳过。六档空口码率已完成软件回环；其他 CRC 长度及实时 SDR 输入尚未实现。新增 CR/BT、码率和 RampTime 模式尚缺对应硬件捕获验证。

## 调制回读

```powershell
python -m flrc_demod modulate --payload-hex 212a72040098f00613e40a2b3fc2bf8d212d4f9c8102e157af66ce3c34126e90e846 --sync b42b2bb4 --output outputs/modulated-dk32s.iq --repeat 3 --verify
```

结果：3/3 帧成功回读，同步字 `b42b2bb4`、硬件头 `0022`、完整 34B 载荷和 CRC `ce8519` 一致。使用默认 12µs RampTime 时，输出 448444 个复数采样，10MSps、cs8，时长 44.8444ms。参数同时保存在 `outputs/modulated-dk32s.iq.json`。

这是软件回环验证；SX1280 硬件空口互通尚未实测。

## 内置协议核对

对已有物理层成功记录直接加载独立协议模块，避免把应用层检查误当成新的 IQ 解调结果：

| 协议 | 记录来源 | 应用解析结果 |
| --- | --- | --- |
| DK32S | 本项目 `outputs/dk32s.jsonl` | 18/18 成功，全部为地面端→天空端配对提议 |
| DataLink | `SIYI2.4G/rf/flrc_verified_frames.json` | 349/349 成功，地面端→天空端46条、天空端→地面端303条 |

解析结果分别保存为 `outputs/dk32s-parsed.jsonl`、`outputs/datalink-parsed.jsonl`。真实双向 DataLink 报文的小型回归夹具保存在 `tests/fixtures/datalink_packets.json`；未知命令或协议解析失败不会删除顶层原始载荷。
