"""FastAPI 应用入口：配置中间件、挂载 API 与 WebSocket 路由，并在启动时完成数据库清理。"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import sys
import webbrowser
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.router import api_router, ws_router
from .storage.db import get_db

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(name)s: %(message)s",
)
logger = logging.getLogger("flrc_webui")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动钩子：清理上次未完成的历史运行状态
    db = get_db()
    cleaned = db.mark_lost_processes_on_startup()
    if cleaned > 0:
        logger.info(f"系统启动：已将 {cleaned} 个前次异常中断的任务状态重置为 FAILED。")
    logger.info("FLRC Modem WebUI 后端服务已就绪。")
    yield
    logger.info("FLRC Modem WebUI 后端服务正在优雅停止...")


app = FastAPI(
    title="FLRC Modem WebUI API",
    version="1.0.0",
    description="专业级 FLRC 调制解调、SDR 设备控制与实时数据流可视化后台接口",
    lifespan=lifespan,
)

# 允许跨域 (供前端开发服务器使用)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 REST 与 WebSocket 路由
app.include_router(api_router)
app.include_router(ws_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "app": "flrc_modem_webui", "version": "1.0.0"}


# 如果前端静态构建目录存在，挂载静态文件
FRONTEND_DIST = Path("webui/frontend/dist").resolve()
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")


def main(argv=None) -> int:
    """CLI 入口，支持 flrc webui 或 python -m flrc_modem webui。"""
    parser = argparse.ArgumentParser(
        prog="flrc webui",
        description="启动 FLRC Modem 本地专业 WebUI 工作台",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="监听端口 (默认 8000)")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开系统浏览器")
    args = parser.parse_args(argv)

    import uvicorn

    url = f"http://{args.host}:{args.port}"
    print(f"[*] 正在启动 FLRC Modem 本地 WebUI: {url}", file=sys.stderr)

    if not args.no_browser:
        def open_browser():
            import time
            time.sleep(1.2)
            webbrowser.open(url)

        import threading
        threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run("webui.backend.app.main:app", host=args.host, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
