import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from flrc_demod.cli import main, make_argument_parser


class CLITests(unittest.TestCase):
    def test_protocol_and_external_parser_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            make_argument_parser().parse_args(["--iq", "x.iq", "--protocol", "dk32s", "--parser", "x:y"])
        self.assertEqual(error.exception.code, 2)

    def test_noise_only_input_emits_no_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zero.iq"
            path.write_bytes(b"\0" * 20000)
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["--iq", str(path), "--jsonl"])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(json.loads(stderr.getvalue())["decoded_frames"], 0)

    def test_input_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.iq"
            path.write_bytes(b"\0" * 200)
            with contextlib.redirect_stderr(io.StringIO()):
                result = main(["--iq", str(path), "--output", str(path)])
            self.assertEqual(result, 2)
            self.assertEqual(path.stat().st_size, 200)
