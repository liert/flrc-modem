"""设置 FLRC_TEST_IQ 后使用外部真实采集文件，仓库不复制大体积 IQ。"""
import json
import os
from pathlib import Path
import unittest

from flrc_modem.config import DemodConfig
from flrc_modem.pipeline import DecodeStats, iter_frames


@unittest.skipUnless(os.environ.get("FLRC_TEST_IQ"), "未设置外部 IQ 路径 FLRC_TEST_IQ")
class CapturedIQTests(unittest.TestCase):
    def test_real_packet_across_chunk_boundary(self):
        config = DemodConfig(max_seconds=0.01, chunk_seconds=0.002)
        stats = DecodeStats()
        frames = list(iter_frames(Path(os.environ["FLRC_TEST_IQ"]), config, stats))
        fixture = json.loads((Path(__file__).parent / "fixtures/dk32s_packet.json").read_text())
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].packet.payload.hex(), fixture["payload_hex"])
        self.assertAlmostEqual(frames[0].time_s, 0.00154, delta=0.00005)
