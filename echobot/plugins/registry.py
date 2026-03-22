"""插件注册表"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import Plugin


class PluginRegistry:
    """插件注册和管理"""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._enabled: set[str] = set()

    def register(self, plugin: Plugin) -> None:
        """注册插件"""
        self._plugins[plugin.name] = plugin

    def enable(self, plugin_name: str) -> None:
        """启用插件"""
        if plugin_name in self._plugins:
            self._enabled.add(plugin_name)

    def disable(self, plugin_name: str) -> None:
        """禁用插件"""
        self._enabled.discard(plugin_name)

    def is_enabled(self, plugin_name: str) -> bool:
        """检查插件是否启用"""
        return plugin_name in self._enabled

    def get_enabled_plugins(self) -> list[Plugin]:
        """获取所有启用的插件"""
        return [self._plugins[name] for name in self._enabled]

    def get_plugin(self, plugin_name: str) -> Plugin | None:
        """获取指定插件"""
        return self._plugins.get(plugin_name)
