"""视频通话插件 - 工具调用处理器"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..interceptors import FaceFeatureInterceptor
    from ..services import VisionProcessingService


class VisionToolHandler:
    """视觉工具调用处理器"""

    def __init__(
        self,
        face_interceptor: "FaceFeatureInterceptor",
        vision_service: "VisionProcessingService | None" = None,
        plugin: Any = None,
    ) -> None:
        self.face_interceptor = face_interceptor
        self.vision_service = vision_service
        self.plugin = plugin  # 用于访问 _latest_frame

    async def handle_tool_call(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        """处理工具调用"""
        if tool_name == "bind_face_to_name":
            return await self._handle_bind_face(tool_input)
        elif tool_name == "list_known_faces":
            return await self._handle_list_faces(tool_input)
        elif tool_name == "forget_face":
            return await self._handle_forget_face(tool_input)
        # 兼容旧工具名
        elif tool_name == "add_face_feature":
            return await self._handle_bind_face({"person_name": tool_input.get("person_name", "")})
        elif tool_name == "query_person_by_face":
            return {"status": "deprecated", "message": "请使用 list_known_faces"}
        else:
            return {"error": f"Unknown tool: {tool_name}"}

    async def _handle_bind_face(
        self, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        """从最新帧提取人脸向量并绑定姓名"""
        person_name = str(tool_input.get("person_name", "")).strip()
        if not person_name:
            return {"error": "person_name 不能为空"}

        # 从最新帧提取真实向量
        face_service = None
        if self.vision_service:
            face_service = self.vision_service.face_recognition_service

        latest_frame = getattr(self.plugin, "_latest_frame", None) if self.plugin else None

        if face_service and latest_frame:
            ok = await __import__('asyncio').to_thread(
                face_service.add_known_face_from_frame, person_name, latest_frame
            )
            if ok:
                return {
                    "status": "success",
                    "message": f"已成功绑定：{person_name}，后续检测到该人脸时将自动识别",
                }
            else:
                return {
                    "status": "warning",
                    "message": f"未能在当前画面中检测到人脸，已记录姓名 {person_name}，请正对摄像头后重试",
                }
        else:
            return {
                "status": "warning",
                "message": f"已记录姓名 {person_name}，待摄像头连接后自动绑定",
            }

    async def _handle_list_faces(
        self, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        """列出已知人脸"""
        if not self.vision_service:
            return {"faces": []}
        names = self.vision_service.face_recognition_service.list_known_faces()
        return {
            "faces": names,
            "count": len(names),
            "message": f"已记住 {len(names)} 个人：{', '.join(names)}" if names else "还没有记住任何人脸",
        }

    async def _handle_forget_face(
        self, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        """删除人脸绑定"""
        person_name = str(tool_input.get("person_name", "")).strip()
        if not person_name:
            return {"error": "person_name 不能为空"}
        if not self.vision_service:
            return {"error": "视觉服务未启动"}
        ok = self.vision_service.face_recognition_service.remove_known_face(person_name)
        if ok:
            return {"status": "success", "message": f"已删除 {person_name} 的人脸绑定"}
        else:
            return {"status": "not_found", "message": f"未找到 {person_name} 的人脸记录"}
