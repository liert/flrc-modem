import tempfile
from pathlib import Path
import unittest
import numpy as np

from flrc_demod.config import DemodConfig
from flrc_demod.io.iq import decode_samples, iter_iq_chunks


class IQTests(unittest.TestCase):
    def test_signed_and_unsigned_normalization(self):
        signed = decode_samples(bytes((128, 0, 127, 64)), "cs8")
        unsigned = decode_samples(bytes((0, 128, 255, 192)), "cu8")
        np.testing.assert_array_equal(signed, unsigned)
        np.testing.assert_array_equal(signed, np.array([-1 + 0j, 127 / 128 + 0.5j], np.complex64))

    def test_float32_format(self):
        raw = np.array([1.0, -2.0, 0.5, 0.25], dtype="<f4").tobytes()
        np.testing.assert_array_equal(decode_samples(raw, "cf32"), [1 - 2j, 0.5 + 0.25j])

    def test_partial_sample_and_nonfinite_float_are_rejected(self):
        with self.assertRaises(ValueError):
            decode_samples(b"\x00", "cs8")
        with self.assertRaises(ValueError):
            decode_samples(np.array([float("nan"), 1], dtype="<f4").tobytes(), "cf32")

    def test_overlap_and_absolute_window_times(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.iq"
            path.write_bytes(bytes(range(16)))
            config = DemodConfig(chunk_seconds=3 / 10_000_000)
            chunks = list(iter_iq_chunks(path, config))
            self.assertEqual([len(iq) for _, iq in chunks], [8, 5, 2])
            self.assertAlmostEqual(chunks[1][0], 3 / 10_000_000)
            np.testing.assert_array_equal(chunks[0][1][3:], chunks[1][1])

    def test_empty_or_incomplete_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.iq"
            for content in (b"", b"\x00"):
                path.write_bytes(content)
                with self.assertRaises(ValueError):
                    list(iter_iq_chunks(path, DemodConfig()))
