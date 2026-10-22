"""测试 FLRC Modem 统一日志与可观测性系统。"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path
import tempfile
import time
import unittest

from flrc_modem.log import (
    WideEvent,
    bind_context,
    clear_context,
    get_current_context,
    get_logger,
    setup_logging,
    shutdown_logging,
    wide_event,
)
from flrc_modem.log.formatters import ConsoleColoredFormatter, JsonLinesFormatter


class LoggingSystemTests(unittest.TestCase):
    def setUp(self):
        clear_context()

    def tearDown(self):
        shutdown_logging()
        clear_context()

    def test_bind_context_lifecycle(self):
        self.assertEqual(get_current_context(), {})
        with bind_context(run_id="run_test_01", command_id="duplex", sdr_type="usrp"):
            ctx = get_current_context()
            self.assertEqual(ctx["run_id"], "run_test_01")
            self.assertEqual(ctx["command"], "duplex")
            self.assertEqual(ctx["sdr_type"], "usrp")

            # 嵌套覆盖
            with bind_context(run_id="run_nested", extra_key="value123"):
                nested = get_current_context()
                self.assertEqual(nested["run_id"], "run_nested")
                self.assertEqual(nested["command"], "duplex")
                self.assertEqual(nested["extra_key"], "value123")

            # 恢复外部状态
            outer = get_current_context()
            self.assertEqual(outer["run_id"], "run_test_01")
            self.assertNotIn("extra_key", outer)

        # 退出后完全恢复
        self.assertEqual(get_current_context(), {})

    def test_stdout_remains_completely_pure(self):
        """测试日志仅输出至 stderr，确保 stdout 绝对不受污染。"""
        fake_stdout = io.StringIO()
        fake_stderr = io.StringIO()

        # 初始化 setup_logging，明确使用非阻塞模式
        logger = setup_logging(
            level="DEBUG",
            output_format="text",
            stream=fake_stderr,
            nonblocking=False,
        )

        with bind_context(command_id="demod"):
            logger.info("测试信息行")
            logger.warning("测试警告")

        # stdout 必须完全为空
        self.assertEqual(fake_stdout.getvalue(), "")

        stderr_content = fake_stderr.getvalue()
        self.assertIn("INFO", stderr_content)
        self.assertIn("WARNING", stderr_content)
        self.assertIn("[demod]", stderr_content)
        self.assertIn("测试信息行", stderr_content)

    def test_jsonlines_formatter_and_wide_event(self):
        fake_stderr = io.StringIO()
        logger = setup_logging(
            level="INFO",
            output_format="json",
            stream=fake_stderr,
            nonblocking=False,
        )

        with bind_context(run_id="run_wide_99", command_id="modulate"):
            with wide_event("modulation_completed", logger=logger) as event:
                time.sleep(0.01)
                event.set("payload_length", 32)
                event.set("bit_rate", 325000)

        lines = [line.strip() for line in fake_stderr.getvalue().splitlines() if line.strip()]
        self.assertTrue(len(lines) >= 1)

        record = json.loads(lines[-1])
        self.assertEqual(record["event"], "modulation_completed")
        self.assertEqual(record["outcome"], "success")
        self.assertEqual(record["run_id"], "run_wide_99")
        self.assertEqual(record["command"], "modulate")
        self.assertEqual(record["payload_length"], 32)
        self.assertEqual(record["bit_rate"], 325000)
        self.assertGreater(record["duration_ms"], 0)

    def test_wide_event_exception_capture(self):
        fake_stderr = io.StringIO()
        logger = setup_logging(
            level="INFO",
            output_format="json",
            stream=fake_stderr,
            nonblocking=False,
        )

        with self.assertRaises(ValueError):
            with wide_event("failing_task", logger=logger) as event:
                event.set("step", "init")
                raise ValueError("仿真硬件故障")

        lines = [line.strip() for line in fake_stderr.getvalue().splitlines() if line.strip()]
        self.assertTrue(len(lines) >= 1)

        record = json.loads(lines[-1])
        self.assertEqual(record["event"], "failing_task")
        self.assertEqual(record["outcome"], "error")
        self.assertEqual(record["error"]["type"], "ValueError")
        self.assertIn("仿真硬件故障", record["error"]["message"])

    def test_rotating_file_handler_logging(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "flrc_test.log"
            fake_stderr = io.StringIO()

            logger = setup_logging(
                level="INFO",
                output_format="json",
                file_path=log_file,
                stream=fake_stderr,
                nonblocking=False,
            )

            logger.info("写入文件的测试日志")

            self.assertTrue(log_file.exists())
            content = log_file.read_text(encoding="utf-8")
            self.assertIn("写入文件的测试日志", content)
            entry = json.loads(content.strip().splitlines()[-1])
            self.assertEqual(entry["message"], "写入文件的测试日志")
            self.assertEqual(entry["level"], "info")

            # 显式关闭文件句柄，避免 Windows 上的临时目录删除锁冲突
            shutdown_logging()

    def test_nonblocking_queue_emission(self):
        """测试高并发/异步非阻塞队列日志分发。"""
        fake_stderr = io.StringIO()
        logger = setup_logging(
            level="INFO",
            output_format="text",
            stream=fake_stderr,
            nonblocking=True,
        )

        with bind_context(command_id="rx", sdr_type="hackrf"):
            for i in range(10):
                logger.info("并发非阻塞日志包 #%d", i)

        # 停止并等待队列消费完成
        shutdown_logging()

        content = fake_stderr.getvalue()
        for i in range(10):
            self.assertIn(f"并发非阻塞日志包 #{i}", content)
        self.assertIn("[rx]", content)


    def test_default_file_logging_and_format_separation(self):
        """测试控制台与磁盘文件格式分离 (例如控制台纯文本，文件 JSONL)。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "custom.log"
            fake_stderr = io.StringIO()

            logger = setup_logging(
                level="INFO",
                output_format="text",      # stderr 输出格式
                file_format="json",        # 磁盘文件输出格式
                file_path=log_file,
                stream=fake_stderr,
                nonblocking=False,
            )

            with bind_context(command_id="duplex", run_id="run_sep"):
                logger.info("双路格式分离测试行")

            shutdown_logging()

            # 1. 检查 stderr 为普通人类可读文本
            stderr_out = fake_stderr.getvalue()
            self.assertIn("[duplex:run_sep]", stderr_out)
            self.assertIn("双路格式分离测试行", stderr_out)
            self.assertFalse(stderr_out.strip().startswith("{"))

            # 2. 检查 log_file 为结构化 JSONL
            file_content = log_file.read_text(encoding="utf-8").strip()
            self.assertTrue(file_content.startswith("{"))
            entry = json.loads(file_content)
            self.assertEqual(entry["message"], "双路格式分离测试行")
            self.assertEqual(entry["context"]["command"], "duplex")
            self.assertEqual(entry["context"]["run_id"], "run_sep")

    def test_disable_file_logging(self):
        """测试显式传入 file_path=False 时不创建任何磁盘文件。"""
        fake_stderr = io.StringIO()
        logger = setup_logging(
            level="INFO",
            file_path=False,
            stream=fake_stderr,
            nonblocking=False,
        )
        logger.info("纯内存日志")
        shutdown_logging()
        self.assertIn("纯内存日志", fake_stderr.getvalue())

    def test_log_file_rotation(self):
        """测试文件达到 max_bytes 时自动轮转产生 .1 备份。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "rotating.log"
            fake_stderr = io.StringIO()

            logger = setup_logging(
                level="INFO",
                file_path=log_file,
                max_bytes=200,   # 很小的阈值以快速触发轮转
                backup_count=2,
                stream=fake_stderr,
                nonblocking=False,
            )

            for i in range(20):
                logger.info(f"第 {i} 条长文本日志信息，用于快速填满单文件并触发轮转落盘")

            shutdown_logging()

            backup_file = Path(str(log_file) + ".1")
            self.assertTrue(log_file.exists())
            self.assertTrue(backup_file.exists(), "应生成 .1 备份轮转文件")

    def test_threading_uncaught_exception_capture(self):
        """测试后台子线程未捕获异常自动被抓取至日志。"""
        fake_stderr = io.StringIO()
        logger = setup_logging(
            level="INFO",
            file_path=False,
            stream=fake_stderr,
            nonblocking=False,
            install_guards=True,
        )

        import threading

        def faulty_thread():
            raise RuntimeError("子线程突发通信故障")

        t = threading.Thread(target=faulty_thread, name="RadioWorker")
        t.start()
        t.join()

        shutdown_logging()
        out = fake_stderr.getvalue()
        self.assertIn("CRITICAL", out)
        self.assertIn("RadioWorker", out)
        self.assertIn("子线程突发通信故障", out)


if __name__ == "__main__":
    unittest.main()
