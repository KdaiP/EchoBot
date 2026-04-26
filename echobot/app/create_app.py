from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..runtime.bootstrap import RuntimeOptions
from .routers import attachments, chat, channels, cron, health, heartbeat, roles, sessions, web
from .runtime import ASRServiceBuilder, AppRuntime, RuntimeContextBuilder, TTSServiceBuilder


WEB_ASSETS_DIR = Path(__file__).with_name("web")


def create_app(
    *,
    runtime_options: RuntimeOptions | None = None,
    channel_config_path: str | Path = ".echobot/channels.json",
    context_builder: RuntimeContextBuilder | None = None,
    tts_service_builder: TTSServiceBuilder | None = None,
    asr_service_builder: ASRServiceBuilder | None = None,
) -> FastAPI:
    options = runtime_options or RuntimeOptions()
    runtime = AppRuntime(
        runtime_options=options,
        channel_config_path=channel_config_path,
        context_builder=context_builder,
        tts_service_builder=tts_service_builder,
        asr_service_builder=asr_service_builder,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title="EchoBot API",
        description="Runtime API for EchoBot daemon and future web console.",
        lifespan=lifespan,
    )

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "name": "EchoBot API",
            "docs": "/docs",
        }

    @app.get("/web", include_in_schema=False)
    async def web_console() -> FileResponse:
        return FileResponse(WEB_ASSETS_DIR / "index.html")

    @app.get("/desktop", include_in_schema=False)
    async def desktop_console() -> FileResponse:
        return FileResponse(WEB_ASSETS_DIR / "desktop.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(
            WEB_ASSETS_DIR / "favicon.svg",
            media_type="image/svg+xml",
        )

    app.mount(
        "/web/assets",
        StaticFiles(directory=WEB_ASSETS_DIR),
        name="web-assets",
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(sessions.router, prefix="/api")
    app.include_router(attachments.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(cron.router, prefix="/api")
    app.include_router(heartbeat.router, prefix="/api")
    app.include_router(roles.router, prefix="/api")
    app.include_router(channels.router, prefix="/api")
    app.include_router(web.router, prefix="/api")
    return app
