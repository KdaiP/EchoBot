"""视频通话插件 - 插件入口"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter

from ...plugins.base import Plugin
from .config import VideoCallConfig
from .interceptors import FaceFeatureInterceptor
from .routers import router as video_router
from .services import VisionProcessingService
from .tools import VISION_TOOLS
from .tools.handlers import VisionToolHandler
from .vision_provider import VisionContextProvider

if TYPE_CHECKING:
    from fastapi import FastAPI
    from ...app.runtime import AppRuntime


class VideoCallPlugin(Plugin):
    """视频通话插件"""

    def __init__(self) -> None:
        self.config = VideoCallConfig.from_env()
        self.vision_service: VisionProcessingService | None = None
        self.face_interceptor: FaceFeatureInterceptor | None = None
        self.tool_handler: VisionToolHandler | None = None
        self.vision_provider: VisionContextProvider | None = None
        self.face_recognition_store: dict[str, str] = {}  # person_name -> description

    @property
    def name(self) -> str:
        return "video_call"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def on_startup(self, app: FastAPI, runtime: AppRuntime) -> None:
        """启动时调用"""
        from loguru import logger

        logger.info("Initializing VideoCallPlugin...")

        # 初始化服务
        self.vision_service = VisionProcessingService()
        self.face_interceptor = FaceFeatureInterceptor()
        self.tool_handler = VisionToolHandler(
            self.face_interceptor,
            vision_service=self.vision_service,
            plugin=self,
        )
        self.vision_provider = VisionContextProvider(max_frames=80)

        # 将插件实例保存到应用状态
        app.state.video_call_plugin = self

        # 把视觉提供器注入到 coordinator，自动为每次对话注入视觉上下文
        try:
            coordinator = runtime.context.coordinator
            coordinator.set_vision_context_provider(self.vision_provider)
            logger.info("VisionContextProvider injected into coordinator")
        except Exception as e:
            logger.warning(f"Failed to inject vision provider: {e}")

        # 启动时预热所有模型（下载 + 加载），不等第一帧
        try:
            await self.vision_service.initialize()
            logger.info("VideoCallPlugin initialized successfully")
        except Exception as e:
            logger.warning(f"VideoCallPlugin model preload warning: {e}")

    async def on_shutdown(self) -> None:
        """关闭时调用"""
        from loguru import logger

        logger.info("Shutting down VideoCallPlugin...")

        if self.vision_service:
            await self.vision_service.close()

        if self.vision_provider:
            self.vision_provider.clear()

        logger.info("VideoCallPlugin shut down successfully")

    def get_routers(self) -> list[APIRouter]:
        """返回插件的 API 路由"""
        return [video_router]

    def get_tools(self) -> list[dict]:
        """返回大模型工具定义（旧格式，兼容用）"""
        from .tools import VISION_TOOLS
        return VISION_TOOLS

    def get_tool_instances(self) -> list:
        """返回 BaseTool 实例列表，供 ToolRegistry 注册"""
        from .tools.face_tools import BindFaceToNameTool, ListKnownFacesTool, ForgetFaceTool
        return [
            BindFaceToNameTool(self),
            ListKnownFacesTool(self),
            ForgetFaceTool(self),
        ]

