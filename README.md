# FLRC Demod

模块化 Python 离线 FLRC 调制与解调项目。解调输出 **硬件头、FEC、CRC24 通过的原始载荷**，可选内置 DK32S、DataLink 协议模块或外部解析插件；调制将原始载荷封装、编码为复数 IQ 文件。代码附中文注释。

支持交错 I/Q 文件（`cs8 / cu8 / cf32`）、指定单个或多个载频、下变频与抽取、粗 CFO 补偿、多采样相位判决、CR=1/2、3/4、1、变长硬件头、CRC24，以及控制台和 JSONL 输出。调制支持 BT=no、0.5、1、数字频率偏移、突发重复和落盘回读验证。

当前实现针对 **32bit AGC 前导、21bit 定时前导、32bit 同步字、变长包、CRC24**。CR=3/4、BT=1 已用 DK32S 真实捕获验证；三种 CR 与三种 BT 的 9 种组合均通过软件回环。固定包长、其他 CRC 长度和实时 SDR 输入尚未实现。

## 安装

需要 Python 3.11+：

```powershell
cd flrc-modem
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

开发代码位于 `src/`，修改后无需重新安装。也可直接使用环境内的 `flrc-demod` 命令。

## 解调现有 IQ

```powershell
python -m flrc_demod --iq capture.iq --config examples/dk32s_325k.toml --output outputs/dk32s.jsonl
```

或显式传入采集参数：

```powershell
python -m flrc_demod --iq capture.iq --sample-rate 10000000 --center-frequency 2480000000 --frequency 2480060000 --bit-rate 325000 --iq-format cs8 --output outputs/frames.jsonl
```

- `--frequency` 可重复，单位为 Hz；不使用厂商信道编号。
- `--sync b42b2bb4` 指定恢复后的同步字字节，默认盲恢复；和旧脚本“原始判决域同步字”过滤的含义不同。
- `--max-seconds 0.1` 只处理开头 100ms，便于快速验证。
- `--jsonl` 让 stdout 只输出 JSONL，统计写入 stderr，便于管道处理。
- `--quiet --output outputs/frames.jsonl` 只保存成功帧。
- CLI 显式参数覆盖 TOML。没有使用业务长度或 sum8 筛选，因此未知协议载荷也能保留。
- 调制、解调均接受 `--cr 1/2|3/4|1` 和 `--bt no|0.5|1`，默认 `3/4`、`1`。CR 必须与信号编码一致；BT 在解调端用于调整前导门限。详见 [CR / BT 参数说明](docs/phy-options.md)。

IQ 为有符号 8 位交错数据时使用 `cs8`；`cu8` 为无符号 8 位，`cf32` 为小端 float32 交错 I/Q。IQ 文件不包含采样率/中心频率元数据，必须按采集设置提供。

## 数据调制成信号

```powershell
# 任意 6..127B 原始物理载荷；--verify 落盘后回读解调
python -m flrc_demod modulate --payload-hex 0011223344556677 --sync b42b2bb4 --output outputs/generated.iq --repeat 3 --verify

# 也可以从二进制文件读取载荷
flrc-modulate --payload-file payload.bin --sync b42b2bb4 --sample-rate 10000000 --offset-hz 60000 --iq-format cf32 --output outputs/generated.cf32 --verify
```

输入不包含硬件头和物理 CRC；调制器自动添加头、CRC24、FEC 和前导。默认 325kb/s、BT=1、10MSps、中心偏移 +60kHz；输出旁同时生成 `.json` 参数文件。厂商业务头和 sum8 需包含在输入载荷中。Python 接口和回读示例见 [调制说明](docs/modulation.md)。

调制码率限定为 SX1280 的六个 bitrate/BW 绑定档位，BW 默认自动确定；`--bandwidth-hz` 可显式填写并校验。`--ramp-time-us` 支持 2、4、6、8、10、12、16、20µs，默认 12µs，通过线性 IQ 包络实现。示例及时间定义见 [发射参数说明](docs/tx-parameters.md)。

当前波形按现有解调模型合成，已验证软件回环；与 SX1280 硬件的空口互通尚未实测。

## 项目结构

```text
src/flrc_demod/
  cli.py              命令行参数和程序入口
  modulate_cli.py     调制命令行、参数记录和回读验证
  config.py           TOML 配置及参数验证
  models.py           物理包和解调结果结构
  pipeline.py         分块处理、多频率调度、窗口去重
  dsp/
    frontend.py       下变频、抽取、滤波、CFO
    detector.py       前导搜索、多相位判决、候选选择
  phy/
    sync.py           同步字与差分判决域恢复
    fec.py            CR=1/2、3/4 卷积编码/Viterbi，CR=1 透传
    profile.py        CR、BT 取值及尾比特规则
    crc.py            CRC24
    packet.py         硬件头和原始载荷恢复
    encoder.py        原始载荷封装、CRC、FEC、差分区段
  modulation/
    config.py         调制参数及验证
    waveform.py       高斯成形、相位积分、上变频、重复突发
  io/
    iq.py             三种 IQ 文件格式读写及重叠分块
    output.py         终端和 JSONL 输出
  parsing/
    api.py            内置协议与外部插件加载
    example.py        不依赖具体设备的示例插件
    protocols/
      common.py       SIYI 紧凑帧、sum8、公共位域工具
      dk32s.py        DK32S 双向封装、遥控、透传及配置
      datalink.py     DataLink F2、配对、延时及配置
examples/             物理层参数预设
tests/                捕获回归、错误校验和分块边界测试
docs/                 架构和验证记录
```

## 数据解析扩展

默认只输出原始物理层载荷。使用 `--protocol` 显式启用独立的内置协议模块：

```powershell
# DK32S 普通遥控模式
python -m flrc_demod --iq capture.iq --config examples/dk32s_325k.toml --protocol dk32s --output outputs/dk32s-parsed.jsonl
# SIYI 2.4G DataLink；采集频率、采样率仍需按实际设置提供
python -m flrc_demod --iq capture.iq --sample-rate 20000000 --center-frequency 2500000000 --frequency 2491660000 --protocol datalink --jsonl
```

DK32S 一控多机遥控布局使用 `--protocol dk32s-multi`，不按报文长度猜测模式。内置模块会检查业务长度和 sum8，解析结果放入 `application`；业务解析失败记录 `application_error`，原始物理帧仍输出。协议覆盖及字段见 [内置协议文档](docs/protocols.md)。

外部插件入口保留，`--protocol` 与 `--parser` 二选一：

```powershell
python -m flrc_demod --iq capture.iq --config examples/dk32s_325k.toml --parser flrc_demod.parsing.example:describe_payload --jsonl
```

插件只追加 `application` 字段，不能把业务解析失败变成物理层解调失败。JSON/TOML 规则文件引擎尚未实现；接口及 JSON 字段见 [架构文档](docs/architecture.md)。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

小型捕获比特夹具随项目保存，大体积 IQ 留在原位置。真实 IQ 全链路核对见 [验证记录](docs/validation.md)。
