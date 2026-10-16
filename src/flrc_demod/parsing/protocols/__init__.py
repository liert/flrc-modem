"""内置协议注册表；每种设备在独立模块中实现，不进入 DSP/PHY 层。"""

BUILTIN_PARSERS = {
    "dk32s": "flrc_demod.parsing.protocols.dk32s:parse",
    "dk32s-multi": "flrc_demod.parsing.protocols.dk32s:parse_multi",
    "datalink": "flrc_demod.parsing.protocols.datalink:parse",
}
