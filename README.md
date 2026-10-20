# FLRC Modem

模块化 Python FLRC 调制与解调项目。解调输出 **硬件头、FEC、CRC24 通过的原始载荷**，可选内置 DK32S、DataLink 协议模块或外部解析插件；调制可输出 IQ 文件，也可直接通过 HackRF 实时发送。独立后端支持文件收发、自定义数据源和实时解调。代码附中文注释。

支持交错 I/Q 文件（`cs8 / cu8 / cf32`）、指定单个或多个载频、下变频与抽取、粗 CFO 补偿、多采样相位判决、CR=1/2、3/4、1、变长硬件头、CRC24，以及控制台和 JSONL 输出。调制支持 BT=0、0.5、1、数字频率偏移、突发重复和落盘回读验证。

当前实现针对 **32bit AGC 前导、21bit 定时前导、32bit 同步字、变长包、CRC24**。CR=3/4、BT=1 已用 DK32S 真实捕获验证；三种 CR 与三种 BT 的 9 种组合均通过软件回环。固定包长和其他 CRC 长度尚未实现。

## 安装

需要 Python 3.11+：

```powershell
cd flrc-modem
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

开发代码位于 `src/`，修改后无需重新安装。也可直接使用环境内的 `flrc` 命令。

命令统一为 `flrc demod|modulate|live|transmit|exchange|hackrf|usrp|sync`，使用 `flrc --help` 查看总览；省略 `demod` 时仍按离线解调处理。迁移旧环境后需重新执行 `python -m pip install -e .`，自定义插件的导入路径同步改为新包名。

## 解调现有 IQ

```powershell
python -m flrc_modem --iq capture.iq --config templates/dk32s_325k.toml --output outputs/dk32s.jsonl
```

或显式传入采集参数：

```powershell
python -m flrc_modem --iq capture.iq --sample-rate 10000000 --center-frequency 2480000000 --frequency 2480060000 --bit-rate 325000 --iq-format cs8 --output outputs/frames.jsonl
```

- `--frequency` 可重复，单位为 Hz；不使用厂商信道编号。
- `--sync b42b2bb4` 指定恢复后的同步字字节，默认盲恢复；和旧脚本“原始判决域同步字”过滤的含义不同。
- `--max-seconds 0.1` 只处理开头 100ms，便于快速验证。
- `--jsonl` 让 stdout 只输出 JSONL，统计写入 stderr，便于管道处理。
- `--quiet --output outputs/frames.jsonl` 只保存成功帧。
- CLI 显式参数覆盖 TOML。没有使用业务长度或 sum8 筛选，因此未知协议载荷也能保留。
- 调制、解调均接受 `--cr 1/2|3/4|1` 和 `--bt 0|0.5|1`，默认 `3/4`、`1`；`0` 关闭高斯成形。CR 必须与信号编码一致；BT 在解调端用于调整前导门限。详见 [CR / BT 参数说明](docs/phy-options.md)。

IQ 为有符号 8 位交错数据时使用 `cs8`；`cu8` 为无符号 8 位，`cf32` 为小端 float32 交错 I/Q。IQ 文件不包含采样率/中心频率元数据，必须按采集设置提供。

## 实时解调

```powershell
# 当前 DK32S 单信道默认 2MSps；持续接收至 Ctrl+C
python -m flrc_modem live --protocol dk32s --output outputs/live-frames.jsonl
# 显式实时预设，接收 15 秒
flrc live --config templates/dk32s_live_2m.toml --duration 15 --jsonl
```

边采集边输出成功帧，每帧刷新 JSONL；默认使用独立 DSP 进程和 100ms 重叠窗口。采样率可自动选择或用 `--sample-rate` 覆盖，已有文件 IQ 的采样率保持原采集值。积压和固件丢样默认报错停止。参数、Python 接口和实测结果见 [实时解调说明](docs/realtime.md)。

## 数据调制成信号

```powershell
# 任意 6..127B 原始物理载荷；--verify 落盘后回读解调
python -m flrc_modem modulate --payload-hex 0011223344556677 --sync b42b2bb4 --output outputs/generated.iq --repeat 3 --verify

