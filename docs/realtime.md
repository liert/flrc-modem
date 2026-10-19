# HackRF 实时 FLRC 解调

直接接收 HackRF 数据、逐窗解调并输出 CRC24 成功帧，无需先保存 IQ。与离线解调共用物理层、窗口去重、JSONL 格式和协议插件。

## 使用

```powershell
cd flrc-modem
# 默认当前 DK32S 参数：2MSps、325kb/s、CR=3/4、BT=1
.\.venv\Scripts\python.exe -m flrc_demod live --protocol dk32s --output outputs/live-frames.jsonl

# 显式指定频率、采样率，仅接收 15 秒
python -m flrc_demod live --sample-rate 2000000 --center-frequency 2480000000 --frequency 2480060000 --duration 15 --jsonl

# 使用实时参数预设；输出原始物理帧
flrc-live --config examples/dk32s_live_2m.toml --output outputs/live-raw.jsonl --quiet
```

未指定时长时持续接收，按 Ctrl+C 停止并关闭设备；退出码为 130，已写出的帧保留。`--duration` 或 `--max-seconds` 指定时长，二选一。输出文件每帧刷新，已有文件拒绝覆盖。控制台统计写入 stderr；没有成功帧时也会报告采样数、窗口数、队列与固件丢样。

`--protocol dk32s|dk32s-multi|datalink` 和 `--parser 模块:函数` 沿用离线接口，二选一。默认仅原始物理帧；业务解析失败仍保留该帧。`--cr`、`--bt`、`--sync`、`--crc-initial` 可显式指定。

## 采样率

当前 325kb/s 单信道使用 **2MSps**，约 6.15 点/比特，接收吞吐为 4MB/s。10MSps 对此单信道不是必需的。

不传 `--config` 和 `--sample-rate` 时，自动选择采样率：至少 2MSps、至少 4 点/比特，并容纳所有目标频率与数字滤波通带，按 2MSps 向上取整。例如当前频率偏移下，650kb/s 自动选 4MSps，1.3Mb/s 自动选 6MSps。可用 `--sample-rate` 覆盖；选择多信道或远离采集中心的频率时所需采样率会增加。

传入 TOML 时尊重配置里的采样率；原有 `dk32s_325k.toml` 是 10MSps 文件预设，实时接收使用 `dk32s_live_2m.toml`，或追加 `--sample-rate 2000000`。已有 10MSps IQ 文件必须按原采样率读取，不能直接改标签成 2MSps。

HackRF 模拟基带带宽在 2MSps 时默认 1.75MHz，FLRC 数字低通为 350kHz；两者含义不同。附近强干扰可能影响低采样率接收，可按现场情况提高采样率并检查增益。

## 调度与参数

```text
HackRF USB 回调 → 原始固定池 → 窗口组装线程 → 有界重叠窗口
                                            ↓
                             独立 DSP 进程 → 成功物理帧
                                            ↓
                               协议插件 → JSONL / 终端
```

默认 DSP 独立进程，先完成进程初始化再启动采集，避免与 USB 回调竞争 GIL。每次传入一个窗口，进程间没有无界积压。`--dsp-mode thread` 将 DSP 放在调用线程，供嵌入或诊断使用；本机此模式出现过持续积压。

| 参数 | 默认值与作用 |
| --- | --- |
| `--chunk-seconds` | 0.1，窗口步进 100ms |
| `--overlap-ms` | 按最长包计算；325kb/s、CR=3/4 默认 6ms，保护跨窗口包 |
| `--rx-queue-depth` | 32 个原始 USB 缓冲，本机约 8MiB |
| `--window-queue-depth` | 8 个待解调窗口；超限报错并停止 |
| `--block-bytes` | 默认 DLL 的 USB 缓冲大小；本机 262144B |
| `--health-interval` | 1 秒检查一次固件状态；停止后再次读取 |
| `--allow-device-shortfalls` | 诊断时允许固件丢样；此时采样连续性无法保证 |
| `--dll` | 自定义 DLL |
| `--serial` / `--device-index` | 选择设备的序列号或索引，二选一；不指定时自动跳过打开失败的候选 |
| `--lna-gain` / `--vga-gain` | 默认各 16dB |

延迟包含窗口积累、重叠、USB 分块、DSP 与输出。2MSps 时本机一个 native USB 块约 65.5ms；默认 100ms 窗口不提供毫秒级即时输出保证。可缩短窗口，但应检查 `windows_queued_max` 和处理耗时是否持续增加，不能只扩大队列掩盖性能不足。

`time_s` 相对于本次接收到的第一个采样，补偿 FIR 延迟；不是硬件时间戳，也不是 UTC。采样丢失会破坏时间连续性，因此主机积压、缓冲溢出和固件丢样默认均终止接收。

## Python 接口

```python
from flrc_demod.config import DemodConfig
from flrc_demod.realtime import LiveReceiver
from flrc_demod.sdr.hackrf import HackRFConfig

def main():
    demod = DemodConfig(sample_rate=2_000_000)
    hardware = HackRFConfig(2_480_000_000, sample_rate=demod.sample_rate)
    with LiveReceiver(demod, hardware, duration=15) as receiver:
        for frame in receiver.frames():
            print(frame.packet.payload.hex(), flush=True)
            # receiver.stop() 可请求停止，并继续读取已排队窗口。
    print(receiver.stats.to_dict())

if __name__ == "__main__":
    main()
```

独立进程采用 spawn；脚本入口需要 `if __name__ == "__main__"`。交互式环境或已有线程调度的程序可设置 `dsp_process=False`，并自行评估持续吞吐。

## 验证

2026-10-16 本机真实 HackRF 接收：2MSps、100ms 窗口、独立 DSP，连续 15 秒完整接收 3000 万复数采样，主机及固件丢样为 0，窗口队列最高 1，DSP 及进程传输共约 8.43 秒，单窗口最长约 82.4ms。结果见 [测试统计](data/live-hardware-2m-15s.json)。这是本次短时测量，不代表任意干扰、信道数量或主机负载下的吞吐。

自动化测试验证：采集结束前输出成功帧、USB/窗口边界、重叠去重、停止后排队数据处理、输出失败与进程异常清理、积压及固件丢样停止、协议解析和采样率选择。真实录制首帧经过抗混叠降采样至 2MSps 后，经回调重放与实时链路恢复成功；原 10MSps 文件的 18 帧保持一致。本次真实设备实时测试未收到 CRC 成功空口帧，尚不能据此声称完成现场 DK32S 空口实时解码。
