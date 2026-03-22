"""视频通话插件 - 数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Face:
    """人脸信息"""

    face_id: str
    person_name: Optional[str] = None
    confidence: float = 0.0
    features: list[float] = field(default_factory=list)  # 512维特征向量
    position: dict = field(default_factory=dict)


@dataclass
class TimelineEvent:
    """时序事件"""

    timestamp: float
    event_type: str  # "image_desc" | "face_detect"
    data: dict


@dataclass
class VisionContext:
    """视觉上下文（支持时间段合并）"""

    trace_id: str              # 有序 trace id，格式: 时间戳_序号
    start_time: float          # 该描述首次出现时间
    end_time: float            # 该描述最后出现时间（动态延伸）
    image_description: str
    faces: list[Face]
    timeline: list[TimelineEvent]

    # 兼容旧代码
    @property
    def timestamp(self) -> float:
        return self.start_time
