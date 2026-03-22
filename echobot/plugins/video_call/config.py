"""视频通话插件 - 配置管理"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class VideoCallConfig:
    """视频通话配置"""

    # 视频处理配置
    frame_rate: float = 1.0  # 每秒采集帧数（支持小数，如0.25=4秒一帧）
    max_frame_size: str = "1280x720"  # 最大帧尺寸
    face_confidence_threshold: float = 0.8  # 人脸检测置信度阈值
    feature_match_threshold: float = 0.6  # 特征匹配阈值

    @classmethod
    def from_env(cls) -> VideoCallConfig:
        """从环境变量加载配置"""
        return cls(
            frame_rate=float(os.getenv("ECHOBOT_VIDEO_FRAME_RATE", "1.0")),
            max_frame_size=os.getenv("ECHOBOT_VIDEO_MAX_FRAME_SIZE", "1280x720"),
            face_confidence_threshold=float(
                os.getenv("ECHOBOT_VIDEO_FACE_CONFIDENCE_THRESHOLD", "0.8")
            ),
            feature_match_threshold=float(
                os.getenv("ECHOBOT_VIDEO_FEATURE_MATCH_THRESHOLD", "0.6")
            ),
        )
