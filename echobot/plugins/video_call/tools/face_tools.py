"""视频通话插件 - 人脸绑定工具（BaseTool 实现，可注册进 ToolRegistry）"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ....tools.base import BaseTool, ToolOutput

if TYPE_CHECKING:
    from ..services import VisionProcessingService


class BindFaceToNameTool(BaseTool):
    """
    将当前画面中检测到的人脸与指定姓名绑定。
    由 InsightFace 提取 512 维特征向量后写入人脸 Map。
    """
    name = "bind_face_to_name"
    description = (
        "将当前摄像头画面中检测到的人脸与指定姓名绑定。"
        "当用户说'这是XXX'、'把我绑定为XXX'、'记住我叫XXX'、'我叫XXX'时调用此工具。"
        "绑定后，后续每次检测到该人脸都会自动识别为该姓名。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "person_name": {
                "type": "string",
                "description": "要绑定的人物姓名",
            },
        },
        "required": ["person_name"],
        "additionalProperties": False,
    }

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        person_name = str(arguments.get("person_name", "")).strip()
        if not person_name:
            raise ValueError("person_name 不能为空")

        import asyncio
        face_service = self._plugin.vision_service.face_recognition_service
        latest_frame = getattr(self._plugin, "_latest_frame", None)

        if not latest_frame:
            return {"ok": False, "message": "没有摄像头画面，请先开启摄像头"}

        # 1. 提取特征并写入内存
        ok = await asyncio.to_thread(
            face_service.add_known_face_from_frame, person_name, latest_frame
        )
        if not ok:
            return {
                "ok": False,
                "message": f"未能在当前画面中检测到人脸，请正对摄像头后重试",
            }

        # 2. 同步写入 face_interceptor 持久化（JSON 文件）
        face_interceptor = getattr(self._plugin, "face_interceptor", None)
        if face_interceptor is not None:
            vec = face_service._known.get(person_name)
            if vec is not None:
                await asyncio.to_thread(
                    face_interceptor.add_or_update_feature,
                    person_name,
                    vec.tolist(),
                )

        return {
            "ok": True,
            "message": f"已成功绑定：{person_name}，后续检测到该人脸时将自动识别",
        }


class ListKnownFacesTool(BaseTool):
    """列出所有已绑定姓名的人脸"""
    name = "list_known_faces"
    description = (
        "列出所有已通过人脸识别绑定姓名的人物。"
        "当用户问'你认识哪些人'、'你记住了谁'时调用。"
    )
    parameters = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        face_service = self._plugin.vision_service.face_recognition_service
        names = face_service.list_known_faces()
        return {
            "faces": names,
            "count": len(names),
            "message": f"已记住 {len(names)} 个人：{', '.join(names)}" if names else "还没有记住任何人脸",
        }


class ForgetFaceTool(BaseTool):
    """删除某个人脸绑定"""
    name = "forget_face"
    description = (
        "删除某个人的人脸绑定记录。"
        "当用户说'忘掉XXX'、'删除XXX的人脸记录'时调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "person_name": {
                "type": "string",
                "description": "要删除的人物姓名",
            },
        },
        "required": ["person_name"],
        "additionalProperties": False,
    }

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        person_name = str(arguments.get("person_name", "")).strip()
        if not person_name:
            raise ValueError("person_name 不能为空")
        face_service = self._plugin.vision_service.face_recognition_service
        ok = face_service.remove_known_face(person_name)
        if ok:
            return {"ok": True, "message": f"已删除 {person_name} 的人脸绑定"}
        else:
            return {"ok": False, "message": f"未找到 {person_name} 的人脸记录"}
