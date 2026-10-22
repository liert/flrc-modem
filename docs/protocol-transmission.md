# 协议发送插件

`transmit` 支持从 JSON 业务模板生成 DK32S 或 DataLink 报文，分别选择 `--protocol dk32s` / `--protocol datalink`。编码器与解析器独立；以下通道和整数时间戳示例属于 DK32S，DataLink 的配置下发见文末。

```powershell
# 固定通道值，发送三包；序号和时间戳每包自动更新
python -m flrc_modem transmit --protocol dk32s --message-file templates/messages/dk32s_tx_channels.json --frequency 2480000000 --offset-hz 60000 --sync 54455354 --repeat 3

# 第一通道循环变化、批次号递增；持续发送至 Ctrl+C
python -m flrc_modem transmit --protocol dk32s --message-file templates/messages/dk32s_tx_changing.json --frequency 2480000000 --sync 54455354 --repeat 0
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

上面对象放在模板的 `updates` 中，且需预置 `sequence`、`timestamp_ticks`、`fields.data_batch_id`。完整通道变化示例见 [dk32s_tx_changing.json](../templates/messages/dk32s_tx_changing.json)。

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

message = json.loads(Path("templates/messages/dk32s_tx_channels.json").read_text())
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

## DataLink 配置下发

选择 `--protocol datalink`。无线物理载荷使用 `N | compact`，没有 DK32S 的外层整数时间戳，也不包含主机管理协议的设备地址等字段。编码器自动计算软件长度、15bit 序号 / 标志位和 sum8，物理发送链路继续生成 FLRC 头、FEC 和 CRC。省略 `sequence` 时按本次发送流的全局包号模 32768 递增，重复发送会逐包重新编码。

已支持的结构化业务字段：

| 命令 | 业务字段 | 模板 |
| --- | --- | --- |
| `0x18` 工作模式同步 | 单字节 `mode`、`sync_count` | [datalink_tx_sync_mode.json](../templates/messages/datalink_tx_sync_mode.json) |
| `0xF3` 飞控类型设置 | 单字节 `fcu_type`，不推断厂商枚举名称 | [datalink_tx_fcu_type.json](../templates/messages/datalink_tx_fcu_type.json) |
| `0xF7` 串口波特率设置 | `baud_rate` 或 `baud_index`，二选一 | [datalink_tx_baud_rate.json](../templates/messages/datalink_tx_baud_rate.json) |

三份模板均为 `ground_to_air`、`need_ack=false`、`is_ack=true`。这是 DataLink 天空端对应无线处理函数所接受的下发属性，不能照搬 DK32S 配置请求的 `need_ack=true`。结构化编码会拒绝方向或标志位不符的请求。`timestamp_ticks`、`rc_mode` 以及拼错或多余的字段也会被拒绝。

波特率索引 `0..6` 对应 `4800 / 9600 / 38400 / 57600 / 76800 / 115200 / 230400`。模板示例为 `baud_rate=115200`（索引 5）。飞控类型模板示例为原始值 0；固件收到类型 2/5/7 时还会将串口索引设为 5，类型 1/3/4/6 会设为 3，类型 8 跳过串口重新配置。编码器只构造指定业务字节，实际配置变更由接收固件处理。

模式同步模板示例 `mode=1 / sync_count=1`；第二阶段需使用 `sync_count=2`。天空端要求收到的模式与本地待同步模式相同，且同步计数依次递增；此模板没有自动执行完整模式切换握手，也不会因为重复发送次数为 2 就自动改变 `sync_count`。

WebUI 在“报文收发 → 盲发”选择上述业务模板时，会自动切换发送编码器到 `datalink`；物理配置选择 `physical/datalink.toml`，实际 RF 频率和同步字需按当前设备设置。模板也可用于双 HackRF `exchange`；当前 DK32S 同步时隙模型不适用于 DataLink，因此不在同步会话的报文选项中展示这些模板。

原始业务入口 `fields.data_hex` 支持其他命令或已知的原始业务字节，并自动完成紧凑帧封装；它只校验十六进制、字段互斥和 FLRC 物理长度，不检查命令语义。业务数据最多 121B，加上软件长度和紧凑帧头尾后为 127B，这只是物理上限，不代表固件支持所有此长度的命令。

### 固件静态依据

以下为已打开 DataLink v0.1.4 分析数据库的静态观察，未对设备执行配置或射频发送：

- 天空端 svn5637，SHA-256 `63c5bd50dd205d475088eed313543d4157fb1dd74a55d19c91bfed1633d75cd0`：处理表 `0x200006D0`、`0x200006E0`、`0x200006F0` 分别指向 `rf_mode_syn_action`（`0x080113A8`）、`config_fcu_type_set_handler`（`0x08011888`）、`config_uart_baud_set_handler`（`0x08011810`）。三个处理函数都检查 `frame->is_ack`，分别读取 2B / 1B / 1B 业务载荷。波特率表位于 `0x200000C0`，`config_uart_baud_apply`（`0x0800DB90`）按索引读取它。
- 地面端 svn5636，SHA-256 `61028a75b063e17a2d9ddb18d51d1647396e37e2f43d2509d6fc1cc15067db48`：`config_fcu_type_send_cmd`（`0x0800CDFC`）、`config_uart_baud_send_cmd`（`0x0800CD08`）分别发送 `0xF3` / `0xF7` 的单字节配置值。无线封装与既有双向 DataLink 抓包夹具 `tests/fixtures/datalink_packets.json` 一致。
