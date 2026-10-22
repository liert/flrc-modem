# FLRC Modem

模块化 Python FLRC 调制与解调项目。解调输出 **通过硬件 CRC 的原始载荷**，可选内置 DK32S、DataLink 协议模块或外部解析插件；调制可输出 IQ 文件，也可直接通过 HackRF / USRP 实时发送。独立后端支持文件收发、自定义数据源和实时解调。代码附中文注释。

支持交错 I/Q 文件（`cs8 / cu8 / cf32`）、指定单个或多个载频、下变频与抽取、粗 CFO 补偿、多采样相位判决、CR=1/2、3/4、1、变长或固定长度成包、CRC16/24/32，以及控制台和 JSONL 输出。调制支持 BT=0、0.5、1、数字频率偏移、突发重复和落盘回读验证。

当前实现针对 **32bit AGC 前导、21bit 定时前导、32bit 同步字**。默认变长包、CRC24，保持 DK32S 行为；H12 固定包、CRC32 已用真实 IQ 验证。解调成包参数可通过 TOML 或 CLI 配置，详见 [成包参数与 H12 验证](docs/packet-engine.md)。发射 CLI 仍默认使用变长包、CRC24，底层 `encode_packet()` 另支持指定头类型及 CRC 长度。

## 安装

需要 Python 3.11+：

```powershell
cd flrc-modem
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

开发代码位于 `src/`，修改后无需重新安装。也可直接使用环境内的 `flrc` 命令。

命令统一为 `flrc webui|rx|tx|demod|modulate|live|transmit|exchange|sync|duplex|hackrf|usrp`，使用 `flrc --help` 查看总览；省略子命令时（例如 `flrc --iq ...`）仍按离线解调处理。迁移旧环境后需重新执行 `python -m pip install -e .`，自定义插件的导入路径同步改为新包名。

## 本地 WebUI 交互工作台

项目内置专业深色工作台本地 WebUI，采用模块化单体架构（FastAPI + React 18 + React Flow + ECharts + Tailwind CSS）：

```powershell
# 1. 确保已安装项目依赖 (包含 FastAPI、Uvicorn、WebSockets、HTTPX 等后端依赖)
python -m pip install -e .

# 2. 安装并构建前端界面 (编译产物输出至 webui/frontend/dist，由 FastAPI 静态托管)
cd webui/frontend
npm install
npm run build
cd ../..

# 3. 一键启动 WebUI 工作台并自动打开浏览器 (默认 http://127.0.0.1:8000)
flrc webui

