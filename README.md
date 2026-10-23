# FLRC Modem

基于 Python 的 SX1280 FLRC 调制解调与 SDR 收发工具，提供统一 CLI 和本地 WebUI。支持离线 IQ 分析、HackRF / USRP 采集与发送、信道盲扫，以及基于 USRP 硬件时间戳的 DK32S 跳频同步、TDD 收发和流程编排。

解调默认输出**通过物理层 CRC 的原始载荷**。DK32S、DataLink 业务解析按需启用；业务解析失败仍保留原始物理帧。物理层支持 CR=1/2、3/4、1，BT=0、0.5、1，变长/固定长度成包及 CRC16/24/32。

## 选择入口

| 需要完成的工作 | 入口 | 说明 |
| --- | --- | --- |
| 使用图形界面 | `flrc webui` | 参数配置、模板管理、实时工作台、任务与产物 |
| 分析已有 IQ | `flrc demod` | 多载频解调、同步字恢复、JSONL 与协议解析 |
| 将载荷生成 IQ | `flrc modulate` | 离线调制、参数记录与回读验证 |
| 采集 / 发送 IQ 文件 | `flrc rx` / `flrc tx` | 统一选择 `--sdr usrp` 或 `--sdr hackrf` |
| HackRF 实时报文接收 / 发送 | `flrc live` / `flrc transmit` | 流式解调、业务模板或动态 JSONL 发送 |
| 寻找信道与 FLRC 候选 | `flrc scan` | 离线 IQ、USRP 或 HackRF；可导出解调配置 |
| 跟踪 FHSS / TDD 时序 | `flrc sync` | USRP / HackRF 实时监听；USRP 提供硬件时间戳 |
| USRP 同步时隙收发与编排 | `flrc duplex` | 单次请求、常驻会话、周期任务与多步方案 |
| 驱动诊断与设备信息 | `flrc usrp` / `flrc hackrf` | 底层维护入口；旧 `rx/tx` 调用仍兼容 |

WebUI 复用 CLI 和核心执行器。具体参数以 `flrc <子命令> --help` 为准；所有命令也可写成 `python -m flrc_modem <子命令>`。省略子命令时按离线解调处理。

## 安装与启动

需要 Python 3.11+。以下示例使用 Windows PowerShell，并从仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\Activate.ps1
flrc --help
```

可编辑安装后，修改 `src/` 无需重新安装。未激活虚拟环境时，可用 `.\.venv\Scripts\python.exe -m flrc_modem` 代替 `flrc`。WebUI 需要保留仓库中的 `webui/` 目录；当前启动方式以源码仓库为基础。

离线调制解调不需要 SDR 驱动。硬件使用前还需准备：

- **HackRF**：项目随附 Windows x64 的 libhackrf DLL 及依赖；设备需具备可用的 USB 驱动。其他平台需安装相应动态库。
- **USRP**：安装 UHD、匹配的设备镜像并配置网络或 USB 连接。可用 `UHD_DIR` 指定 UHD 安装目录；支持该参数的命令也可通过 `--dll` 指定动态库。实机验证以 N310 为主，其他型号需核对采样率、通道与时钟设置。
- **WebUI 前端**：需要 Node.js 和 npm，建议使用 Node.js 22 LTS 或更新版本；只使用 CLI 无需构建前端。

### 先运行一次软件回环

不连接硬件，将 8 字节载荷调制成 IQ，再回读验证并输出解调结果：

```powershell
flrc modulate --config templates/physical/dk32s.toml --payload-hex 0011223344556677 --sync b42b2bb4 --sample-rate 2500000 --output outputs/quickstart.cs8 --repeat 3 --verify
flrc demod --iq outputs/quickstart.cs8 --config templates/physical/dk32s.toml --sample-rate 2500000 --center-frequency 2480000000 --frequency 2480000000 --iq-format cs8 --sync b42b2bb4 --output outputs/quickstart.jsonl
```

这里使用示例同步字。原始载荷不包含物理头和物理 CRC，由调制器添加；业务头和业务校验需包含在载荷中。

### 启动 WebUI

```powershell
cd webui/frontend
npm ci
npm run build
cd ../..
flrc webui
```

默认打开 `http://127.0.0.1:8000`，可通过 `--host`、`--port` 修改监听地址。前端构建产物位于 `webui/frontend/dist`，由 FastAPI 托管；首次使用及前端更新后需重新构建。

