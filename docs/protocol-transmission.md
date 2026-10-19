# DK32S 协议发送插件

`transmit` 支持从 JSON 业务模板生成 DK32S 报文。当前只内置 DK32S 发送编码器，原有 DK32S/DataLink 解析插件保持独立。

```powershell
# 固定通道值，发送三包；序号和时间戳每包自动更新
python -m flrc_modem transmit --protocol dk32s --message-file examples/dk32s_tx_channels.json --frequency 2480000000 --offset-hz 60000 --sync 54455354 --repeat 3

# 第一通道循环变化、批次号递增；持续发送至 Ctrl+C
python -m flrc_modem transmit --protocol dk32s --message-file examples/dk32s_tx_changing.json --frequency 2480000000 --sync 54455354 --repeat 0
```

示例频率和 `TEST` 同步字用于测试，应替换为实际参数；频率、同步字、CR、BT、码率、RampTime、设备选择仍由原有发送参数控制。

## 模板字段

```json
{
  "direction": "ground_to_air",
  "command": "0x11",
  "need_ack": false,
  "is_ack": false,
  "fields": {
    "channels_raw": [1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024]
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `direction` | 必填，`ground_to_air` 地面到天空，或 `air_to_ground` 天空到地面 |
| `command` | 必填，命令整数或 `"0x11"` 形式的字符串 |
| `sequence` | 可选，0..32767；省略时取本次流的全局包号模 32768 |
| `timestamp_ticks` | 地面发包可选，uint32、每 tick 100µs；省略时按前导起点的样本时钟生成并回绕 |
| `need_ack` / `is_ack` | 均默认 false，按业务明确设置；不会自动等待/生成应答 |
| `rc_mode` | 默认 `normal`；`multi` 为一控多机遥控布局 |
| `fields` | 业务字段对象 |
| `updates` | 可选，发送每一包前执行的字段变化规则 |

明确填写的序号/时间戳保持固定，除非添加变化规则。重复发送固定序号可能被接收机去重。

自动生成封装：地面发包 `N | timestamp_u32le | compact`；天空发包 `N | compact`。`compact` 包括数据长度/need_ack、15bit 序号/is_ack、命令、业务数据及 sum8。每包重算长度、sum8、FLRC 硬件头与 CRC24，再进行 FEC 和调制。

## 已支持的业务字段

| 方向/命令 | `fields` 内容 |
| --- | --- |
| 地面 `0x11` / `0x16`，非 ACK | `channels_raw`：16 路、各 0..2047，11bit 小端打包；可追加 `data_batch_id` 与 `stream_data_hex` |
| 上述一控多机布局 | 增加 `target_device_mask`、`device_number`，均为单字节 |
| 天空 `0x16` / `0x46` | `data_batch_id`、可选 `stream_data_hex`；`0x46` 另需 `voltage_raw` uint16 |
| 地面 `0x13` | `filter_address` uint32、`channel_indices`：20 个单字节频道索引 |
| 天空 `0x13` | `filter_address` uint32 |
| `0x17` | `correction_ticks` int32，单位 100µs |
| `0x18` | 单字节 `mode`、`sync_count` |
| 地面 `0x28/34/38/40/45/54/56/58` | 对应单字节 `auto_pairing/signal_mode/fcu_type/baud_index/voltage_reporting/vice_pairing/tx_power_level/antenna_mode`（`0x54` 开启/关闭遥控接力，亦支持 `rc_relay`/`rc_relay_switch`/`vice_match_mode`） |
| 地面 `0x55` | 切换主副；支持携带单字节 `switch_master_slave`/`switch_vice`/`rc_relay`/`vice_pairing`/`vice_match_mode`/`release_rc_relay` 或空 `fields` |
| 上述配置、接力开关及切换主副的天空确认 | 空 `fields`，自行设置 `is_ack` |
| `0x22`，以及天空 `0xF0` | `version_hex` uint32，可写十六进制字符串 |
| 任意命令的原始业务入口 | `{"data_hex":"..."}`；空业务用 `""`，与其他业务字段互斥 |

`channels_raw` 使用空口原始值；解析器中的 `channels_output` 是加 500 后的输出值，不用于发送输入。`data_hex` 仅封装原始业务字节，不检查其命令语义；未提供专用字段编码器的命令必须显式使用此入口。

固件发送长度限制：地面业务数据最多 105B，天空最多 109B。字段名拼错、数值越界、缺少字段或未使用的字段均报错。

## 字段变化

使用点路径定位字段，数组下标从 0 开始。目标字段必须先在模板中存在，避免错误拼写被忽略。

```json
{
  "sequence": {"type":"counter", "start":100, "step":1, "modulo":32768},
  "fields.channels_raw.0": {"type":"cycle", "values":[900,1024,1100]},
  "fields.data_batch_id": {"type":"counter", "start":0, "modulo":256},
  "timestamp_ticks": {"type":"clock", "start":291370, "rate":10000, "modulo":4294967296}
}
```

上面对象放在模板的 `updates` 中，且需预置 `sequence`、`timestamp_ticks`、`fields.data_batch_id`。完整通道变化示例见 [dk32s_tx_changing.json](../examples/dk32s_tx_changing.json)。

| 类型 | 计算方式 |
| --- | --- |
| `counter` | `start + 全局包号 × step`；默认 start=0、step=1；可选 modulo 回绕 |
| `cycle` | 按全局包号循环选择非空 values 数组 |
| `clock` | `start + floor(前导样本位置 × rate / 采样率)`；默认 start=0、rate=10000；可选 modulo |

全局包号从 0 开始，跨模板、JSONL 行及原始载荷继续递增。每次 `repeat` 都执行规则，`--repeat 0` 也一样；规则值越界时立即停止，不静默截断。规则不执行 Python 表达式。

时间戳使用连续 IQ 流内的计划前导起点，包括首部 guard、前面突发、gap、等待输入时的静默及当前 RampTime。它不是设备硬件绝对时间，也不会自动跟踪真实接收机时钟。USB 缓冲造成的发送延迟仍然存在。

## JSONL 与 Python

启用 `--protocol dk32s --stdin-jsonl` 后，每行使用以下封装；省略 sync/repeat 时沿用命令行值：

```json
{"message":{"direction":"air_to_ground","command":"0x16","is_ack":true,"fields":{"data_batch_id":0,"stream_data_hex":"0102"}},"sync":"54455354","repeat":3}
```

JSONL 每条 repeat 必须为正整数，EOF 后排空。未指定发送插件时继续使用原来的 `payload_hex/sync/repeat` 格式。

```python
import json
from pathlib import Path
from flrc_modem.encoding import load_encoder

