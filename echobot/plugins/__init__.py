"""EchoBot 插件系统"""

from .base import Plugin
from .registry import PluginRegistry

__all__ = ["Plugin", "PluginRegistry"]
