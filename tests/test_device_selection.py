"""多设备自动跳过失败项、明确选择、歧义及初始化/列表资源清理。"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from flrc_modem.sdr.hackrf import HackRFDevice, HackRFError
from flrc_modem.sdr.hackrf.library import HackRFLibrary
from flrc_modem.transmit_cli import main as transmit_main
from flrc_modem.live_cli import main as live_main
from hackrf_fake import FakeAPI

SERIALS = ("00000000000000000000000000001111", "00000000000000000000000000002222",
           "00000000000000000000000000003333")


class DeviceSelectionTests(unittest.TestCase):
    def test_auto_skips_every_failed_open_until_one_succeeds(self):
        api = FakeAPI(serials=SERIALS, open_codes={0: -5, 1: -1000})
        with HackRFDevice(api) as device:
            self.assertEqual(device.selected_device, {"index": 2, "serial": SERIALS[2]})
            self.assertEqual([call for call in api.calls if isinstance(call, tuple)],
                             [("list_open", 0), ("list_open", 1), ("list_open", 2)])
            self.assertFalse(api._device_lists)
        self.assertEqual(api.calls.count("close"), 1)
        self.assertEqual(api.calls[-1], "release")

    def test_explicit_index_opens_only_target_including_missing_serial(self):
        api = FakeAPI(serials=(SERIALS[0], None))
        with HackRFDevice(api, device_index=1) as device:
            self.assertEqual(device.selected_device, {"index": 1, "serial": None})
        self.assertNotIn(("list_open", 0), api.calls)
        self.assertIn(("list_open", 1), api.calls)

    def test_unique_serial_suffix_and_full_serial_case_insensitive(self):
        serials = (SERIALS[0], "0000000000000000000000000000abcd")
        for requested in ("ABCD", serials[1].upper()):
            api = FakeAPI(serials=serials)
            with self.subTest(serial=requested), HackRFDevice(api, serial=requested) as device:
                self.assertEqual(device.selected_device["index"], 1)
            self.assertNotIn(("list_open", 0), api.calls)

    def test_explicit_unavailable_target_never_falls_back(self):
        for selection in ({"serial": "2222"}, {"device_index": 1}):
            api = FakeAPI(serials=SERIALS, open_codes={1: -5})
            device = HackRFDevice(api, **selection)
            with self.subTest(selection=selection), self.assertRaisesRegex(HackRFError, "不切换到其他设备"):
                device.__enter__()
            self.assertEqual([call for call in api.calls if isinstance(call, tuple)], [("list_open", 1)])
            self.assertIsNone(device.handle.value)
            self.assertFalse(api._device_lists)
            self.assertEqual(api.calls[-1], "release")

    def test_ambiguous_suffix_missing_serial_and_out_of_range_never_open(self):
        for serials, selection, message in (
            (("00001111", "abcd1111"), {"serial": "1111"}, "匹配多台"),
            (SERIALS, {"serial": "ffff"}, "找不到序列号"),
            (SERIALS, {"device_index": 3}, "超出范围"),
        ):
            api = FakeAPI(serials=serials)
            with self.subTest(selection=selection), self.assertRaisesRegex(HackRFError, message):
                HackRFDevice(api, **selection).__enter__()
            self.assertFalse(any(isinstance(call, tuple) for call in api.calls))
            self.assertFalse(api._device_lists)
            self.assertEqual(api.calls[-1], "release")

    def test_all_unavailable_reports_each_attempt_and_releases_library(self):
        api = FakeAPI(serials=SERIALS[:2], open_codes={0: -5, 1: -1000})
        with self.assertRaises(HackRFError) as raised:
            HackRFDevice(api).__enter__()
        self.assertIn("没有可打开", str(raised.exception))
        self.assertIn("index=0", str(raised.exception))
        self.assertIn("index=1", str(raised.exception))
        self.assertIn("-5", str(raised.exception))
        self.assertIn("-1000", str(raised.exception))
        self.assertFalse(api._device_lists)
        self.assertEqual(api.calls[-1], "release")

    def test_no_devices_and_invalid_selection(self):
        api = FakeAPI(serials=())
        with self.assertRaisesRegex(HackRFError, "未发现 HackRF"):
            HackRFDevice(api).__enter__()
        self.assertEqual(api.calls[-1], "release")
        for selection in ({"serial": "1111", "device_index": 0}, {"device_index": -1},
                          {"device_index": True}, {"serial": ""}):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                HackRFDevice(api, **selection)

    def test_info_lists_indices_without_opening_devices(self):
        api = FakeAPI(serials=SERIALS)
        api.path = "fake.dll"
        api.hackrf_library_version = lambda: b"fake"
        api.hackrf_library_release = lambda: b"fake"
        info = HackRFLibrary.info(api)
        self.assertEqual(info["devices"], [{"index": index, "serial": serial} for index, serial in enumerate(SERIALS)])
        self.assertEqual(info["serial_numbers"], list(SERIALS))
        self.assertNotIn("open", api.calls)
        self.assertEqual(api.calls[-1], "release")

    def test_transmit_cli_auto_selection_and_explicit_index_and_serial(self):
        for selection, codes in (([], {0: -5}), (["--device-index", "1"], {}), (["--serial", "2222"], {})):
            api = FakeAPI(serials=SERIALS[:2], open_codes=codes, buffer_size=4096)
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(selection=selection), \
                    patch("flrc_modem.transmission.session.HackRFLibrary", return_value=api), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                result = transmit_main(["--frequency", "2480000000", "--sync", "54455354",
                                        "--payload-hex", "000102030405", *selection])
            self.assertEqual(result, 0, stderr.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())["device"], {"index": 1, "serial": SERIALS[1]})
            self.assertIn("已选择 HackRF：index=1", stderr.getvalue())

    def test_live_cli_auto_selection_uses_same_backend(self):
        api = FakeAPI(serials=SERIALS[:2], open_codes={0: -5}, rx_data=bytes(8000), buffer_size=4096)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("flrc_modem.realtime.session.HackRFLibrary", return_value=api), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            result = live_main(["--duration", "0.002", "--dsp-mode", "thread", "--quiet"])
        self.assertEqual(result, 0, stderr.getvalue())
        # 启动提示占第一行，原有结束统计仍为独立 JSON 行。
        self.assertIn("已选择 HackRF：index=1", stderr.getvalue())
        stats = json.loads(stderr.getvalue().splitlines()[-1])
        self.assertEqual(stats["device"], {"index": 1, "serial": SERIALS[1]})
