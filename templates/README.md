# 模板目录

配置文件统一存放在本目录，按用途分类；运行输出仍是任务产物，不作为模板来源。

| 目录 | 格式 | 用途 | CLI 参数 |
| --- | --- | --- | --- |
| `physical/` | TOML | 采样率、码率、CR、BT、包头、CRC | `--config` |
| `rf/` | JSON | 当前设备的有序跳频表、OTA 同步字 | `--rf-config` |
| `messages/` | JSON | 业务指令、载荷字段和动态更新规则 | `--message-file` |
| `plans/` | JSON | 周期任务、多步流程与伴随监听 | `duplex --plan-file` |

物理、射频和业务报文模板可以同时使用；编排模板可组织多条报文任务。选择或清除一种模板不会移除其他配置。同步收发示例：

```powershell
flrc duplex --config templates/physical/dk32s.toml --rf-config templates/rf/dk32s-rf-config.json --message-file templates/messages/dk32s_tx_get_hops.json --timed-hopping --json
```

`rf/dk32s-rf-config.json` 是一次设备查询快照，不保证适用于另一台设备或之后的配对。未知跳频表需从当前设备查询、配对数据或已验证配置取得；单个频率、码率和同步字不足以推算完整跳频顺序。未指定射频文件时 CLI 仍可使用内置历史表，但会明确提示来源；同步字可以从有效接收帧恢复。固定频段接力切换探测不要求完整跳频表，流程见 [编排方案](plans/README.md)。

每个模板都使用同一组 `template` 元数据，不改变现有 CLI 载荷格式：

```toml
[template]
schema_version = 1
kind = "physical_config"
title = "模板名称"
description = "用途与约束"
protocol = "dk32s"
```

JSON 使用同名的 `"template": { ... }` 对象；`kind` 为 `rf_config`、`message_payload` 或 `duplex_plan`。物理参数继续置于 `[demod]` / `[transmit]`，射频 JSON 保留顶层 `hopping_table` / `sync_word`，业务 JSON 保留 `direction` / `command` / `fields` / `updates`，编排定义置于 `plan`。元数据不会进入无线载荷。

模板库递归扫描、按相对路径区分同名文件，支持子目录读取与保存。旧的无元数据文件仍可识别，旧 `templates/<文件名>` 路径在分类目录中有唯一匹配时仍可由 CLI 读取；新配置应使用完整分类路径。