开发界面时保持后端运行，在另一个终端进入 `webui/frontend` 执行 `npm run dev`。Vite 配置端口为 **3000**，API 与 WebSocket 代理到本机 8000 端口。

## 常用流程

示例中的频率、USRP 地址、同步字和增益均需按实际链路设置。频率、采样率使用 Hz / Sps；IQ 文件为 raw 交错 I/Q，不含采集参数，解调或发送时必须提供匹配的采样率与格式。

### 采集与解调

```powershell
# USRP N310：采集 1 秒、2.5MSps
flrc rx --sdr usrp --args "addr=192.168.10.2" --frequency 2480000000 --sample-rate 2500000 --gain 30 --duration 1 --output outputs/usrp-rx.cs8
flrc demod --iq outputs/usrp-rx.cs8 --config templates/physical/dk32s.toml --sample-rate 2500000 --center-frequency 2480000000 --frequency 2480060000 --protocol dk32s --output outputs/usrp-frames.jsonl

# HackRF：持续实时解调，Ctrl+C 停止
flrc live --config templates/physical/dk32s.toml --sample-rate 2000000 --center-frequency 2480000000 --frequency 2480060000 --protocol dk32s --output outputs/live-frames.jsonl
```

`cs8` 是有符号 8 位 I/Q，`cu8` 是无符号 8 位 I/Q，`cf32` 是小端 float32 I/Q。HackRF 采集仅输出 `cs8`；USRP 的支持格式与转换方式见 [USRP 后端](docs/usrp.md)。

离线解调的 `--frequency` 可重复指定多个目标；省略 `--sync` 时尝试从信号恢复同步字。`--jsonl` 将帧以 JSONL 输出到 stdout，统计写入 stderr；`--protocol dk32s|dk32s-multi|datalink` 启用业务解析，也可用 `--parser 模块:函数` 加载外部解析器。

### USRP 信道盲扫

```powershell
flrc scan --sdr usrp --args "addr=192.168.10.2" --master-clock-rate 125000000 --sample-rate 12500000 --step-bw 10000000 --dwell 0.25 --passes 3 --refine-dwell 1.2 --output-config outputs/scanned_channels.toml
```

结果区分能量候选、仅前导疑似 FLRC 和 CRC 确认 FLRC；只有 CRC 确认的同步字会写入导出的解调配置。扫描默认 2 轮、未确认候选精扫 0.25 秒，USRP 调谐后默认等待 10ms 并丢弃最初 2ms 采样。

跳频与低占空比信号需要足够观察时间。复扫提高捕获机会，但无法恢复采集间隙内的突发；已知码率时可指定 `--bit-rate` 缩小搜索范围。扫描结果不等于完整、有序的跳频表。

### 发送 IQ 或业务报文

```powershell
# 发送前面软件回环生成的 IQ，保持采样率一致
flrc tx --sdr usrp --args "addr=192.168.10.2" --iq outputs/quickstart.cs8 --iq-format cs8 --frequency 2480000000 --sample-rate 2500000 --gain 20 --repeat 3

# HackRF：直接编码业务模板并发送，不生成 IQ 文件
flrc transmit --protocol dk32s --config templates/physical/dk32s.toml --message-file templates/messages/dk32s_tx_channels.json --frequency 2480000000 --sync 54455354 --repeat 3
```

业务模板逐包编码，支持序号递增、时间戳更新和校验重算。`transmit --stdin-jsonl` 可接收动态报文；原始载荷发送可用 `--payload-hex` 或 `--payload-file`。具体行为见 [实时发送](docs/transmission.md) 和 [协议发送插件](docs/protocol-transmission.md)。

