"""视频通话插件 - 视觉处理服务（双链路并行 + trace_id 追踪）"""

from __future__ import annotations

import asyncio
import os
import time
from itertools import count
from typing import Optional

from ..models import Face, TimelineEvent, VisionContext
from .face_recognition import FaceRecognitionService
from .image_description import ImageDescriptionService


# 全局有序计数器，保证 trace_id 单调递增
_trace_counter = count(1)


def _make_trace_id(ts: float) -> str:
    """生成有序 trace_id：时间戳_序号"""
    seq = next(_trace_counter)
    return f"{ts:.3f}_{seq:06d}"


class VisionProcessingService:
    """双链路并行：图像描述链路 + 人脸识别链路，通过 trace_id 拼接"""

    def __init__(self) -> None:
        self.image_desc_service = ImageDescriptionService()
        self.face_recognition_service = FaceRecognitionService(
            feature_match_threshold=float(
                os.environ.get("ECHOBOT_VIDEO_FEATURE_MATCH_THRESHOLD", "0.4")
            )
        )
        self._initialized = False
        # 图像描述节流
        self._last_desc_time: float = 0.0
        self._last_description: str = ""
        self._desc_interval: float = float(
            os.environ.get("ECHOBOT_VIDEO_DESC_INTERVAL", "4")
        )

    async def initialize(self) -> None:
        if self._initialized:
            return
        await asyncio.to_thread(self._load_models)
        self._initialized = True

    def _load_models(self) -> None:
        self.image_desc_service.initialize()
        self.face_recognition_service.initialize()

    async def process_frame(self, frame_bytes: bytes) -> VisionContext:
        """双链路并行处理，通过 trace_id 追踪和拼接结果"""
        if not self._initialized:
            await self.initialize()

        current_time = time.time()
        trace_id = _make_trace_id(current_time)

        # 双链路并行
        image_desc_task = asyncio.to_thread(
            self._describe_image, frame_bytes, trace_id
        )
        face_task = asyncio.to_thread(
            self._detect_faces, frame_bytes, trace_id
        )

        image_desc, faces = await asyncio.gather(image_desc_task, face_task)

        # 用人脸识别结果增强描述
        enriched_desc = _enrich_description(image_desc, faces)

        timeline = [
            TimelineEvent(
                timestamp=current_time,
                event_type="image_desc",
                data={"trace_id": trace_id, "description": image_desc},
            ),
            TimelineEvent(
                timestamp=current_time,
                event_type="face_detect",
                data={"trace_id": trace_id, "faces": len(faces)},
            ),
        ]

        return VisionContext(
            trace_id=trace_id,
            start_time=current_time,
            end_time=current_time,
            image_description=enriched_desc,
            faces=faces,
            timeline=timeline,
        )

    def _describe_image(self, frame_bytes: bytes, trace_id: str) -> str:
        """图像描述链路，带节流"""
        now = time.time()
        if self._last_description and (now - self._last_desc_time) < self._desc_interval:
            return self._last_description
        description = self.image_desc_service.describe(frame_bytes)
        self._last_desc_time = now
        self._last_description = description
        return description

    def _detect_faces(self, frame_bytes: bytes, trace_id: str) -> list[Face]:
        """人脸识别链路"""
        return self.face_recognition_service.detect_and_extract_features(frame_bytes)

    async def close(self) -> None:
        self.image_desc_service.close()
        self.face_recognition_service.close()
        self._initialized = False


def _enrich_description(description: str, faces: list[Face]) -> str:
    """将人脸识别结果拼接到描述中"""
    if not faces:
        return description
    named = [f.person_name for f in faces if f.person_name]
    unnamed_count = sum(1 for f in faces if not f.person_name)
    parts = []
    if named:
        parts.append(f"识别到：{', '.join(named)}")
    if unnamed_count:
        parts.append(f"另有 {unnamed_count} 张未知人脸")
    if not parts:
        return description
    face_info = "；".join(parts)
    return f"{description}【{face_info}】"
