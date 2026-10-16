from pathlib import Path
import tempfile
import unittest

from flrc_demod.config import DemodConfig, load_config


class ConfigTests(unittest.TestCase):
    def test_cli_overrides_toml_without_erasing_other_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[demod]\nbit_rate=260000.0\nsync_word="b42b2bb4"\n', encoding="utf-8")
            config = load_config(path, {"max_seconds": 0.1, "bit_rate": None})
            self.assertEqual(config.bit_rate, 260000)
            self.assertEqual(config.sync_word.hex(), "b42b2bb4")
            self.assertEqual(config.max_seconds, 0.1)

    def test_invalid_numeric_and_rf_boundaries(self):
        for values in ({"sample_rate": float("nan")}, {"bit_rate": 0},
                       {"overlap_seconds": 0}, {"frequencies": (3e9,)},
                       {"sync_word": b"short"}, {"filter_taps": 100}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                DemodConfig(**values)

    def test_unknown_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            load_config(None, {"typo": 1})
