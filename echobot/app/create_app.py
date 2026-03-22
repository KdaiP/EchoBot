from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from ..plugins import PluginRegistry
from ..plugins.video_call import VideoCallPlugin
from ..runtime.bootstrap import RuntimeOptions
from .routers import chat, channels, cron, health, heartbeat, roles, sessions, web
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

    # 初始化插件系统（先于 runtime 构建，以便注入工具）
    plugin_registry = PluginRegistry()
    video_plugin = VideoCallPlugin()
    plugin_registry.register(video_plugin)
    video_enabled = os.getenv("ECHOBOT_ENABLE_VIDEO_CALL", "false").lower() == "true"
    if video_enabled:
        plugin_registry.enable("video_call")
        logger.info("VideoCall plugin enabled")

    # 构建包装过的 context_builder，注入插件工具
    def _plugin_context_builder(opts: RuntimeOptions):
        from ..runtime.bootstrap import build_runtime_context
        # 调用原始 builder（默认或用户传入）
        if context_builder is not None:
            ctx = context_builder(opts)
        else:
            ctx = build_runtime_context(opts, load_session_state=False)

        if video_enabled:
            # 包装 tool_registry_factory，追加插件工具
            original_factory = ctx.tool_registry_factory

            def wrapped_factory(session_name: str, scheduled_context: bool):
                registry = original_factory(session_name, scheduled_context)
                if registry is None:
                    return registry
                for tool in video_plugin.get_tool_instances():
                    try:
                        registry.register(tool)
                    except ValueError:
                        pass  # 已注册则跳过
                return registry

            ctx.tool_registry_factory = wrapped_factory
        return ctx

    runtime = AppRuntime(
        runtime_options=options,
        channel_config_path=channel_config_path,
        context_builder=_plugin_context_builder,
        tts_service_builder=tts_service_builder,
        asr_service_builder=asr_service_builder,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        app.state.runtime = runtime
        app.state.plugin_registry = plugin_registry

        # 启动所有启用的插件
        for plugin in plugin_registry.get_enabled_plugins():
            await plugin.on_startup(app, runtime)

        try:
            yield
        finally:
            # 关闭所有插件
            for plugin in plugin_registry.get_enabled_plugins():
                await plugin.on_shutdown()

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

    @app.get("/web/camera", include_in_schema=False)
    async def web_camera() -> FileResponse:
        return FileResponse(WEB_ASSETS_DIR / "camera.html")

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
    app.include_router(chat.router, prefix="/api")
    app.include_router(cron.router, prefix="/api")
    app.include_router(heartbeat.router, prefix="/api")
    app.include_router(roles.router, prefix="/api")
    app.include_router(channels.router, prefix="/api")
    app.include_router(web.router, prefix="/api")

    # 加载启用的插件路由
    for plugin in plugin_registry.get_enabled_plugins():
        for router in plugin.get_routers():
            app.include_router(router, prefix="/api")
            logger.info(f"Loaded router from plugin: {plugin.name}")

    return app
