# FHSS + TDD 硬件时间戳同步引擎说明

本项目实现了基于 **USRP N310 硬件时间戳** 的 SX1280 FLRC 调制通信同步引擎，覆盖 FHSS 跳频序列跟踪、TDD 时分双工时序估计、硬件采样时间轴上的时钟漂移拟合、UHD Timed Commands 硬件调度及时间轴可视化。

---

## 一、 三类时间维度的严格分离

在高速跳频与时分双工系统中，必须严格解耦以下三类物理时间维度：

1. **USRP 硬件采样时间 ($t_{\text{hw}}$)**：
   - 由 USRP N310 内部硬件时钟计数器维护，格式为 `(full_secs, frac_secs)`。
   - 完全独立于操作系统调度、Python GIL、以太网或 USB 缓冲延迟。
   - 前导物理到达时间：$t_{\text{arrival}} = t_{\text{hw\_chunk}} + \frac{k_{\text{peak}} - d_{\text{filter}}}{f_s}$。
2. **FHSS 跳频切换时间 ($t_{\text{hop}}$ 与 $T_{\text{slot}}$)**：
   - 载波驻留时间（Dwell Time）$T_{\text{slot}} = 50.0\text{ ms}$（500 个 100µs ticks）。
   - 20 个频点构成 1.000 秒的大跳频周期 $T_{\text{cycle}} = 1000\text{ ms}$。
   - 时隙索引：$\text{slot\_index} = (\lfloor T_{\text{remote}} \cdot 10000 \rfloor \pmod{10000}) // 500$。
3. **TDD 上下行交互时间 ($T_{\text{tdd}}$)**：
   - 槽内子帧重复周期约为 12.0 ms。
   - 包含地面下行发送持续期（~1.53ms，33B 载荷）、射频收发转换保护间隔（Turnaround Guard，~810µs）、空中回复期（~0.53ms）以及时隙末尾守候期（Slot Guard，~11ms）。

---

## 二、 协议实测与 TDD 双候选模型判定

针对两种候选模型：
- **Model A (固定周期调度，Fixed-Periodic)**：双端各自维护严格的绝对 TDMA 周期表。
- **Model B (接收触发式响应，Reception-Triggered)**：天空端仅在收到地面有效包后发起一次回复。

在真实捕获（`outputs/passive-current-20261020.jsonl` 与 `docs/data/dual-hackrf-20261020.json`）中：
1. 36 个空中回传包 **100% 紧随地面包出现**（前导到达间隔为 $2.356 \pm 0.016\text{ ms}$，转换保护时间为 $810 \pm 40\text{ }\mu\text{s}$）。
2. 未收到地面包时，空中端未发出任何独立包；遥控器关闭时，监听频点回传数为 0。
3. **实测证明当前协议运行机制为 Model B（接收触发式响应）**。同步模块同时支持两种模型的配置与解析。

---

## 三、 同步状态机与鲁棒性

状态机管理以下状态流转：
- `SEARCHING`：初始盲搜，搜集物理有效帧。
- `ACQUIRING`：捕获中，获取 1~2 个有效物理锚点。
- `LOCKED`：锁定跟踪，估计时钟漂移（ppm），预测时隙与载频，置信度达到 95%+。
- `HOLDOVER`：短暂丢包或深衰落保持，依靠漂移模型外推，避免漏解帧导致时钟中断。
- `LOST`：超时未收到新锚点，复位基准并重新搜索。
- **溢出与不连续处理**：当 UHD RXMetadata 报告 `OVERFLOW` 时，立即标记采样不连续，拒绝使用不可靠的采样计数外推，等待下一个有效硬件时间戳完成再同步。

---

## 四、USRP N310 定时跳频与提前量

N310 的射频芯片为 **AD9371**。不能将 AD9361 的锁相环建立时间当作已验证的 N310 参数。

当前实时跳频固定 RX 本振在 2450.06MHz、模拟带宽为 100MHz，使用 `rf_freq_policy=NONE` 的定时数字调谐切换 FPGA DDC。调度线程独立于解调线程，按当前硬件时钟预测未来时隙，检查命令顺序并过滤重复命令。跨调谐窗口按实际中心频率分段解调，默认丢弃调谐后的 0.5ms；该保护值是配置预算，不是已测得的模拟 PLL 建立时间。

