"""WebUI 的工程路径不随启动命令的工作目录变化。"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
