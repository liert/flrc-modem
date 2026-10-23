"""Source ranges for fields already decoded by the built-in protocol parsers."""


def business_ranges(payload: bytes, app: dict) -> list[dict]:
    """Keep offsets in physical-payload coordinates; bit fields are little endian."""
    fields = app["fields"]
    kind = fields["kind"]
    start = 9 if app["wire_layout"] == "timestamp_prefix" else 5
    size = app["payload_length"]
    spans = []

    def span(path, value, offset, length, **extra):
        if not length:
            return
        absolute = start + offset
        spans.append({"path": "fields." + path, "label": path, "offset": absolute,
                      "length": length, "value": value,
                      "raw_hex": payload[absolute:absolute + length].hex(),
                      "section": "business", **extra})

    def field(key, offset, length, **extra):
        if key in fields:
            span(key, fields[key], offset, length, **extra)

    def array(key, offset, width, **extra):
        values = fields.get(key, [])
        if not values:
            return
        field(key, offset, (len(values) * width + 7) // 8,
              bit_offset=0, bit_length=len(values) * width, **extra)
        for index, value in enumerate(values):
            bit = index * width
            span(f"{key}.{index}", value, offset + bit // 8, (bit % 8 + width + 7) // 8,
                 bit_offset=bit % 8, bit_length=width, **extra)

    def channels(offset):
        array("channels_raw", offset, 11)
        array("channels_output", offset, 11, derived=True)

    # Classification/query names come from command and envelope, not a business byte.
    field("kind", -1, 1, derived=True)
    field("key", -1, 1, derived=True)
    if kind == "pairing_handshake":
        field("stage", -1, 1, derived=True)
    if kind in ("pairing_offer", "pairing_ack"):
        field("filter_address", 0, 4)
        array("channel_indices", 4, 8)
    elif kind == "rc_channels":
        offset = 2 if fields["rc_mode"] == "multi" else 0
        field("target_device_mask", 0, 1)
        field("device_number", 1, 1)
        channels(offset)
        field("data_batch_id", offset + 22, 1)
        field("stream_data_hex", offset + 23, max(0, size - offset - 23))
    elif kind == "extended_rc_channels":
        field("prefix_hex", 0, 4)
        channels(4)
    elif kind == "wireless_trainer":
        field("enable", 0, 1)
        channels(1)
    elif kind == "telemetry":
        offset = 4 if "sync_time_sec" in fields else 0
        tail = 2 if "voltage_raw" in fields else 0
        field("sync_time_sec", 0, 4)
        field("data_batch_id", offset, 1)
        field("stream_data_hex", offset + 1, size - offset - 1 - tail)
        field("voltage_raw", size - 2, 2)
    elif kind == "time_correction":
        field("correction_ticks", 0, 4)
        field("correction_sec", 0, 4, derived=True)
    elif kind == "transfer_delay":
        field("transfer_delay_sec", 0, 4)
    elif kind == "mode_sync":
        field("mode", 0, 1)
        field("sync_count", 1, 1)
    elif kind == "failsafe":
        field("mode", 0, 1)
        field("channel_mask", 1, 2)
        array("channel_enabled", 1, 1)
        channels(3)
    elif kind == "voltage_calibration":
        field("input_voltage_mv", 0, 4)
    elif kind == "pwm_mapping":
        array("channel_mapping", 0, 5)
        field("unused_tail_hex", 6, size - 6)
    elif kind == "firmware_version":
        field("version_hex", 0, 4)
    elif kind == "channel_table":
        array("channel_indices", 0, 8)
    elif kind in ("config_set", "config_value"):
        for key in fields:
            if key != "kind":
                field(key, 0, 1, **({"derived": True} if key == "baud_rate" else {}))
    elif kind == "port_function":
        for offset, key in enumerate(("operation", "data1_function", "data2_function")):
            field(key, offset, 1)
    elif kind == "notification":
        field("value", 0, 1)
    elif kind in ("multi_air_pairing_offer", "multi_air_pairing_ack"):
        field("stage", 0, 1)
        field("device_number", 1, 1)
        field("rc_address", 2, 4)
        field("receiver_address", 6, 4)
        field("filter_address", 10, 4)
        array("channel_indices", 14, 8)
    elif kind == "multi_air_sync_config":
        field("magic_hex", 0, 4)
        field("index", 4, 1)
        array("params_u32", 5, 32)
        field("filter_address", 17, 4)
        array("channel_indices", 21, 8)
    elif kind == "video_config":
        array("parameters_u32", 0, 32)
    elif kind == "searchlight_config":
        for offset, key in enumerate(("enable", "control_channel", "brightness_level")):
            field(key, offset, 1)
    elif kind == "busy_status":
        field("is_busy", 0, 1)
    elif kind == "switch_master_slave":
        field("vice_pairing", 0, 1)
    elif kind == "ack":
        field("status", 0, 1)
    elif kind == "unknown":
        field("raw_hex", 0, size)
    return spans
