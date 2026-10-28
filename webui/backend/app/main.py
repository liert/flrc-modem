"""FastAPI 应用入口：配置中间件、挂载 API 与 WebSocket 路由，并在启动时完成数据库清理。"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import sys
import webbrowser
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .spectrum.files import router as spectrum_files_router

from .api.router import api_router, ws_router
from .storage.db import get_db
from .jobs.manager import get_job_manager
from .paths import PROJECT_ROOT
from .artifacts.cleanup import prune_overwritten_runs
from .diagnostics import install_service_diagnostics

# 统一日志初始化
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from flrc_modem.log import (
    bind_context,
    get_default_log_dir,
    get_logger,
    setup_logging,
    shutdown_logging,
    wide_event,
)

setup_logging(
    file_path=get_default_log_dir() / "webui.log",
    extra_loggers=["flrc_webui", "webui"],
)
logger = get_logger("flrc_webui")


class WideEventHTTPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        path = request.url.path
        if path.startswith("/api/v1/ws") or path in ("/health", "/favicon.ico"):
            with bind_context(request_id=req_id):
                return await call_next(request)

        with bind_context(request_id=req_id):
            with wide_event("http_request", logger=logger) as event:
                event.update(
                    method=request.method,
                    path=path,
                    client_ip=request.client.host if request.client else None,
                )
                try:
                    response: Response = await call_next(request)
                    event.set("status_code", response.status_code)
                    event.set("outcome", "success" if response.status_code < 400 else "error")
                    response.headers["x-request-id"] = req_id
                    return response
                except Exception as exc:
                    event.set("status_code", 500)
                    event.record_error(exc)
                    raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .events.hub import get_event_hub
    get_event_hub().start()
    install_service_diagnostics()
    # 启动钩子：清理上次未完成的历史运行状态
    db = get_db()
    cleaned = db.mark_lost_processes_on_startup()
    if cleaned > 0:
        logger.info(f"系统启动：已将 {cleaned} 个前次异常中断的任务状态重置为 FAILED。")
    try:
        pruned = await asyncio.to_thread(prune_overwritten_runs, db)
        if pruned:
            logger.info("系统启动：已清理 %s 个结果被覆盖的旧解调任务。", len(pruned))
    except Exception:
        logger.exception("系统启动：清理被覆盖的任务失败。")
    # Load/compile DSP kernels before the API can enable a live IQ consumer.
    # Doing this for the first time inside the demod worker fills its raw queue.
    from flrc_modem.phy.fec import warm_up
    try:
        accelerated = await asyncio.to_thread(warm_up)
        logger.info("实时解调预热完成，Numba 加速=%s", accelerated)
    except Exception:
        # Other workbenches must remain available if optional acceleration fails.
        logger.warning("实时解调预热失败；开启解调时将报告具体错误", exc_info=True)
    logger.info("FLRC Modem WebUI 后端服务已就绪。")
    from .spectrum.manager import get_spectrum_manager
    get_spectrum_manager().start_watchdog()
    try:
        yield
    finally:
        logger.info("FLRC Modem WebUI 后端服务正在优雅停止...")
        try:
            from .spectrum.manager import get_spectrum_manager
            await get_spectrum_manager().shutdown()
        except Exception as e:
            logger.warning("停止频谱管理器告警: %s", e)
        try:
            await get_job_manager().shutdown()
        finally:
            try:
                await get_event_hub().shutdown()
            finally:
                shutdown_logging()


app = FastAPI(
    title="FLRC Modem WebUI API",
    version="0.2.0",
    description="专业级 FLRC 调制解调、SDR 设备控制与实时数据流可视化后台接口",
    lifespan=lifespan,
)

# 允许跨域 (供前端开发服务器使用)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 REST 与 WebSocket 路由
app.add_middleware(WideEventHTTPMiddleware)
app.include_router(api_router)
app.include_router(spectrum_files_router)
app.include_router(ws_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "app": "flrc_modem_webui", "version": "0.2.0"}


# 如果前端静态构建目录存在，挂载静态文件
FRONTEND_DIST = PROJECT_ROOT / "webui/frontend/dist"
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