本机 Windows/UHD 4.9 的一次硬件时间读取实测耗时约 20–32ms。调度默认要求至少 100ms 提前量，并进一步预排后续边界。缓存的硬件时间估计使用完整 RPC 往返延迟作为保守上界；时钟拟合仍使用 IQ 自身的 UHD 采样时间戳。网络提前量发生在命令执行之前，不应与执行后的射频建立时间混为同一预算。

默认只在初始监听信道解调校时，其他信道由硬件定时跟随；等待本机发送回复时解调相关窗口。库接口的 `observation_frequencies` 可增加观测信道。这个策略允许跳过大多数帧，避免将“每帧解调成功”作为跳频继续运行的条件。

TX 在启动跳频线程前初始化，后续同一本振内的频点切换仅更新数字频偏、复用 streamer。若线程延迟导致某次调谐已错过提交期限，记录 `late_tune_skips` 并重新规划未来边界，不将一次迟到变成整个接收会话的终止。

### 已验证的查询命令

接收天线连接通道 0 的 RX2，发送天线连接该通道的 TX/RX。以下命令在 N310 SFP0 上完成过真实定时跳频查询，匹配到 `0x23` 返回的 20 项跳频表（约 1.439ms 前导间 RTT）：

```powershell
python -m flrc_modem duplex --args "type=n3xx,addr=192.168.10.2" --rf-config templates/rf/current-rf-config-20261020.json --gain 40 --tx-gain 20 --antenna RX2 --timed-hopping --message-file templates/messages/dk32s_tx_get_hops.json --json
```

DK32S 天空端序号独立递增，即使 `is_ack=true` 也不回显地面序号。匹配检查方向、sum8、同步字、频点、命令、回复窗口，以及天空端序号是否相对于发送前的观测向前推进（支持 15bit 回绕）。普通周期性遥测不能据此认定为本次请求的回复。`reply_matched` 表示协议关联匹配，不声称仅凭时间邻近就证明因果关系。

`duplex --message-file` 单次模式只发送一帧，等待回复后退出。模板中的 `updates` 只用于在编码前生成字段值，不控制发送次数。例如 `dk32s_tx_rc_relay.json` 的 `sequence` 计数器 `start=100`，首次发送的实际序号为 100；同一收发机会话下一次显式发送才使用 101。调制预览和时隙重新规划不会推进这个计数器。若要使用固定序号，可删除 `updates`、保留顶层 `sequence`。

弱信号下可加 `--sync-timeout 30`，将初始同步等待及每次发送前的重新同步等待上限由默认 5 秒改为 30 秒。这个参数同时用于单次、JSONL 和交互模式；`flrc usrp duplex` 入口也支持。`--timeout` 仍只控制发送后等待回复的时间。

等待更久只增加收到有效包的机会，不降低锁定条件：目前至少需要 3 个有效地面时间锚点，覆盖至少 0.4 秒。只有射频能量或只有天空端回传不能建立地面时钟拟合。等待失败时会显示 CRC 有效帧数、地面时间锚点数、锚点跨度、溢出及接收错误。锁定后的默认 5 秒保持上限与 `--sync-timeout` 独立，不会随启动等待时间一起延长。

日志进一步区分地面帧、天空回传、解析失败、时钟观测拒绝和累计接受锚点。`CRC有效帧>0` 且当前锚点为零不代表物理层没工作：可能全部是无地面时间戳的天空回传，也可能曾经建立锚点、随后失锁清空；累计锚点和最近失锁原因可区分这两种情况。

本机远端遥控器的被动对照采集中，通道 0/RX2、2471.66MHz、40dB 接收增益下，6 秒解出 23 帧，全部为天空端 `0x16`；随后 50dB 下，8 秒解出 46 帧（地面 15、天空 31），最终锁定且无溢出。该场景可先尝试 `--gain 50 --sync-timeout 30`。增益改善取决于实际噪声、干扰和天线条件，不能由这次结果推断任意增加增益都有效。

---

## 五、 时间轴可视化与运行示例

项目提供同步运行与可视化工具 [`scripts/run_sync_engine.py`](../scripts/run_sync_engine.py)：

```powershell
python scripts/run_sync_engine.py --frames outputs/passive-current-20261020.jsonl --output-html outputs/sync_timeline.html
```

控制台输出高精度 ASCII 字符画时间轴与微秒级残差，并生成交互式 HTML/SVG 时间轴视图：

