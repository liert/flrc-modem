"""共享 SDR 后端选择与旧命令兼容转发。"""


def resolve_sdr_backend(args) -> str:
    if args.sdr:
        return args.sdr.lower()
    if args.serial is not None or args.device_index is not None or args.amp or args.bias_tee:
        return "hackrf"
    return "usrp"


def forward_iq_command(args, backend: str, *, library_factory=None) -> int:
    """保留旧入口参数和默认值，将业务执行交给统一 rx/tx CLI。"""
    from .. import rx_cli, tx_cli
    command = rx_cli if args.command == "rx" else tx_cli
    argv = ["--sdr", backend]
    for name, value in vars(args).items():
        if name == "command" or value is None or value is False:
            continue
        name = "gain" if name == "tx_gain" else name
        argv.append("--" + name.replace("_", "-"))
        if value is not True:
            argv.append(str(value))
    return command.main(argv, library_factory=library_factory)
