"""内置协议注册表；每种设备在独立模块中实现，不进入 DSP/PHY 层。"""

BUILTIN_PROTOCOLS = {
    "dk32s": {
        "title": "DK32S 遥控协议",
        "description": "普通遥控布局；支持带时间戳的地对空封装与空对地紧凑封装。",
        "parser": "flrc_modem.parsing.protocols.dk32s:parse",
        "payload_parser": "flrc_modem.parsing.protocols.dk32s:parse_payload",
    },
    "dk32s-multi": {
        "title": "DK32S 一控多机",
        "description": "显式使用目标设备位图及设备编号，不按载荷长度猜测模式。",
        "parser": "flrc_modem.parsing.protocols.dk32s:parse_multi",
        "payload_parser": "flrc_modem.parsing.protocols.dk32s:parse_multi_payload",
    },
    "datalink": {
        "title": "DataLink 数传协议",
        "description": "无线紧凑帧；透传内部数据保留原始字节。",
        "parser": "flrc_modem.parsing.protocols.datalink:parse",
        "payload_parser": "flrc_modem.parsing.protocols.datalink:parse_payload",
    },
}

BUILTIN_PARSERS = {name: entry["parser"] for name, entry in BUILTIN_PROTOCOLS.items()}
