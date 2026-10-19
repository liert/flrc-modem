import sys

# 保留原来的解调命令格式，同时提供独立调制入口。
if len(sys.argv) > 1 and sys.argv[1] == "transmit":
    from .transmit_cli import main
    del sys.argv[1]
elif len(sys.argv) > 1 and sys.argv[1] == "live":
    from .live_cli import main
    del sys.argv[1]
elif len(sys.argv) > 1 and sys.argv[1] == "hackrf":
    from .sdr.hackrf.cli import main
    del sys.argv[1]
elif len(sys.argv) > 1 and sys.argv[1] == "modulate":
    from .modulate_cli import main
    del sys.argv[1]
else:
    from .cli import main

raise SystemExit(main())