```text
=== FHSS + TDD 同步时间轴 [534.3ms -> 634.3ms] ===
534.3ms 554.3ms       574.3ms         594.3ms         614.3ms         634.3ms   
--------------------------------------------------------------------------------
FHSS: =[Slot 17]=============================[Slot 18]==============================  
TDD : TRR      TTR      TTRR      TTR########TTR      TTRR      TTR      TTRR#######  
PKT : ^V       ^ V      ^ V       ^ V        ^ V      ^ V       ^ V      ^ V          
--------------------------------------------------------------------------------
图例说明: [T] 地面TX (~1.5ms) | [G] 转换保护 (~810us) | [R] 空中RX (~0.5ms) | [#] 槽尾守候 (~11ms)
         [V] 实测前导到达   | [^] 预测前导到达
预测误差详情:
  * Pred: 误差 = +13.54 us (Seq 13921)
  * Pred: 误差 = +8.90 us (Seq 13922)
  * Pred: 误差 = +8.35 us (Seq 13923)
  * Pred: 误差 = +8.29 us (Seq 13924)
  * Pred: 误差 = +10.80 us (Seq 13925)
  * Pred: 误差 = +9.66 us (Seq 13926)
```

---

## 六、 实时同步命令 (`flrc sync` / `flrc usrp sync`)

无需 IQ 文件落盘，直接从硬件在线流式采集并实时分析：

```powershell
# 1. 接入 USRP N310 进行实时流式 FHSS + TDD 同步分析 (按 Ctrl+C 停止)
flrc sync --sdr usrp --args "addr=192.168.10.2" --frequency 2471660000 --gain 30

# 2. 启用 USRP N310 UHD Timed Commands 硬件定时跳频跟踪
flrc sync --sdr usrp --args "type=n3xx,addr=192.168.10.2" --antenna RX2 --rf-config templates/rf/current-rf-config-20261020.json --timed-hopping

# 3. 接入 HackRF 进行实时同步分析
flrc sync --sdr hackrf --frequency 2471660000

# 4. 通过子命令直接调用 (等价形式)
flrc usrp sync --args "addr=192.168.10.2" --frequency 2471660000 --output outputs/live_sync.jsonl
```

### 实时控制台滚动输出示例：
```text
[LOCKED    96.1%] t=1.130686s | Slot 17 (2471.66MHz) | TDD: ground_tx        | Drift:  +4.77ppm | ground_to_air (seq=13921)
[LOCKED    96.1%] t=1.133042s | Slot 17 (2471.66MHz) | TDD: air_rx           | Drift:  +4.77ppm | air_to_ground (seq=13795)
[LOCKED    96.1%] t=1.142686s | Slot 17 (2471.66MHz) | TDD: ground_tx        | Drift:  +4.77ppm | ground_to_air (seq=13922)
[LOCKED    96.1%] t=1.145038s | Slot 17 (2471.66MHz) | TDD: air_rx           | Drift:  +4.77ppm | air_to_ground (seq=13796)
```
会话退出时将自动计算微秒级残差，并在控制台打印 ASCII 时间轴，同时将交互式时间轴保存至 `--output-html`。

---

## 4. USRP N310 主时钟 (MCR) 与采样率对齐机制

USRP N310 硬件架构中基带 IQ 采样率由其 FPGA DDC 从主时钟频率（Master Clock Rate, MCR）执行整数分频产生：
$$\text{Sample Rate} = \frac{\text{MCR}}{\text{Decimation Factor } N}, \quad N \in [1, 1024] \cap \mathbb{Z}$$

N310 官方原生支持以下三组 MCR：
1. **125.00 MHz**（UHD 默认值）：
   - 请求 2.0 MSps 时：$125 / 2 = 62.5$（非整数），UHD 硬件将强制对齐（Coerce）至 62 分频（$\approx 2.016129\text{ MSps}$，偏差 $+8065\text{ ppm}$）。
   - **推荐整除采样率**：**2.5 MSps**（50 偶数分频，通带最平坦，已设为 USRP 模式默认值）或 5.0 MSps（25 分频）。
2. **122.88 MHz**（蜂窝/LTE 标准时钟）：
   - 请求 2.0 MSps 时：$122.88 / 2 = 61.44$（非整数）。
   - **推荐整除采样率**：**1.92 MSps**（64 偶数分频，标准速率）或 3.84 MSps（32 分频）。
