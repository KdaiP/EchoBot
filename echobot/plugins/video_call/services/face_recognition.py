"""视频通话插件 - 人脸识别服务（InsightFace + 特征向量 Map 映射）"""

from __future__ import annotations

import io
import uuid
from typing import Optional

import numpy as np
from PIL import Image

from ..models import Face


class FaceRecognitionService:
    """
    人脸识别服务：
    1. 用 InsightFace 检测人脸 + 提取 512 维特征向量
    2. 与已知人脸 Map 做余弦相似度匹配
    3. 有匹配 → 返回【向量 + 姓名】；无匹配 → 透传向量
    """

    def __init__(self, feature_match_threshold: float = 0.4) -> None:
        self._initialized = False
        self._app = None  # insightface FaceAnalysis
        self._known: dict[str, np.ndarray] = {}  # person_name -> 平均特征向量
        self._match_threshold = feature_match_threshold

    def initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        try:
            import insightface
            from insightface.app import FaceAnalysis
            from loguru import logger
            logger.info("Loading InsightFace buffalo_sc model (first run may download ~85MB)...")
            self._app = FaceAnalysis(
                name="buffalo_sc",  # 轻量模型，速度快
                allowed_modules=["detection", "recognition"],
                providers=["CPUExecutionProvider"],
            )
            self._app.prepare(ctx_id=-1, det_size=(320, 320))  # 小尺寸更快
            logger.info("InsightFace buffalo_sc model ready")
        except Exception as e:
            from loguru import logger
            logger.warning(f"InsightFace init failed, face recognition disabled: {e}")
            self._app = None

    def detect_and_extract_features(
        self, frame_bytes: bytes, confidence_threshold: float = 0.5
    ) -> list[Face]:
        """检测人脸，提取特征向量，查 Map 映射人名"""
        if not self._app:
            return []
        try:
            img = _bytes_to_bgr(frame_bytes)
            if img is None:
                return []
            faces_raw = self._app.get(img)
            if not faces_raw:
                return []

            result: list[Face] = []
            for i, f in enumerate(faces_raw):
                det_score = float(getattr(f, "det_score", 0.0))
                if det_score < confidence_threshold:
                    continue

                embedding = getattr(f, "embedding", None)
                if embedding is None:
                    continue

                vec = np.array(embedding, dtype=np.float32)
                vec = vec / (np.linalg.norm(vec) + 1e-8)  # L2 归一化

                # 查 Map
                person_name, score = self._match(vec)

                bbox = getattr(f, "bbox", [0, 0, 0, 0])
                result.append(Face(
                    face_id=str(uuid.uuid4())[:8],
                    person_name=person_name,
                    confidence=det_score,
                    features=vec.tolist(),
                    position={
                        "x": int(bbox[0]), "y": int(bbox[1]),
                        "w": int(bbox[2] - bbox[0]),
                        "h": int(bbox[3] - bbox[1]),
                        "match_score": round(score, 3) if person_name else None,
                    },
                ))
            return result
        except Exception as e:
            from loguru import logger
            logger.warning(f"Face detection failed: {e}")
            return []

    def add_known_face(
        self, person_name: str, features: list[float] | np.ndarray
    ) -> bool:
        """添加已知人脸向量（支持多次添加，取平均向量）"""
        if not person_name:
            return False
        vec = np.array(features, dtype=np.float32)
        vec = vec / (np.linalg.norm(vec) + 1e-8)
        if person_name in self._known:
            # 滑动平均
            self._known[person_name] = (
                self._known[person_name] * 0.7 + vec * 0.3
            )
            self._known[person_name] /= (
                np.linalg.norm(self._known[person_name]) + 1e-8
            )
        else:
            self._known[person_name] = vec
        return True

    def add_known_face_from_frame(
        self, person_name: str, frame_bytes: bytes
    ) -> bool:
        """从帧图像中提取人脸向量并绑定姓名"""
        faces = self.detect_and_extract_features(frame_bytes)
        if not faces:
            return False
        # 取置信度最高的人脸
        best = max(faces, key=lambda f: f.confidence)
        if not best.features:
            return False
        return self.add_known_face(person_name, best.features)

    def list_known_faces(self) -> list[str]:
        return list(self._known.keys())

    def remove_known_face(self, person_name: str) -> bool:
        if person_name in self._known:
            del self._known[person_name]
            return True
        return False

    def _match(self, vec: np.ndarray) -> tuple[str | None, float]:
        """余弦相似度匹配，返回 (person_name, score)"""
        best_name = None
        best_score = 0.0
        for name, known_vec in self._known.items():
            score = float(np.dot(vec, known_vec))
            if score > best_score:
                best_score = score
                best_name = name
        if best_score >= self._match_threshold:
            return best_name, best_score
        return None, best_score

    def close(self) -> None:
        self._known.clear()
        self._app = None
        self._initialized = False


def _bytes_to_bgr(frame_bytes: bytes):
    """将 JPEG bytes 转为 BGR numpy array（insightface 需要 BGR）"""
    try:
        import cv2
        arr = np.frombuffer(frame_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except ImportError:
        # 没有 cv2，用 PIL 转
        img_pil = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
        arr = np.array(img_pil)[:, :, ::-1].copy()  # RGB -> BGR
        return arr
    except Exception:
        return None
