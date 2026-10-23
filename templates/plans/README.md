# 同步收发编排模板

预设方案从本目录的 JSON 文件加载，界面中的名称、说明、报文、周期与步骤均来自文件：

- `heartbeat.json`：心跳保活轮询。
- `handshake_loop.json`：跳频协商与参数握手。
- `timing_ladder.json`：阶梯时延测试。

文件包含 `template` 元数据和 `plan` 编排定义。`template.schema_version` 为 `1`，`template.kind` 为 `duplex_plan`，`title`、`description` 用于菜单展示，`protocol` 标识所用报文协议。

`plan` 使用实际编排执行器的格式：`plan_id`、`periodic`、`steps`、`cycles`。任务可携带 `message` 或 `payload_hex`；两者只能选一项。周期任务设置 `interval_ms` 与 `count`，流程步骤设置 `delay_ms` 与 `on_failure`。至少需要一条任务，保存时复用执行器校验。

可以直接编辑文件，或在 WebUI 的“模板预设库 → 编排方案”中新建、编辑和删除。新建时先补充任务再保存，也可复制已有方案的内容修改。重新打开“预设方案”菜单会重新读取模板，无需重新编译前端。

选择方案只生成独立草稿，修改草稿不会自动覆盖模板；需在模板库中保存才能修改预设。已有的纯编排 JSON 导入/导出保持兼容，也支持直接导入带 `template` / `plan` 的模板文件。选择模板后仍需手动启动编排。