### FHSS 同步监听与 TDD 会话

```powershell
# 按所选射频模板跟踪跳频，仅监听
flrc sync --sdr usrp --args "addr=192.168.10.2" --rf-config templates/rf/dk32s-rf-config.json --timed-hopping

# 常驻 USRP 同步收发会话：先接收并锁定，再按请求发送
flrc duplex --args "addr=192.168.10.2" --config templates/physical/dk32s.toml --rf-config templates/rf/dk32s-rf-config.json --timed-hopping --session
```

射频模板是一次设备查询快照，使用前确认仍对应当前设备。未指定 `--rf-config` 时使用内置历史表并提示来源；不能据此推断当前设备的跳频顺序。固定频点监听时不启用 `--timed-hopping`，并明确设置 `--frequency`。

同步收发目前使用已验证的 **DK32S 变长头、CRC24、50ms 时隙**模型，不能直接套用 H12 固定包模板。N310 双工接收使用 `RX2`，发送使用同通道 `TX/RX`。硬件发送完成和收到匹配回复是两个独立结果。

### DK32S 接力切换与同步字探测

在 WebUI 的 USRP 同步收发会话中，配置原链路同步字和一个已知监听频点，关闭定时跳频；锁定后选择“DK32S 接力切换与同步字探测”方案并启动。

同一模板也能直接从 CLI 执行，完成后退出：

```powershell
# 以下同步字和频率仅为示例，替换为原链路的实际值
$originalSync = "527ffbbd"
$listenHz = 2471660000
flrc duplex --args "addr=192.168.10.2" --config templates/physical/dk32s.toml --frequency $listenHz --sync $originalSync --plan-file templates/plans/dk32s_relay_switch_probe.json
```

流程为：启动伴随监听 → 发送 `0x54 / rc_relay=1` 并等待 ACK → 发送 `0x55 / switch_vice=1` → 继续观察 5 秒。第二步发送完成后不等待旧同步字 ACK；需要反码回复时追加 `--reply-invert-sync`。

该流程无需接力遥控器的完整跳频表，只监听配置的频点范围。默认同一候选需至少 3 帧通过物理 CRC 和 DK32S 校验；候选不自动替换原链路同步字。未捕获候选不能直接判定切换失败。监听频偏、观察时长、失败处理和结果含义见 [编排模板说明](templates/plans/README.md)。

## WebUI 工作台

WebUI 集中提供以下操作：

- **命令与模板**：按参数 Schema 配置命令、预览 CLI、发送前预检；管理物理参数、射频、业务报文和编排模板。
- **信号分析**：IQ 频谱、星座与时域波形、帧定位、载荷差分和测试向量导出。
- **实时工作台**：频谱与解调、信道盲扫、FHSS/TDD 同步、物理帧预览，以及 USRP 同步会话和发送编排。FHSS/TDD 页面仅提供实机运行。
- **任务与设备**：实时日志和事件、停止任务、重命名与重新执行、执行历史和产物，以及设备独占占用状态。

同步会话启动后先监听，锁定后才能发送；修改物理或硬件参数需要重启会话。编排预设从模板文件动态加载，选择后生成独立草稿，修改草稿不会自动覆盖模板。任务重新执行可能覆盖同名输出，具体记录见任务执行历史。

## 模板、日志与产物

| 目录 | 内容 | 使用方式 |
| --- | --- | --- |
| `templates/physical/` | DK32S、DataLink、H12 物理 TOML | `--config` |
| `templates/rf/` | 有序跳频表、OTA 同步字 JSON | `--rf-config` |
| `templates/messages/` | 业务指令、字段与动态规则 JSON | `--message-file` |
| `templates/plans/` | 周期任务、多步流程与伴随监听 JSON | `duplex --plan-file` 或 WebUI 编排预设 |
| `logs/` | CLI、WebUI 和崩溃诊断日志 | 默认 `flrc.log`、`webui.log`、`crash.log` |
| `outputs/` | 示例及默认任务产物 | IQ、解调 JSONL、扫描结果等；可指定其他输出路径 |

