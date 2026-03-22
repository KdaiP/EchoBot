"""插件基类定义"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI
    from ..app.runtime import AppRuntime


class Plugin(ABC):
    """所有插件的基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称"""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """插件版本"""
        pass

    @abstractmethod
    async def on_startup(self, app: FastAPI, runtime: AppRuntime) -> None:
        """启动时调用"""
        pass

    @abstractmethod
    async def on_shutdown(self) -> None:
        """关闭时调用"""
        pass

    @abstractmethod
    def get_routers(self) -> list[APIRouter]:
        """返回插件的 API 路由"""
        pass

    @abstractmethod
    def get_tools(self) -> list[dict]:
        """返回大模型工具定义"""
        pass