# 或显式指定端口与地址 (支持 python -m flrc_modem webui)
flrc webui --host 127.0.0.1 --port 8000
```

> **前端开发热重载**：UI 开发者可在 `webui/frontend` 运行 `npm run dev` 启动 Vite HMR 开发服务器（默认端口 5173，自动反向代理后端 8000 端口）。

### 核心功能区：
1. **总览仪表盘 (Dashboard)**：实时监测 USRP N310 / HackRF 硬件连接与独占占用状态、当前活动进程、最近任务耗时/退出码及文件产物。
2. **命令控制库 (Command Library)**：图形化检索所有已注册命令（`demod`, `modulate`, `live`, `transmit`, `exchange`, `sync`, `duplex`, `rx`, `tx`, `hackrf`, `usrp`），基于 Schema 动态生成参数表单，支持最终 CLI 预览。“模板预设”采用下拉选择框，支持不选择模板（纯手动配置参数）或选定模板只读引用，并支持一键跳转到模板库查看与编辑源码。“报文收发”分组统一支持同步时隙收发（USRP，调用 `duplex`）与盲发（HackRF，调用 `transmit`）两种模式，按模式呈现参数配置。启动前可点击“检查并预览生效参数”（Preflight 校验），在不占用/打开设备的前提下检查报文长度、同步字、CRC 和采样率。
3. **模板预设库 (Template Library)**：左侧主导航独立管理模块，集中呈现与分类筛选物理层 TOML 参数配置与业务报文 JSON 模板；支持语法高亮与快捷保存 (Ctrl+S)、实时磁盘文件持久化、16通道载荷结构化监视器以及“在命令库中使用”一键载入。
4. **数据流工作台 (Dataflow Pipeline)**：基于 @xyflow/react 的动态节点式数据流画布，动态映射真实处理阶段（Source/Processor/Sink）、实时进度与局部指标。
5. **信号与解调分析 (Signal Analysis)**：本地 IQ 功率谱密度 (PSD)、原始 I/Q 散点与星座图、包络幅度变异系数与硬件 CRC 通过帧的载荷 Hex 检查器。离线解调任务可点击帧时间戳定位附近 IQ，联动显示频谱、星座图和带帧起点标记的时域波形（采用包络峰谷降采样）；支持前后帧切换、观察窗口长度调整及跨任务分析上下文同步。
6. **任务执行中心 (Task Center)**：任务状态机管理、实时终端日志流 (stdout/stderr 筛选与一键复制)、结构化事件列表及 Windows 进程树优雅/强制终止控制。任务名称默认包含命令、配置与关联文件，可点击名称旁的铅笔重命名（留空恢复自动名称）；支持按名称、文件、配置、日期或 ID 搜索、状态筛选和分页浏览。已结束任务可点击“重新执行”，沿用原命令、参数、名称与输出路径，任务 ID 不变，列表仍保留一条任务。详情中的执行时间线记录每次执行的时间、状态、耗时与退出码，点击可查看对应日志、事件、参数和产物关联。同名输出仍会覆盖，历史文件被覆盖时明确标记并禁用下载；执行历史不会因同名覆盖自动清理。对应 API 为 `POST /api/v1/runs/{run_id}/rerun`，`GET /api/v1/runs/{run_id}/attempts` 读取时间线，详情、日志、事件和产物 API 可通过 `?attempt=N` 选择轮次；运行中或排队中的任务须先停止。已结束任务可单个删除，或勾选后批量删除；删除会清理整个任务及所有轮次历史，保留磁盘上的 IQ、JSONL 等文件。没有执行历史的旧解调任务仍按原规则在同名结果被成功覆盖后自动清理。
7. **时隙双工交互会话 (Duplex Session Panel)**：“同步时隙收发（USRP）”启用 `session` 模式后，启动仅建立持续接收与硬件时钟同步；在任务中心的会话面板载入 JSON 模板（如 `templates/messages/dk32s_tx_set_delay_ticks.json`）或输入 Hex，逐条按需在精确时隙注入发送，实时计算物理 RTT 并回显对端有效回复。每笔交互区分发送完成、匹配回复、回复超时、拒绝和中断未确认。

**物理帧与 FHSS/TDD 联动**：在“调制发射与物理空口帧剖析工作台”编辑载荷后，点击“验证当前帧的 FHSS/TDD 时序”，按实际编码、FEC、波形采样数和爬升时间计算发送及预期回复预算。两个页面共享载荷、同步字、码率和成包参数，页面切换保留当前草稿。设备跳频 JSON 配置同时用于时序预览和真实会话。

需要实时交互时，点击“带入 USRP 同步收发（DK32S）”，确认设备参数后启动会话。启动仅监听；锁定后在同步页投递载荷、添加周期任务或流程步骤，并查看交互记录。物理或硬件参数修改后须停止并重启会话；只修改载荷可点击“重新载入当前物理帧载荷”。自动匹配回复需要合法的 DK32S 请求格式。固定头和 DataLink 可验证物理时序；实时入口使用 DK32S 变长头 / CRC24 和 50 ms 时隙，仿真驻留时长不会改动实时模型。

报文收发启动前可点击“检查并预览生效参数”，检查报文长度、同步字、CRC 和采样率，检查不会打开设备。物理 TOML 可在离线调制、实时盲发与解调之间复用；优先级为 CLI 覆盖值、`[transmit]`、`[demod]`、默认值。接收模板中的多个 CRC 初值不能直接用于发送，须指定唯一发送初值；实际 RF 频率也必须明确填写，不会从 IQ 捕获的混叠频点推断。同步收发当前仅支持已验证的 DK32S 时序与变长头/CRC24 配置，H12 物理模板不能直接套用 DK32S 时隙模型。

设备选择支持 HackRF 序列号/索引、USRP 定位参数与通道；已探测到的不同物理设备可分开占用，自动或未知定位仍按类型互斥。

## 统一硬件 IQ 采样与回放发射

项目提供面向 USRP 与 HackRF 的统一硬件收发入口，支持直接落盘为 raw IQ 或从文件循环发射：

```powershell
# 1. 统一采集指令 (rx)：支持 --sdr usrp / hackrf，输出格式支持 cs8 / cu8 / cf32
# USRP N310 采集 1 秒 (推荐 2.5MSps 完美对齐 50us 整数时钟)
flrc rx --sdr usrp --args "addr=192.168.10.2" --frequency 2480000000 --sample-rate 2500000 --gain 30 --duration 1 --output outputs/usrp-rx.cs8