# 也可以从二进制文件读取载荷
flrc modulate --payload-file payload.bin --sync b42b2bb4 --sample-rate 10000000 --offset-hz 60000 --iq-format cf32 --output outputs/generated.cf32 --verify
```

输入不包含硬件头和物理 CRC；调制器自动添加头、CRC24、FEC 和前导。默认 325kb/s、BT=1、10MSps、中心偏移 +60kHz；输出旁同时生成 `.json` 参数文件。厂商业务头和 sum8 需包含在输入载荷中。Python 接口和回读示例见 [调制说明](docs/modulation.md)。

调制码率限定为 SX1280 的六个 bitrate/BW 绑定档位，BW 默认自动确定；`--bandwidth-hz` 可显式填写并校验。`--ramp-time-us` 支持 2、4、6、8、10、12、16、20µs，默认 12µs，通过线性 IQ 包络实现。示例及时间定义见 [发射参数说明](docs/tx-parameters.md)。

当前波形已通过软件回环及两组真实空口比特对照。2026-10-19 修正同步字至编码数据的连续差分映射后，用户确认 DK32S 发送已可用；其他 CR/BT/速率组合尚未逐项验证硬件互通。证据见 [验证记录](docs/validation.md)。

## 不落盘实时发送

```powershell
# 直接将载荷调制并发送三次，不产生 IQ 文件
python -m flrc_modem transmit --frequency 2480000000 --sync 54455354 --payload-hex 000102030405 --repeat 3
# 动态 JSONL 输入，每行一个 {"payload_hex":"...","sync":"...","repeat":1}
flrc transmit --frequency 2480000000 --sync 54455354 --stdin-jsonl
# DK32S 业务模板；每包自动递增序号、更新时间戳并重算校验
python -m flrc_modem transmit --protocol dk32s --message-file templates/dk32s_tx_channels.json --frequency 2480000000 --sync 54455354 --repeat 3
```

当前模式默认 2MSps；`--repeat 0` 可持续重复静态载荷，`--duration` 可限时停止。动态输入支持逐条载荷、同步字和重复次数；Python 接口可由业务线程持续向有界队列提交报文。参数、停止语义及缓冲延迟见 [实时发送说明](docs/transmission.md)。

发送插件当前只内置 DK32S，支持普通/一控多机通道、透传、配对参数及部分配置命令，JSON 规则可改变指定字段；外部插件使用 `--encoder 模块:函数`。模板重复发送会逐包重新编码，使用示例与接口见 [协议发送插件](docs/protocol-transmission.md)。

## HackRF 后端

收发回传测试使用两台 HackRF：`exchange` 在接收端采样就绪后有限发送，并保留原始 IQ、自身发送包与天空回传候选。DK32S 跳频实验脚本使用当前20频点表和50ms时隙，记录主机调度迟到及跳过的时隙。真实回传结果、命令和限制见 [双 HackRF 与跳频测试](docs/dual-hackrf.md)。

Windows x64 DLL 及依赖已放入项目，直接使用：

```powershell
python -m flrc_modem hackrf info
python -m flrc_modem hackrf tx --iq outputs/generated.iq --frequency 2480000000 --sample-rate 10000000 --tx-gain 0
python -m flrc_modem hackrf rx --frequency 2480000000 --sample-rate 10000000 --duration 1 --output outputs/capture.cs8
```

多设备默认选择第一个可成功打开的设备，自动跳过失败项。`hackrf info` 可查看设备索引和序列号；收发命令可追加 `--serial <完整序列号或唯一后缀>` 或 `--device-index <从0开始的索引>`。显式选择失败不会切换设备。

采用固定缓冲池、有界队列、独立生产/消费线程和 TX flush。支持自定义 DLL、设备序列号、RF 参数、缓冲、数据源和消费者。真实设备 2/10MSps 一秒收发、尾块、模式切换和取消已通过；本机更高速率出现的固件丢样在原生工具中也能复现，默认会作为错误报告。用法、扩展接口和测量结果见 [HackRF 后端说明](docs/hackrf.md)。

## USRP (UHD) 后端

项目通过纯 Python `ctypes` 动态绑定官方 UHD C API（`uhd.dll`），零额外编译依赖，支持网络型 USRP（如 N310 / N320 / X300 / X400）及 USB 型 USRP（如 B210 / B200mini）。已在 **USRP N310（SFP0: 192.168.10.2，UHD 4.9.0.0-release）** 上完成端到端收发自检。

```powershell
# 探测与打印设备树
python -m flrc_modem usrp info --args "addr=192.168.10.2"
python -m flrc_modem usrp probe --args "addr=192.168.10.2"

# 统一采集指令 (rx / capture)：采集 1 秒 cs8 数据（支持 --sdr usrp / hackrf；N310 推荐 2.5MSps）
python -m flrc_modem rx --sdr usrp --args "addr=192.168.10.2" --frequency 2480000000 --sample-rate 2500000 --gain 30 --duration 1 --output outputs/usrp-rx.cs8

