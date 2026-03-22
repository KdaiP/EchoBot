"""视频通话插件 - 特征拦截器"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np

from ..models import Face


class FaceFeatureInterceptor:
    """人脸特征拦截器"""

    def __init__(self, feature_map_file: Optional[Path] = None) -> None:
        self.feature_map_file = feature_map_file or Path(".echobot/face_features.json")
        self.feature_data = self._load_feature_map()
        self.match_threshold = 0.6  # 余弦相似度阈值

    def intercept(self, faces: list[Face]) -> list[Face]:
        """将人脸特征映射到人名"""
        for face in faces:
            person_name = self._match_face(face.features)
            if person_name:
                face.person_name = person_name
        return faces

    def _match_face(self, features: list[float]) -> Optional[str]:
        """特征匹配（余弦相似度）"""
        if not features or not self.feature_data:
            return None

        try:
            query_features = np.array(features)
            best_match = None
            best_similarity = 0.0

            for person_name, stored_features_list in self.feature_data.items():
                for stored_features in stored_features_list:
                    similarity = self._cosine_similarity(
                        query_features, np.array(stored_features)
                    )
                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_match = person_name

            if best_similarity >= self.match_threshold:
                return best_match
        except Exception:
            pass

        return None

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """计算余弦相似度"""
        try:
            dot_product = np.dot(a, b)
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return float(dot_product / (norm_a * norm_b))
        except Exception:
            return 0.0

    def add_or_update_feature(
        self, person_name: str, features: list[float], confidence: float = 0.9
    ) -> bool:
        """新增或更新特征"""
        if not features:
            return False

        try:
            if person_name not in self.feature_data:
                self.feature_data[person_name] = []

            self.feature_data[person_name].append(features)
            self._save_feature_map()
            return True
        except Exception:
            return False

    def query_person_by_face(self, features: list[float]) -> Optional[str]:
        """根据人脸特征查询人名"""
        return self._match_face(features)

    def _load_feature_map(self) -> dict:
        """从文件加载特征映射"""
        if self.feature_map_file.exists():
            try:
                data = json.loads(self.feature_map_file.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_feature_map(self) -> None:
        """保存特征映射到文件"""
        self.feature_map_file.parent.mkdir(parents=True, exist_ok=True)
        self.feature_map_file.write_text(
            json.dumps(self.feature_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
