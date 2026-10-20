"""模拟 UHD C API 的测试桩，用于离线单元测试 USRP 组件，不依赖真实硬件或 DLL。"""
import ctypes as C

from flrc_modem.sdr.usrp.abi import UHDErrorCode, UHDRxMetadataErrorCode


def _set_val(ref, val):
    if hasattr(val, "value"):
        val = val.value
    if hasattr(ref, "_obj"):
        ref._obj.value = val
    elif hasattr(ref, "contents"):
        ref.contents.value = val
    else:
        ref.value = val


def _get_obj(ref):
    if hasattr(ref, "_obj"):
        return ref._obj
    elif hasattr(ref, "contents"):
        return ref.contents
    return ref


class FakeUHDDll:
    def __init__(self, devices=None, rx_samples=b""):
        self.devices = devices or [
            "mgmt_addr=192.168.10.2,type=n3xx,product=n310,serial=3245E26,name=ni-n3xx-3245E26,fpga=HG,claimed=False"
        ]
        self.rx_samples = rx_samples
        self.tx_sent_bytes = bytearray()
        self.calls = []
        self._strings = {}
        self.rx_rate = 2000000.0
        self.tx_rate = 2000000.0
        self.mcr = 125000000.0
        self.rx_freq = 2480000000.0
        self.tx_freq = 2480000000.0
        self.rx_gain = 30.0
        self.tx_gain = 30.0
        self.fail_make = False
        self.rx_stream_cmd = None
        self._rx_offset = 0

    def uhd_get_version_string(self, buf, size):
        self.calls.append("get_version")
        ver = b"4.9.0.0-fake"
        C.memmove(buf, ver, min(len(ver) + 1, size))
        return 0

    def uhd_get_last_error(self, buf, size):
        msg = b"Fake UHD error"
        C.memmove(buf, msg, min(len(msg) + 1, size))
        return 0

    # String vector
    def uhd_string_vector_make(self, h_out):
        self.calls.append("string_vector_make")
        key = id(h_out)
        self._strings[key] = list(self.devices)
        _set_val(h_out, key)
        return 0

    def uhd_string_vector_free(self, h_in):
        self.calls.append("string_vector_free")
        key = _get_obj(h_in).value
        self._strings.pop(key, None)
        return 0

    def uhd_string_vector_size(self, h, size_out):
        _set_val(size_out, len(self.devices))
        return 0

    def uhd_string_vector_at(self, h, idx, buf, size):
        val = self.devices[idx].encode("utf-8")
        C.memmove(buf, val, min(len(val) + 1, size))
        return 0

    # USRP Find & Make
    def uhd_usrp_find(self, args, h_vec_out):
        self.calls.append(("usrp_find", args))
        return 0

    def uhd_usrp_make(self, h_out, args):
        self.calls.append(("usrp_make", args))
        if self.fail_make:
            return UHDErrorCode.INVALID_DEVICE
        _set_val(h_out, 123456)
        return 0

    def uhd_usrp_free(self, h_in):
        self.calls.append("usrp_free")
        _set_val(h_in, 0)
        return 0

    def uhd_usrp_get_pp_string(self, h, buf, size):
        self.calls.append("get_pp_string")
        msg = b"Fake USRP N310 Tree Properties"
        C.memmove(buf, msg, min(len(msg) + 1, size))
        return 0

    def uhd_usrp_get_mboard_name(self, h, idx, buf, size):
        name = b"n310"
        C.memmove(buf, name, min(len(name) + 1, size))
        return 0

    def uhd_usrp_get_rx_num_channels(self, h, size_out):
        _set_val(size_out, 4)
        return 0

    def uhd_usrp_get_tx_num_channels(self, h, size_out):
        _set_val(size_out, 4)
        return 0

    def uhd_usrp_set_master_clock_rate(self, h, rate, mboard):
        r = rate.value if hasattr(rate, "value") else rate
        self.calls.append(("set_master_clock_rate", r))
        self.mcr = r
        return 0

    def uhd_usrp_get_master_clock_rate(self, h, mboard, rate_out):
        _set_val(rate_out, self.mcr)
        return 0

    # Rates & Frequencies
    def uhd_usrp_set_rx_rate(self, h, rate, chan):
        r = rate.value if hasattr(rate, "value") else rate
        c = chan.value if hasattr(chan, "value") else chan
        self.calls.append(("set_rx_rate", r, c))
        self.rx_rate = r
        return 0

    def uhd_usrp_get_rx_rate(self, h, chan, rate_out):
        rate = getattr(self, "fixed_rx_rate", None)
        if rate is None:
            rate = self.rx_rate
        _set_val(rate_out, rate)
        return 0

    def uhd_usrp_set_tx_rate(self, h, rate, chan):
        r = rate.value if hasattr(rate, "value") else rate
        c = chan.value if hasattr(chan, "value") else chan
        self.calls.append(("set_tx_rate", r, c))
        self.tx_rate = r
        return 0

    def uhd_usrp_get_tx_rate(self, h, chan, rate_out):
        _set_val(rate_out, self.tx_rate)
        return 0

    def uhd_usrp_set_rx_freq(self, h, tune_req, chan, tune_res):
        req = _get_obj(tune_req)
        c = chan.value if hasattr(chan, "value") else chan
        self.calls.append(("set_rx_freq", req.target_freq, c))
        self.rx_freq = req.target_freq
        res = _get_obj(tune_res)
        res.actual_rf_freq = self.rx_freq
        return 0

    def uhd_usrp_get_rx_freq(self, h, chan, freq_out):
        _set_val(freq_out, self.rx_freq)
        return 0

    def uhd_usrp_set_tx_freq(self, h, tune_req, chan, tune_res):
        req = _get_obj(tune_req)
        c = chan.value if hasattr(chan, "value") else chan
        self.calls.append(("set_tx_freq", req.target_freq, c))
        self.tx_freq = req.target_freq
        res = _get_obj(tune_res)
        res.actual_rf_freq = self.tx_freq
        return 0

    def uhd_usrp_get_tx_freq(self, h, chan, freq_out):
        _set_val(freq_out, self.tx_freq)
        return 0

    # Gains & Antennas
    def uhd_usrp_set_rx_gain(self, h, gain, chan, name):
        g = gain.value if hasattr(gain, "value") else gain
        c = chan.value if hasattr(chan, "value") else chan
        self.calls.append(("set_rx_gain", g, c))
        self.rx_gain = g
        return 0

    def uhd_usrp_get_rx_gain(self, h, chan, name, gain_out):
        _set_val(gain_out, self.rx_gain)
        return 0

    def uhd_usrp_set_tx_gain(self, h, gain, chan, name):
        g = gain.value if hasattr(gain, "value") else gain
        c = chan.value if hasattr(chan, "value") else chan
        self.calls.append(("set_tx_gain", g, c))
        self.tx_gain = g
        return 0

    def uhd_usrp_get_tx_gain(self, h, chan, name, gain_out):
        _set_val(gain_out, self.tx_gain)
        return 0

    def uhd_usrp_set_rx_bandwidth(self, h, bw, chan):
        b = bw.value if hasattr(bw, "value") else bw
        c = chan.value if hasattr(chan, "value") else chan
        self.calls.append(("set_rx_bw", b, c))
        return 0

    def uhd_usrp_set_tx_bandwidth(self, h, bw, chan):
        b = bw.value if hasattr(bw, "value") else bw
        c = chan.value if hasattr(chan, "value") else chan
        self.calls.append(("set_tx_bw", b, c))
        return 0

    def uhd_usrp_set_rx_antenna(self, h, ant, chan):
        c = chan.value if hasattr(chan, "value") else chan
        self.calls.append(("set_rx_ant", ant, c))
        return 0

    def uhd_usrp_set_tx_antenna(self, h, ant, chan):
        c = chan.value if hasattr(chan, "value") else chan
        self.calls.append(("set_tx_ant", ant, c))
        return 0

    def uhd_usrp_set_clock_source(self, h, src, mboard):
        self.calls.append(("set_clock_src", src, mboard))
        return 0

    def uhd_usrp_set_time_source(self, h, src, mboard):
        self.calls.append(("set_time_src", src, mboard))
        return 0

    def uhd_usrp_set_time_now(self, h, full, frac, mboard):
        self.calls.append(("set_time_now", full, frac, mboard))
        return 0

    def uhd_usrp_get_time_now(self, h, mboard, full_out, frac_out):
        self.calls.append(("get_time_now", mboard))
        _set_val(full_out, 100)
        _set_val(frac_out, 0.5)
        return 0

    def uhd_usrp_set_command_time(self, h, full, frac, mboard):
        f = full.value if hasattr(full, "value") else full
        fr = frac.value if hasattr(frac, "value") else frac
        mb = mboard.value if hasattr(mboard, "value") else mboard
        self.calls.append(("set_command_time", f, fr, mb))
        return 0

    def uhd_usrp_clear_command_time(self, h, mboard):
        mb = mboard.value if hasattr(mboard, "value") else mboard
        self.calls.append(("clear_command_time", mb))
        return 0

    # Streamers
    def uhd_rx_streamer_make(self, h_out):
        _set_val(h_out, 22222)
        return 0

    def uhd_rx_streamer_free(self, h_in):
        _set_val(h_in, 0)
        return 0

    def uhd_usrp_get_rx_stream(self, h_usrp, args, h_streamer):
        req = _get_obj(args)
        self.calls.append(("get_rx_stream", req.cpu_format))
        return 0

    def uhd_rx_streamer_issue_stream_cmd(self, streamer, cmd):
        c = _get_obj(cmd)
        self.calls.append(("issue_stream_cmd", c.stream_mode))
        self.rx_stream_cmd = c.stream_mode
        return 0

    def uhd_rx_streamer_recv(self, streamer, buf_ptrs, samps_per_buff, md, timeout, one_packet, items_recvd_out):
        ptr = buf_ptrs[0]
        remaining = len(self.rx_samples) - self._rx_offset
        if remaining <= 0:
            _set_val(items_recvd_out, 0)
            return 0
        samps_to_give = min(samps_per_buff, remaining // 2)
        bytes_to_give = samps_to_give * 2
        chunk = self.rx_samples[self._rx_offset:self._rx_offset + bytes_to_give]
        C.memmove(ptr, chunk, len(chunk))
        self._rx_offset += bytes_to_give
        _set_val(items_recvd_out, samps_to_give)
        return 0

    def uhd_tx_streamer_make(self, h_out):
        _set_val(h_out, 33333)
        return 0

    def uhd_tx_streamer_free(self, h_in):
        _set_val(h_in, 0)
        return 0

    def uhd_usrp_get_tx_stream(self, h_usrp, args, h_streamer):
        req = _get_obj(args)
        self.calls.append(("get_tx_stream", req.cpu_format))
        return 0

    def uhd_tx_streamer_send(self, streamer, buf_ptrs, samps_per_buff, md, timeout, items_sent_out):
        ptr = buf_ptrs[0]
        bytes_to_read = samps_per_buff * 2
        if bytes_to_read > 0 and ptr:
            buf = (C.c_uint8 * bytes_to_read).from_address(ptr)
            self.tx_sent_bytes.extend(buf)
        _set_val(items_sent_out, samps_per_buff)
        return 0

    # Metadata
    def uhd_rx_metadata_make(self, h_out):
        _set_val(h_out, 44444)
        return 0

    def uhd_rx_metadata_free(self, h_in):
        _set_val(h_in, 0)
        return 0

    def uhd_rx_metadata_error_code(self, md, code_out):
        _set_val(code_out, UHDRxMetadataErrorCode.NONE)
        return 0

    def uhd_rx_metadata_has_time_spec(self, md, result_out):
        _set_val(result_out, True)
        return 0

    def uhd_rx_metadata_time_spec(self, md, full_out, frac_out):
        _set_val(full_out, 100)
        _set_val(frac_out, 0.123456)
        return 0

    def uhd_rx_metadata_start_of_burst(self, md, result_out):
        _set_val(result_out, False)
        return 0

    def uhd_rx_metadata_end_of_burst(self, md, result_out):
        _set_val(result_out, False)
        return 0

    def uhd_tx_metadata_make(self, h_out, has_time, full, frac, start, end):
        _set_val(h_out, 55555)
        return 0

    def uhd_tx_metadata_free(self, h_in):
        _set_val(h_in, 0)
        return 0


class FakeUHDLibrary:
    def __init__(self, devices=None, rx_samples=b""):
        self.path = "fake/uhd.dll"
        self.dll = FakeUHDDll(devices=devices, rx_samples=rx_samples)

    def check(self, code, func_name=""):
        if code != 0:
            raise Exception(f"{func_name}: fake error {code}")

    def get_version(self):
        return "4.9.0.0-fake"

    def info(self, args=""):
        from flrc_modem.sdr.usrp.discovery import parse_device_record
        devices = [parse_device_record(d) for d in self.dll.devices]
        return {
            "library_path": self.path,
            "library_version": "4.9.0.0-fake",
            "devices": devices,
        }