# 统一发射指令 (tx)：发射 IQ 文件（支持 --sdr usrp / hackrf）
python -m flrc_modem tx --sdr usrp --args "addr=192.168.10.2" --iq outputs/generated.iq --frequency 2480000000 --sample-rate 2500000 --gain 30 --repeat 3
```

支持指定主时钟频率（`--mcr`：`125e6`/`122.88e6`/`153.6e6`）、通道、天线、时钟源/时间源（`internal` / `external` / `gpsdo` / `sfp0`）、模拟基带带宽以及 `cs8`/`cf32` 格式，具备实时溢出与欠载统计。针对 N310 采样率分频约束，引擎内置自适应实际时钟对齐保护（Zero-Drift Protection）。参数与 Python API 见 [USRP 后端说明](docs/usrp.md)。

基于 USRP N310 硬件时间戳的 FHSS 跳频（20频点、50ms时隙）与 TDD 双工（接收触发式、~810µs转换保护、微秒级时钟漂移跟踪）同步引擎及时间轴可视化见 [FHSS + TDD 同步说明](docs/sync.md)。无需 IQ 文件落盘，直接流式接入硬件实时分析：

```powershell
# 实时接入 USRP N310 执行 FHSS + TDD 硬件时间戳同步分析（自动对齐 2.5MSps 完美整除）
flrc sync --sdr usrp --args "addr=192.168.10.2" --frequency 2471660000

# 启用 UHD Timed Commands 硬件跳频
flrc sync --sdr usrp --args "addr=192.168.10.2" --timed-hopping

# 启动交互式 TDD 同步双工收发机（持续同步校准，随时按需时隙发射并接收对端回复）
flrc duplex --args "addr=192.168.10.2"
```

## 项目结构

```text
src/flrc_modem/
  entrypoint.py       flrc / python -m flrc_modem 的统一子命令入口
  cli.py              命令行参数和程序入口
  modulate_cli.py     调制命令行、参数记录和回读验证
  live_cli.py         实时命令行、自动采样率和即时输出
  transmit_cli.py     不落盘发送、动态 JSONL 输入
  transmission/      有界报文队列、在线调制和发送会话
  encoding/          发送插件接口、字段变化规则、DK32S 业务编码
  cli_options.py      离线/实时共享参数
  realtime/          采集会话、有界重叠窗口、独立 DSP 进程
  sdr/hackrf/         DLL 绑定、设备句柄、固定池收发、输入适配及内置 DLL
  sdr/usrp/           UHD C 动态库绑定、设备句柄、发现及收发流式 CLI
  sync/               基于 USRP 硬件时间戳的 FHSS 跳频与 TDD 双工同步引擎
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
templates/            物理层参数预设与发送模板
tests/                捕获回归、错误校验和分块边界测试
scripts/              真实 HackRF 收发及不落盘发送复测脚本
docs/                 架构和验证记录
```

## 数据解析扩展

默认只输出原始物理层载荷。使用 `--protocol` 显式启用独立的内置协议模块：

```powershell
# DK32S 普通遥控模式
python -m flrc_modem --iq capture.iq --config templates/dk32s_325k.toml --protocol dk32s --output outputs/dk32s-parsed.jsonl
# SIYI 2.4G DataLink；采集频率、采样率仍需按实际设置提供
python -m flrc_modem --iq capture.iq --sample-rate 20000000 --center-frequency 2500000000 --frequency 2491660000 --protocol datalink --jsonl
```

DK32S 一控多机遥控布局使用 `--protocol dk32s-multi`，不按报文长度猜测模式。内置模块会检查业务长度和 sum8，解析结果放入 `application`；业务解析失败记录 `application_error`，原始物理帧仍输出。协议覆盖及字段见 [内置协议文档](docs/protocols.md)。

外部插件入口保留，`--protocol` 与 `--parser` 二选一：

```powershell
python -m flrc_modem --iq capture.iq --config templates/dk32s_325k.toml --parser flrc_modem.parsing.example:describe_payload --jsonl
```

插件只追加 `application` 字段，不能把业务解析失败变成物理层解调失败。JSON/TOML 规则文件引擎尚未实现；接口及 JSON 字段见 [架构文档](docs/architecture.md)。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

小型捕获比特夹具随项目保存，大体积 IQ 留在原位置。真实 IQ 全链路核对见 [验证记录](docs/validation.md)。