CLI 显式参数优先于模板。发送配置依次按 CLI、`[transmit]`、`[demod]`、默认值解析；实际发送 RF 频率必须明确填写，不能从接收混叠频点推断。接收用的多个 CRC 初值不能直接作为发送参数，需指定唯一初值。

CLI 与 WebUI 使用统一的轮转日志系统，日志写入 stderr；机器输出模式将 stdout 用于结果或事件。WebUI 将任务输出排队、后台批量落库，再通过各浏览器独立队列推送；慢连接会断开并自动重连，任务日志与关键结果仍保存在数据库中。高频指标可合并为最新快照，队列上限和过载规则见 [架构说明](docs/architecture.md)。设备探测通过独立子进程隔离驱动，异常堆栈统一写入 `logs/webui.log`，不再逐次创建 `device-probe-*.fault.log`。启动 WebUI 时会清理超过 60 秒且为空或仅含启动头的旧探测日志，有实际堆栈的文件保留。

可通过 `FLRC_LOG_LEVEL`、`FLRC_LOG_FILE`、`FLRC_LOG_FILE_FORMAT=json` 调整日志级别、普通日志文件路径与格式；`FLRC_LOG_FILE=off` 关闭 CLI 默认普通日志文件输出。崩溃监控独立保留。

实时接收共用有界缓冲和断流标记，但过载处理按用途区分：`live` 默认遇到积压或固件丢样时报错停止；同步会话跳过过期窗口、重新捕获，并在重新锁定前禁止沿用旧时序发送；实时频谱保留最新数据。断流前后的采样不会拼成同一个包。详细约束见 [实时解调](docs/realtime.md) 和 [同步说明](docs/sync.md)。

## 开发与验证

```powershell
python -m pip install pytest

# 按修改范围选择测试；示例为共享实时缓冲
python -m pytest tests/test_realtime_buffers.py -q

# 完整 Python 回归
python -m pytest -q

# 前端行为测试与构建
cd webui/frontend
npm test
npm run build
```

日常修改先验证受影响模块；共享物理层、缓冲、配置或入口变更再运行完整回归。普通回归使用软件信号或模拟驱动；外部 IQ 夹具与实机验证单独执行，维护约定见 [测试说明](tests/README.md)。

软件回环、真实录制 IQ 解码和实际硬件互通是不同层级的证据。当前实机记录以 Windows x64、HackRF 与 USRP N310 为主，未逐项验证所有速率、CR/BT 组合和硬件型号；已验证范围见 [验证记录](docs/validation.md)。

## 项目结构与详细文档

```text
src/flrc_modem/    CLI、DSP/PHY、协议插件、SDR 后端、实时收发与同步核心
webui/backend/    FastAPI API、任务管理、设备占用与实时会话
webui/frontend/   React + Vite 工作台
templates/        可编辑的物理、射频、业务报文和编排模板
tests/            软件回归与稳定夹具
scripts/          实机诊断、验证与辅助脚本
docs/             架构、参数、接口与验证记录
```

| 主题 | 文档 |
| --- | --- |
| 架构与解析插件 | [架构](docs/architecture.md)、[内置协议](docs/protocols.md) |
| 物理层与离线调制 | [成包参数与 H12](docs/packet-engine.md)、[CR / BT](docs/phy-options.md)、[调制](docs/modulation.md)、[发射参数](docs/tx-parameters.md) |
| 实时收发与信号分析 | [实时解调](docs/realtime.md)、[实时发送](docs/transmission.md)、[实时频谱](docs/spectrum.md) |
| 硬件与同步 | [HackRF](docs/hackrf.md)、[USRP](docs/usrp.md)、[双 HackRF](docs/dual-hackrf.md)、[FHSS / TDD](docs/sync.md) |
| 业务发送与编排 | [协议发送插件](docs/protocol-transmission.md)、[模板目录](templates/README.md)、[编排方案](templates/plans/README.md) |
| 验证与测试维护 | [验证记录](docs/validation.md)、[测试说明](tests/README.md) |