# HackRF 采集 1 秒 (10MSps)
flrc rx --sdr hackrf --frequency 2480000000 --sample-rate 10000000 --duration 1 --output outputs/hackrf-rx.cs8

# 2. 统一发射指令 (tx)：从 IQ 文件回放发射，支持指定重复次数 (--repeat) 与欠载策略
# USRP 发射 IQ 文件
flrc tx --sdr usrp --args "addr=192.168.10.2" --iq outputs/generated.iq --frequency 2480000000 --sample-rate 2500000 --gain 30 --repeat 3

# HackRF 发射 IQ 文件
flrc tx --sdr hackrf --iq outputs/generated.iq --frequency 2480000000 --sample-rate 10000000 --gain 30 --repeat 3
```

## 解调现有 IQ

```powershell
python -m flrc_modem --iq capture.iq --config templates/physical/dk32s_325k.toml --output outputs/dk32s.jsonl
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
flrc live --config templates/physical/dk32s_live_2m.toml --duration 15 --jsonl
```

边采集边输出成功帧，每帧刷新 JSONL；默认使用独立 DSP 进程和 100ms 重叠窗口。采样率可自动选择或用 `--sample-rate` 覆盖，已有文件 IQ 的采样率保持原采集值。积压和固件丢样默认报错停止。参数、Python 接口和实测结果见 [实时解调说明](docs/realtime.md)。

## 数据调制成信号

```powershell
# 任意 6..127B 原始物理载荷；--verify 落盘后回读解调
python -m flrc_modem modulate --payload-hex 0011223344556677 --sync b42b2bb4 --output outputs/generated.iq --repeat 3 --verify

