# USRP (UHD) 后端说明

项目支持基于官方 UHD C API（`uhd.dll`）的 USRP 后端，通过纯 Python `ctypes` 动态加载，无需额外编译 C++ 扩展，兼容 Windows/Linux，支持网络设备（如 USRP N310/N320/X300/X400）及 USB 设备（如 B210/B200mini）。

已在本机与 **USRP N310（SFP0: 192.168.10.2，UHD 4.9.0.0-release，FPGA HG 8.2）** 完成端到端收发自检。

---

## 快速使用

### 1. 设备探测与详细属性

```powershell
# 探测网络上或本地连接的 USRP 设备
flrc usrp info --args "addr=192.168.10.2"

# 打印完整硬件设备树属性（包含射频子板、通道及前端参数）
flrc usrp probe --args "addr=192.168.10.2"
```

### 2. 接收 IQ 采样 (`rx` / `capture`)

统一使用 `flrc rx` 采集指令，通过 `--sdr usrp`（或指定 `--args` 自动推断）指定 USRP 后端：

```powershell
# 从 2.48GHz 载波采集 1 秒 2.5MSps cs8 数据（有符号 8 位复数采样）
flrc rx --sdr usrp --args "addr=192.168.10.2" --frequency 2480000000 --sample-rate 2500000 --gain 30 --duration 1 --output outputs/usrp-rx.cs8

# 也支持子命令形式兼容调用：flrc usrp rx ...

# 采集完成后可直接送入离线解调管道
python -m flrc_modem --iq outputs/usrp-rx.cs8 --config templates/dk32s_325k.toml --output outputs/usrp-frames.jsonl
```

### 3. 发射 IQ 采样 (`tx`)

统一使用 `flrc tx` 发射指令，通过 `--sdr usrp` 指定 USRP 后端：

```powershell
# 将调制生成的 IQ 文件通过 USRP 发射
flrc tx --sdr usrp --args "addr=192.168.10.2" --iq outputs/generated.iq --frequency 2480000000 --sample-rate 2500000 --gain 30 --repeat 3

# 也支持子命令形式兼容调用：flrc usrp tx ...
```

---

## 命令行参数一览

| 参数 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `--dll` | 自定义 `uhd.dll` 文件路径；默认自动搜索标准安装目录 | `C:\Program Files\UHD\bin\uhd.dll` |
| `--args` | 设备过滤与定位参数，如 `"addr=192.168.10.2"` 或 `"type=n3xx"` | `""` |
| `--frequency` | 射频中心载频（单位 Hz） | 必须指定 |
| `--sample-rate` | 采样率（单位 Sps，如 `2000000`） | `2000000` |
| `--gain` | 射频模拟/数字增益（单位 dB） | `30.0` |
| `--channel` | 射频物理通道号（N310 支持 0 ~ 3） | `0` |
| `--antenna` | 天线接口选择，如 `"TX/RX"`、`"RX2"` | 默认接口 |
| `--bandwidth-hz` | 模拟前端基带滤波器双边带宽（单位 Hz） | 默认自动 |
| `--clock-source` | 参考时钟源：`internal`、`external`、`gpsdo` | 默认板载 |
| `--time-source` | 时间同步基准源：`internal`、`external`、`gpsdo`、`sfp0` | 默认板载 |
| `--iq-format` | 采样数据格式：`cs8` (int8) 或 `cf32` (float32) | `cs8` |
| `--duration` | 接收持续时间（秒，`rx` 子命令必填） | 必填 |
| `--repeat` | 发射重复次数（`tx` 子命令） | `1` |

---

## Python API 调用示例

可在自定义测试脚本中直接调用 `flrc_modem.sdr.usrp` 模块：

```python
from flrc_modem.sdr.usrp import UHDLibrary, USRPConfig, USRPDevice

lib = UHDLibrary()
cfg = USRPConfig(
    frequency_hz=2480000000,
    sample_rate=2000000,
    gain=30.0,
    channel=0,
    device_args="addr=192.168.10.2",
    cpu_format="cs8",
)

with USRPDevice(lib, device_args=cfg.device_args) as dev:
    # 接收 200,000 个采样并写入文件
    with open("capture.cs8", "wb") as f:
        stats = dev.receive(f, cfg, sample_count=200000)
    print("接收统计:", stats.to_dict())
```

---

## 硬件自检脚本

项目附带硬件自检脚本 [`scripts/check_usrp_hardware.py`](../scripts/check_usrp_hardware.py)：

```powershell
python scripts/check_usrp_hardware.py --args "addr=192.168.10.2" --duration 0.1 --tx
```

自动执行 2MSps / 10MSps 采集及低增益发射验证，并在 `outputs/usrp-check-*/report.json` 中保存机器可读的性能与丢样统计。
