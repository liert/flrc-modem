"""统一入口的路由、参数所有权，以及安装后命令与模块入口的一致性。"""
from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from flrc_modem.entrypoint import COMMANDS, main


class EntrypointTests(unittest.TestCase):
    def test_subcommands_and_default_demod_preserve_caller_arguments(self):
        for command, module in COMMANDS.items():
            args = [command, "--help"]
            with self.subTest(command=command), patch(f"{module}.main", return_value=7) as target:
                self.assertEqual(main(args), 7)
                target.assert_called_once_with(["--help"])
                self.assertEqual(args, [command, "--help"])
        with patch("flrc_modem.cli.main", return_value=3) as target:
            self.assertEqual(main(["--iq", "capture.iq"]), 3)
            target.assert_called_once_with(["--iq", "capture.iq"])

    def test_overview_and_version(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([]), 0)
        for command in COMMANDS:
            self.assertIn(command, output.getvalue())
        with redirect_stdout(output), self.assertRaises(SystemExit) as result:
            main(["--version"])
        self.assertEqual(result.exception.code, 0)
        self.assertIn("flrc-modem 0.1.0", output.getvalue())

    def test_module_and_installed_console_entrypoints(self):
        script_dir = Path(sys.executable).parent
        console = script_dir / ("flrc.exe" if sys.platform == "win32" else "flrc")
        self.assertTrue(console.is_file(), "请先在虚拟环境中执行 pip install -e .")
        commands = [[sys.executable, "-m", "flrc_modem", "--version"],
                    [str(console), "--version"],
                    [str(console), "transmit", "--help"],
                    [sys.executable, "-m", "flrc_modem", "hackrf", "--help"]]
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(command, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                self.assertTrue(result.stdout)