# 也可以从二进制文件读取载荷
flrc modulate --payload-file payload.bin --sync b42b2bb4 --sample-rate 10000000 --offset-hz 60000 --iq-format cf32 --output outputs/generated.cf32 --verify
```

输入不包含硬件头和物理 CRC；调制器按配置添加变长头或固定头封装、CRC16/24/32、FEC 和前导。默认变长头、CRC24、325kb/s、BT=1、10MSps、数字偏移 0Hz；输出旁同时生成 `.json` 参数文件。厂商业务头和 sum8 需包含在输入载荷中。Python 接口和回读示例见 [调制说明](docs/modulation.md)。

调制码率限定为 SX1280 的六个 bitrate/BW 绑定档位，BW 默认自动确定；`--bandwidth-hz` 可显式填写并校验。`--ramp-time-us` 支持 2、4、6、8、10、12、16、20µs，默认 12µs，通过线性 IQ 包络实现。示例及时间定义见 [发射参数说明](docs/tx-parameters.md)。

当前波形已通过软件回环及两组真实空口比特对照。2026-10-19 修正同步字至编码数据的连续差分映射后，用户确认 DK32S 发送已可用；其他 CR/BT/速率组合尚未逐项验证硬件互通。证据见 [验证记录](docs/validation.md)。

## 不落盘实时发送

```powershell
# 直接将载荷调制并发送三次，不产生 IQ 文件
python -m flrc_modem transmit --frequency 2480000000 --sync 54455354 --payload-hex 000102030405 --repeat 3
# 动态 JSONL 输入，每行一个 {"payload_hex":"...","sync":"...","repeat":1}
flrc transmit --frequency 2480000000 --sync 54455354 --stdin-jsonl
# DK32S 业务模板；每包自动递增序号、更新时间戳并重算校验
python -m flrc_modem transmit --protocol dk32s --message-file templates/messages/dk32s_tx_channels.json --frequency 2480000000 --sync 54455354 --repeat 3
```

当前模式默认 2MSps；`--repeat 0` 可持续重复静态载荷，`--duration` 可限时停止。动态输入支持逐条载荷、同步字和重复次数；Python 接口可由业务线程持续向有界队列提交报文。参数、停止语义及缓冲延迟见 [实时发送说明](docs/transmission.md)。

发送插件内置 DK32S 和 DataLink：DK32S 支持普通/一控多机通道、透传、配对参数及部分配置命令；DataLink 支持工作模式同步、飞控类型和串口波特率配置下发。JSON 规则可改变指定字段，外部插件使用 `--encoder 模块:函数`。模板重复发送会逐包重新编码，使用示例与接口见 [协议发送插件](docs/protocol-transmission.md)。

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

## USRP (UHD) 后端与 TDD 双工

项目通过纯 Python `ctypes` 动态绑定官方 UHD C API（`uhd.dll`），零额外编译依赖，支持网络型 USRP（如 N310 / N320 / X300 / X400）及 USB 型 USRP（如 B210 / B200mini）。已在 **USRP N310（SFP0: 192.168.10.2，UHD 4.9.0.0-release）** 上完成端到端收发自检。

```powershell
# 探测与打印设备树
python -m flrc_modem usrp info --args "addr=192.168.10.2"
python -m flrc_modem usrp probe --args "addr=192.168.10.2"
```

支持指定主时钟频率（`--mcr`：`125e6`/`122.88e6`/`153.6e6`）、通道、天线、时钟源/时间源（`internal` / `external` / `gpsdo` / `sfp0`）、模拟基带带宽以及 `cs8`/`cf32` 格式，具备实时溢出与欠载统计。针对 N310 采样率分频约束，引擎内置自适应实际时钟对齐保护（Zero-Drift Protection）。参数与 Python API 见 [USRP 后端说明](docs/usrp.md)。

### FHSS 跳频跟踪与 TDD 双工收发 (`sync` / `duplex`)

基于 USRP N310 硬件时间戳的 FHSS 跳频（20频点、50ms时隙）与 TDD 双工（接收触发式、~810µs转换保护、微秒级时钟漂移跟踪）同步引擎及时间轴可视化见 [FHSS + TDD 同步说明](docs/sync.md)。无需 IQ 文件落盘，直接流式接入硬件实时分析与交互：

```powershell
# 1. 实时流式 FHSS + TDD 硬件时间戳同步分析（自动对齐 2.5MSps 完美整除）
flrc sync --sdr usrp --args "addr=192.168.10.2" --frequency 2471660000

# 2. 启用 UHD Timed Commands 硬件定时跳频
flrc sync --sdr usrp --args "addr=192.168.10.2" --timed-hopping

# 3. 启动单次 TDD 时隙同步投递 (示例：向对端发送获取跳频表查询 0x23，等待并捕获回复)
python -m flrc_modem duplex --args "type=n3xx,addr=192.168.10.2" --rf-config templates/rf/current-rf-config-20261020.json --gain 40 --tx-gain 20 --antenna RX2 --timed-hopping --message-file templates/messages/dk32s_tx_get_hops.json --json --sync-timeout 30

