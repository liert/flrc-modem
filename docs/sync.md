# FHSS + TDD 硬件时间戳同步引擎说明

本项目实现了基于 **USRP N310 硬件时间戳** 的 SX1280 FLRC 调制通信同步引擎，覆盖 FHSS 跳频序列跟踪、TDD 时分双工时序估计、微秒级时钟漂移拟合、UHD Timed Commands 硬件调度及时间轴可视化。

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

## 四、 USRP N310 硬件定时命令 (Timed Commands) 与 AD9361 调谐评估

利用 UHD 的 `uhd_usrp_set_command_time` 与 `uhd_usrp_clear_command_time` 接口：
- **提前下发量 (Lead Time)**：$t_{\text{lead}} \ge 2.0\text{ ms}$（当前默认 5.0ms）。
- **AD9361 锁相环建立时间**：$\tau_{\text{settling}} \approx 300 \sim 500\text{ }\mu\text{s}$。
- **槽尾可用守候期 (Slot Guard)**：~11.0 ms。
- **安全裕量**：$\text{margin} = 11.0\text{ms} - (5.0\text{ms} + 0.5\text{ms}) = 5.5\text{ ms} > 0$，切换完全落在静默守候窗口内，绝不污染空口业务。

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
flrc sync --sdr usrp --args "addr=192.168.10.2" --timed-hopping

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