message = json.loads(Path("examples/dk32s_tx_channels.json").read_text())
# sender 为按实时发送说明创建的 LiveTransmitter，已有默认同步字。
preview = sender.submit_message(message, load_encoder("dk32s"), repeat=3)
sender.finish()
with sender:
    sender.wait()
```

`submit_message` 支持逐条 sync_word、repeat、timeout；返回包号为 0 的载荷预览，实际发送在生产线程按当前上下文重新编码。入队时复制模板，避免业务线程修改影响已排队报文。

外部插件通过 `--encoder your_package.protocol:encode` 加载：

```python
from collections.abc import Mapping
from flrc_modem.encoding import EncodeContext

def encode(message: Mapping, context: EncodeContext) -> bytes:
    # context.frame_index：全局包号；start_sample：计划前导位置；sample_rate：采样率。
    # 返回 6..127B 完整物理载荷，包含业务协议封装，不包含硬件头/物理 CRC。
    return bytes.fromhex(message["data_hex"])
```

插件必须是纯函数，不依赖调用次数修改内部计数；入队预检也会调用它。字段规则执行后再调用插件，插件异常会停止发送并释放设备。

本功能完成报文构造和字段变化，没有实现跳频、配对流程、双向 ACK 管理或连接状态机。正确的单帧封装不代表接收机一定进入已连接状态；SX1280 空口互通仍需独立接收验证。
