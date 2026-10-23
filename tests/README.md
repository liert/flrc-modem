# 测试选择与维护

日常修改先运行受影响模块；共享物理层、缓冲、配置或入口变更完成后，再运行一次完整回归。无需每个小改动都重复跑全量。

```powershell
# 缓冲和断流规则：快速、不启动设备或接收子进程
python -m pytest tests/test_realtime_buffers.py -q

# 实时接收集成：采集生命周期、同步恢复、频谱、实际软件解码
python -m pytest tests/test_realtime.py tests/test_sync_backlog.py tests/test_sync_regressions.py tests/test_spectrum.py tests/test_spectrum_regressions.py -q

# 完整 Python 回归
python -m pytest -q
```

`test_realtime_buffers.py` 统一覆盖窗口与离线一致性、槽位所有权、过载策略、样本索引、断流与时间重锚；不再在实时会话和同步会话测试中重复验证窗口池实现。cs8/cf32 参数化分别保护 HackRF 和 USRP 的实际输入格式。

维护时以行为覆盖为依据：删除已移除功能的用例；相同输入与断言集中维护；保留不同设备、格式和故障边界的回归。跨入口测试验证参数传递，底层行为交给对应模块测试，避免构造不完整的实时会话模拟对象。

软件版本断言应引用项目版本。射频快照可以随设备更新，测试应验证所选文件的 OTA 同步字、跳频顺序与配置优先级；需要固定空口期望值的解码测试使用 `fixtures/` 中的稳定夹具。

未提供 `FLRC_TEST_IQ` 时跳过外部录制 IQ 测试属于正常行为，不代表该测试失效。普通回归使用假驱动或软件信号，不会打开真实设备；硬件测试需按设备文档单独执行。
