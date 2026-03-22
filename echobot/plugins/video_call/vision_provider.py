"""视频通话插件 - 视觉上下文提供器（时间段合并去重）"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict
from typing import Optional

from .models import VisionContext


# 描述相似度阈值：两段描述相似度超过此值视为「未变化」
_SIMILARITY_THRESHOLD = 0.7


class VisionContextProvider:
    """视觉上下文提供器 - 时间段合并去重，内容不变则延伸时间段"""

    def __init__(self, max_frames: int = 80) -> None:
        self.max_frames = max_frames
        self.frames: deque[dict] = deque(maxlen=max_frames)

    def add_vision_context(self, vision_context: VisionContext) -> None:
        """添加视觉上下文，内容未变则延伸时间段，变化则插入新条目"""
        new_desc = vision_context.image_description.strip()
        new_faces = vision_context.faces

        if self.frames:
            last = self.frames[-1]
            last_desc = last.get("image_description", "").strip()

            # 检查描述是否发生变化
            if _is_similar(last_desc, new_desc):
                # 未变化：延伸时间段
                last["end_time"] = vision_context.end_time
                last["trace_id_end"] = vision_context.trace_id
                # 更新人脸信息（可能新增了识别结果）
                if new_faces:
                    last["faces"] = _faces_to_dict(new_faces)
                    last["face_count"] = len(new_faces)
                return

        # 描述变化或首帧：插入新条目
        frame_data = {
            "trace_id": vision_context.trace_id,
            "trace_id_end": vision_context.trace_id,
            "start_time": vision_context.start_time,
            "end_time": vision_context.end_time,
            # 兼容旧字段
            "timestamp": vision_context.start_time,
            "image_description": new_desc,
            "face_count": len(new_faces),
            "faces": _faces_to_dict(new_faces),
        }
        self.frames.append(frame_data)

    def get_vision_context_list(self) -> list[dict]:
        """返回所有时间段条目，按 start_time 升序"""
        return list(self.frames)


def _is_similar(a: str, b: str) -> bool:
    """判断两段描述是否相似（词袋 Jaccard 相似度）"""
    if not a or not b:
        return False
    # 完全相同快速返回
    if a == b:
        return True
    words_a = set(a)
    words_b = set(b)
    if not words_a or not words_b:
        return False
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return (intersection / union) >= _SIMILARITY_THRESHOLD


def _faces_to_dict(faces) -> list[dict]:
    return [
        {
            "face_id": f.face_id,
            "person_name": f.person_name,
            "confidence": f.confidence,
        }
        for f in faces
    ]
