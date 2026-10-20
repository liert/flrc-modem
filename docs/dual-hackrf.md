# 双 HackRF 接收回传与跳频测试

单台 HackRF 是半双工。`transmit` 包间输出零 IQ 时仍处于 TX 模式，不会自动接收；双设备测试使用明确的序列号，一台持续采集，另一台有限发送。

## 固定频点诊断

```powershell
python -m flrc_modem exchange --tx-serial 2c4b154f --rx-serial 2a5faf67 --frequency 2480000000 --sync 54455354 --payload-hex 464c524330310009 --repeat 50 --sample-rate 8000000 --amplitude 0.7 --tx-gain 24 --lna-gain 32 --vga-gain 40 --rx-bandwidth-hz 1750000 --output-dir outputs/exchange-run-01
```

序列号、频率和增益需按设备及现场调整。示例发送自定义 TEST 包，用于验证两台 HackRF 间的空口链路，不请求 DK32S 回传。`--message-file` 与 `--protocol dk32s` 可改用业务模板。`--rx-center-frequency`、可重复的 `--rx-frequency` 支持独立接收设置；默认盲恢复同步字，指定 `--rx-sync` 可限制回复同步字。

程序先预生成有限 TX 波形并回读自检，两个设备均打开后启动 RX；写入足够预采样后才发送。TX flush 完成后，同时等待尾部墙钟时间和足够采样数，再停止 RX。停止射频后才解调原始文件，避免 DSP 拖慢 USB 采集。

结果目录拒绝覆盖，保存 `capture.cs8`、`sent.jsonl`、`frames.jsonl`、`report.json`。异常时保留已采集 IQ 和设备状态。`tx_echo` 表示同步字/载荷与本次生成的发送包一致；`air_to_ground_candidate` 表示同链路天空方向的回传候选。采集完成、USB flush、收到自身发送包分别记录，不能互相代替。程序不会仅凭 ACK 或序号断言回复的来源；地面和天空端序号可以各自递增。

## DK32S 跳频诊断

2026-10-20 的设备查询得到 20 个跳频点，当前同步字为 `ecee6698`，并非旧录制的 `d5e2870c`。实际配对参数会变化，应先重新运行用户的 `get_rf_channels.py --json`，把其 JSON 输出保存成配置文件。

本次真实捕获的 36 个地面包全部满足：

```text
slot_index = (timestamp_ticks % 10000) // 500
frequency_hz = hopping_table[slot_index].frequency_hz
```

时间戳单位为100µs，20个50ms时隙组成1秒周期；遥控包间隔约12ms，一个跳频时隙可发送多包。已有天空端固件分析中也存在按远端时间戳同步空口时钟并选择跳频点的路径；分析数据库版本不等同于本机运行固件版本。固定载频、持续更新时间戳却不跟随跳频，不能作为可靠的连接方法。

```powershell
python scripts/check_hopping_exchange.py --rf-config outputs/current-rf-config-20261020.json --message-file outputs/dk32s-current-probe-20261020.json --tx-serial 2c4b154f --rx-serial 2a5faf67 --rx-frequency 2471660000 --tx-correction-hz -18000 --tx-gain 24 --lna-gain 32 --vga-gain 32 --cycles 3 --output-dir outputs/hopping-run-01
```

命令中的射频配置和报文模板是本次测试保存在 `outputs/` 的文件，未随仓库分发；可用自己的当前查询结果和 `templates/dk32s_tx_channels.json` 替换模板。测试模板的通道值来自测试前真实遥控包，运行自己的模板前应检查通道值。发送时间戳由计划的全局采样时钟重建，原模板的时间戳规则会被替换，序号/其他字段更新规则保留。只支持本次建立证据的20×50ms DK32S模型，不包含配对流程。

脚本预生成全部突发，复用同一 TX 设备句柄，逐时隙调谐后发送短波形；另一台 HackRF 固定监听 `--rx-frequency`，仅观察该频点的发送及回传，不能声称录全20个频点。默认每时隙3包，间隔12ms。输出保存原始 IQ、实际生成载荷、每时隙统计、解码结果及错误；程序未使用硬件时间戳。

每时隙记录 `late_ms` 和 `elapsed_ms`。主机迟到到无法容纳波形时，记录 `skipped_late` 并跳过，避免发送过期时间戳。`late_ms` 不包含随后设备配置/USB至天线的延迟，不能据此声称硬件时序精度。Windows/USB偶发延迟及旧固件短流 flush 超时在本次实验中出现过，完整连续控制链路尚未完成稳定性验证。超时会停止并保留原始采样，不能默认为成功。

`-18000Hz` 是本次两台 HackRF 间相对频偏测量所得的实验校正，未校准绝对频率，不是所有设备通用值。接收增益过高也会削顶；应结合原始采样和解码率调整，不能一味提高。

## 本机空口证据

- 当前跳频表内三个频点的3秒被动捕获：72帧CRC24成功，地面/天空各36帧。
- 2MSps单频点实时接收5秒：40帧，地面/天空各20帧，固件丢样0，窗口队列最高1。
- 调整距离、增益和接收带宽后的TEST互发：一次50包收到49包；该短时结果不代表长期丢包率。
- 用户确认遥控器关闭后，初次20频点跳频实验在监听频点收到9个自身发送包和3个天空回传；回传紧随最后一轮的三个发送包，发送结束至回复开始约0.81–0.84ms。固定频点的多个对照未收到天空回传，跳频计划后出现回传，是本次接收机响应HackRF的实验依据。
- 正式脚本复测：60个计划时隙中43个完成发送、17个因主机迟到跳过；监听频点收到8个自身发送包和6个天空回传，RX固件丢样0。5个回传能与此前5ms以内已解码的发送包关联，间隔约0.80–0.84ms；另1个回传附近的发送包未解码，未给它强行绑定序号或时间差。
- 旧设备固件2021.03.1无法读取M0丢样统计；新设备2024.02.1作为RX时报告丢样0。没有将旧设备状态不可用写成零丢样。

复测记录和限定条件见 `docs/data/dual-hackrf-20261020.json`。这些是短时实验，不能据此声称已实现任意跳频配置、任意主机负载下的可靠完整遥控链路。