# 4. 启动常驻 TDD 双工收发会话 (持续校准时钟，随时由 stdin/WebUI 投递时隙请求)
flrc duplex --args "addr=192.168.10.2" --rf-config templates/rf/current-rf-config-20261020.json --timed-hopping --session
```

单次投递完成后输出详细结构化 JSON 结果，包含实际物理发射时间戳、发射时隙号、物理 RTT 往返耗时及对端解调回复报文：
```json
{
  "success": true,
  "tx_payload_hex": "0d16f0930684c80017d00700003a",
  "tx_hw_time": 3.0705784097584283,
  "tx_slot": 17,
  "tx_frequency_hz": 2471660000,
  "rtt_ms": 1.4929957800333682,
  "reply_matched": true,
  "rx_reply": {
    "time_s": 3.0720714055384617,
    "frequency_hz": 2471660000,
    "payload_hex": "09048d5717d0070000d6",
    "sample_power_db": -35.52
  }
}
```

## 项目结构

```text
src/flrc_modem/
  entrypoint.py       flrc / python -m flrc_modem 的统一子命令分发入口
  cli.py              离线解调命令行入口
  rx_cli.py           统一 IQ 采样采集入口（支持 USRP / HackRF）
  tx_cli.py           统一 IQ 文件发射入口（支持 USRP / HackRF）
  modulate_cli.py     调制命令行、参数记录和回读验证
  live_cli.py         HackRF 实时解调命令行
  transmit_cli.py     不落盘发送、动态 JSONL 输入
  exchange_cli.py     双 HackRF 连续采集及有限发送诊断
  sync_cli.py         实时 FHSS 跳频与 TDD 硬件时间戳同步分析命令行
  duplex_cli.py       交互式 / 单次 TDD 同步双工收发机
  physical_cli.py     物理层通用参数解析与跨命令配置共享
  transmission/      有界报文队列、在线调制和发送会话
  encoding/          发送插件接口、字段变化规则、DK32S/DataLink 业务编码
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
    crc.py            CRC24 / CRC16 / CRC32
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
    protocols/
      common.py       SIYI 紧凑帧、sum8、公共位域工具
      dk32s.py        DK32S 双向封装、遥控、透传及配置
      datalink.py     DataLink F2、配对、延时及配置
webui/
  backend/            基于 FastAPI 的 REST + WebSocket 后端服务、SDR 互斥与任务进程树生命周期
  frontend/           基于 React 18 + Vite + Tailwind + React Flow + ECharts 的深色交互工作台
templates/            物理层参数预设与业务发送 JSON/TOML 模板
tests/                捕获回归、双工、WebUI 鲁棒性与单元测试
scripts/              真实 HackRF/USRP 收发、不落盘发送复测与同步时间轴脚本
docs/                 架构、同步、物理层与协议验证记录
```

## 数据解析扩展

默认只输出原始物理层载荷。使用 `--protocol` 显式启用独立的内置协议模块：

```powershell
# DK32S 普通遥控模式
python -m flrc_modem --iq capture.iq --config templates/physical/dk32s_325k.toml --protocol dk32s --output outputs/dk32s-parsed.jsonl
# SIYI 2.4G DataLink；采集频率、采样率仍需按实际设置提供
python -m flrc_modem --iq capture.iq --sample-rate 20000000 --center-frequency 2500000000 --frequency 2491660000 --protocol datalink --jsonl
```

DK32S 一控多机遥控布局使用 `--protocol dk32s-multi`，不按报文长度猜测模式。内置模块会检查业务长度和 sum8，解析结果放入 `application`；业务解析失败记录 `application_error`，原始物理帧仍输出。协议覆盖及字段见 [内置协议文档](docs/protocols.md)。

外部插件入口保留，`--protocol` 与 `--parser` 二选一：

```powershell
python -m flrc_modem --iq capture.iq --config templates/physical/dk32s_325k.toml --parser flrc_modem.parsing.example:describe_payload --jsonl
```

插件只追加 `application` 字段，不能把业务解析失败变成物理层解调失败。JSON/TOML 规则文件引擎尚未实现；接口及 JSON 字段见 [架构文档](docs/architecture.md)。

## 测试

```powershell
# 运行全部自动化回归测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 或使用 pytest (若环境中已安装)
pytest
```

小型捕获比特夹具随项目保存，大体积 IQ 留在原位置。真实 IQ 全链路核对见 [验证记录](docs/validation.md)。