3. **153.60 MHz**：
   - **推荐整除采样率**：**2.40 MSps**（64 分频）或 3.20 MSps（48 分频）。

> [!TIP]
> **自适应硬件时钟对齐保护（Zero-Drift Protection）**：
> 引擎在 USRP 启动时会实时回读 UHD 的实际生效采样率（`uhd_usrp_get_rx_rate`）。即便用户显式指定了无法整除的采样率（如 2.0 MSps），解调器与硬件时间戳提取器也会自动将计算基准调整为硬件真实频率（2016129.03 Hz），彻底避免因采样率偏差累积导致的时间戳虚假漂移与解调丢包。

---

## 5. 交互式 TDD 同步双工收发机 (`flrc duplex`)

针对“**不模拟固定角色，在后台持续接收校准同步，用户随时按需发送自定义数据，并在精准时隙打出后接收对端回复**”的核心业务需求，系统提供了 `LiveTDDTransceiver` 与 `flrc duplex` 统一交互命令：

```powershell
# 启动交互式 TDD 双工控制台（默认连接 USRP N310 192.168.10.2）
flrc duplex --args "addr=192.168.10.2"
```

### 工作机制与流程
1. **后台持续接收与动态纠偏**：后台线程持续拉取 RX IQ 流并送入解调器，高频更新时钟漂移（ppm）与 50ms 时隙边界。
2. **状态感知按需发射**：
   - 当用户在控制台输入十六进制（如 `01 02 03 04 05 06`）或文本时，引擎评估同步状态。若处于锁定状态，根据当前时隙剩余时间自动选定当前时隙或下一时隙首部作为目标硬件时刻 $t_{\text{tx}}$。
   - 在线将载荷编码调制为 FLRC IQ 突发包，并通过 UHD TX Streamer 下发硬件时间戳指令（`has_time_spec=True`）。
3. **精准捕获对端回复**：
   - 伴随运行的 RX 流在对端回传到达时完成解调，自动计算往返时延（RTT，毫秒级），并将回复载荷实时回显至控制台。

### 单次非交互调用
```powershell
# 在同步锁定后准时发射指定数据，等待回复并以 JSON 输出
flrc duplex --args "addr=192.168.10.2" --data "010203040506" --timeout 1.0 --json
```


## WebUI 常驻收发会话

在命令控制库选择“报文收发 → 同步时隙收发（USRP）”，明确 USRP 定位参数、通道和接收参数，启用 `session` 并启动。建立会话只持续接收和同步，不会发送参数表中的初始模板。在任务中心选择 JSON 模板或原始 Hex，点击“投递一条报文”；同一设备和同步引擎可连续处理多次交互。

每个请求有唯一编号和独立结果：`tx_completed`（不等待回复）、`reply_matched`、`reply_timeout`、`send_failed`、`rejected`、`cancelled` 或 `interrupted`。`tx_completed=true` 与 `reply_matched=true` 分别表示发送完成和回复匹配；回复超时不视为交互成功。失锁会触发本次发送前重新等待，不能使用旧快照强行发送。停止会话时取消尚未提交的发送，先释放设备再结束任务；未收到最终确认的请求标为中断，不自动重发。

CLI 的 `duplex --session --ui-events ndjson` 使用 stdin 控制协议，输出 NDJSON 事件。普通请求例如 `{"request_id":"query-1","message":{"direction":"ground_to_air","command":"0x23","fields":{"data_hex":""}},"wait_reply":true}`；原始载荷使用 `payload_hex` 替代 `message`。`{"action":"stop"}` 取消队列并结束会话，普通 stdin EOF 则排空已接收请求后退出。单次和原有 `--stdin-jsonl`/REPL 入口继续保留。

WebUI 的 `POST /api/v1/runs/{id}/messages` 返回 accepted 仅代表请求进入控制管道，最终执行结果由 `duplex.result` 事件和会话面板给出。每个会话最多接收 10000 个不同请求编号，最多 16 个未完成请求；重复编号被拒绝。历史结果与状态分开查询，长时间的同步状态更新不会挤掉最近的交互结果。

物理配置可读取共用 TOML，但目前时钟锚点、跳频周期、TDD 规划和回复匹配均基于 DK32S。固定头、CRC32 等其他模式会在启动前明确拒绝，需先实现对应协议的同步模型；物理层盲发和离线调制可以使用这些成包参数。
