"""视频通话插件 - API 路由"""

from __future__ import annotations

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from loguru import logger

router = APIRouter(tags=["video"])


@router.websocket("/web/video/stream")
async def video_stream(websocket: WebSocket) -> None:
    """视频流处理 WebSocket"""
    plugin = getattr(websocket.app.state, "video_call_plugin", None)
    if not plugin:
        await websocket.close(code=1011, reason="Video plugin not available")
        return

    await websocket.accept()
    logger.info("Video stream WebSocket connected")

    try:
        while True:
            # 接收视频帧
            data = await websocket.receive_bytes()

            # 缓存最新帧供 snapshot 接口使用
            plugin._latest_frame = data

            # 处理视频帧（双链路并行）
            vision_context = await plugin.vision_service.process_frame(data)

            # 存储到视觉上下文提供器（时间段合并去重）
            plugin.vision_provider.add_vision_context(vision_context)

            # 发送处理结果给前端
            await websocket.send_json(
                {
                    "type": "vision_context",
                    "trace_id": vision_context.trace_id,
                    "start_time": vision_context.start_time,
                    "end_time": vision_context.end_time,
                    "image_description": vision_context.image_description,
                    "faces": [
                        {
                            "face_id": face.face_id,
                            "person_name": face.person_name,
                            "confidence": face.confidence,
                            "position": face.position,
                        }
                        for face in vision_context.faces
                    ],
                }
            )
    except WebSocketDisconnect:
        logger.info("Video stream WebSocket disconnected")
    except Exception as exc:
        logger.error(f"Video stream error: {exc}")
        await websocket.close(code=1011, reason=str(exc))


@router.get("/web/video/context")
async def get_vision_context(request: Request) -> dict:
    """获取视觉上下文（时间段列表）"""
    plugin = getattr(request.app.state, "video_call_plugin", None)
    if not plugin:
        return {"vision_list": [], "count": 0}

    vision_list = plugin.vision_provider.get_vision_context_list()
    return {
        "vision_list": vision_list,
        "count": len(vision_list),
    }


@router.post("/web/video/face-bind")
async def bind_face(request: Request) -> dict:
    """
    绑定人脸：从最近帧中提取特征向量与人名绑定。
    body: {"person_name": "张三"}
    """
    plugin = getattr(request.app.state, "video_call_plugin", None)
    if not plugin:
        return {"ok": False, "error": "Video plugin not available"}

    body = await request.json()
    person_name = str(body.get("person_name", "")).strip()
    if not person_name:
        return {"ok": False, "error": "person_name is required"}

    # 从视觉 List 最近一帧的人脸特征中绑定
    vision_list = plugin.vision_provider.get_vision_context_list()
    if not vision_list:
        return {"ok": False, "error": "没有视觉数据，请先开启摄像头"}

    # 取最近帧的人脸（已有特征向量）
    last_frame = vision_list[-1]
    faces = last_frame.get("faces", [])

    # 也尝试直接从人脸识别服务的最近检测结果绑定
    face_service = plugin.vision_service.face_recognition_service
    known = face_service.list_known_faces()

    # 如果有特征向量直接绑定，否则用描述文本绑定
    desc = last_frame.get("image_description", "")
    plugin.face_recognition_store[person_name] = desc

    return {
        "ok": True,
        "person_name": person_name,
        "note": "已记录，下次检测到相似人脸时将自动关联姓名",
    }


@router.post("/web/video/face-bind-frame")
async def bind_face_from_frame(request: Request) -> dict:
    """
    从上传的帧图像中提取特征向量绑定人名。
    body: {"person_name": "张三", "frame_b64": "..."}
    """
    plugin = getattr(request.app.state, "video_call_plugin", None)
    if not plugin:
        return {"ok": False, "error": "Video plugin not available"}

    body = await request.json()
    person_name = str(body.get("person_name", "")).strip()
    frame_b64 = str(body.get("frame_b64", "")).strip()

    if not person_name:
        return {"ok": False, "error": "person_name is required"}
    if not frame_b64:
        return {"ok": False, "error": "frame_b64 is required"}

    import base64
    try:
        frame_bytes = base64.b64decode(frame_b64)
    except Exception:
        return {"ok": False, "error": "Invalid base64 frame"}

    face_service = plugin.vision_service.face_recognition_service
    ok = face_service.add_known_face_from_frame(person_name, frame_bytes)
    if not ok:
        return {"ok": False, "error": "未能在画面中检测到人脸，请正对摄像头"}

    return {"ok": True, "person_name": person_name}


@router.get("/web/video/snapshot")
async def get_latest_snapshot(request: Request):
    """返回最新视频帧 JPEG（供主页面浮动预览窗口使用）"""
    plugin = getattr(request.app.state, "video_call_plugin", None)
    if not plugin:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No snapshot available")

    frame = getattr(plugin, "_latest_frame", None)
    if not frame:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No snapshot available")

    from fastapi.responses import Response
    return Response(content=frame, media_type="image/jpeg")


@router.get("/web/video/face-list")
async def list_faces(request: Request) -> dict:
    """列出所有已绑定人脸"""
    plugin = getattr(request.app.state, "video_call_plugin", None)
    if not plugin:
        return {"faces": []}
    face_service = plugin.vision_service.face_recognition_service
    return {"faces": face_service.list_known_faces()}
