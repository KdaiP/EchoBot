"""视频通话插件 - 图像描述服务"""

from __future__ import annotations

import base64
import io
import os
from typing import Optional

from PIL import Image


class ImageDescriptionService:
    """图像描述服务 - 使用 MiniMax Vision，降级返回基础描述"""

    def __init__(self, model_name: str = "auto") -> None:
        self.model_name = model_name
        self._initialized = False
        self._use_llm = False
        self._client = None
        self._llm_model = None
        self._llm_base_url = None
        self._call_count = 0
        self._describe_every = 5  # 每5帧调用一次 Vision API
        self._last_description = ""

    def initialize(self) -> None:
        """初始化服务"""
        if self._initialized:
            return
        self._initialized = True

        # 图像描述用 VISION_* 配置（MiniMax），降级到 LLM_*
        api_key = os.environ.get("VISION_API_KEY") or os.environ.get("LLM_API_KEY", "")
        base_url = os.environ.get("VISION_BASE_URL") or os.environ.get("LLM_BASE_URL", "")
        model = os.environ.get("VISION_MODEL") or os.environ.get("LLM_MODEL", "")

        if not api_key:
            return

        try:
            import openai
            self._client = openai.OpenAI(
                api_key=api_key,
                base_url=base_url or None,
            )
            self._llm_model = model
            self._use_llm = True
        except ImportError:
            pass

    def describe(self, frame_bytes: bytes, prompt: str = "用中文简洁描述画面内容：场景是什么、有没有人（如有请描述外貌特征：性别/发型/眼镜/衣着等）、有什么显眼的物品。不超过60字。") -> str:
        """生成图像描述"""
        if not self._initialized:
            self.initialize()

        try:
            image = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
            image.thumbnail((640, 480))
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=75)
            compressed_bytes = buf.getvalue()

            if self._use_llm and self._client:
                return self._describe_with_llm(compressed_bytes, prompt)
            else:
                return self._describe_fallback(image)
        except Exception as e:
            return f"画面处理异常: {e}"

    def _describe_with_llm(self, frame_bytes: bytes, prompt: str) -> str:
        """用 LLM Vision 描述图像"""
        try:
            b64 = base64.b64encode(frame_bytes).decode("utf-8")
            response = self._client.chat.completions.create(
                model=self._llm_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64}",
                                },
                            },
                        ],
                    }
                ],
                max_tokens=200,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            from loguru import logger
            logger.warning(f"Vision LLM failed, using fallback: {e}")
            try:
                image = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
                return self._describe_fallback(image)
            except Exception:
                return "画面描述不可用"

    def _describe_fallback(self, image: Image.Image) -> str:
        """降级描述：返回图像基础信息"""
        import numpy as np
        w, h = image.size
        arr = np.array(image)
        brightness = float(arr.mean())
        if brightness > 200:
            light = "明亮"
        elif brightness > 100:
            light = "正常光线"
        else:
            light = "较暗"
        return f"视频画面（{w}x{h}，{light}）"

    def close(self) -> None:
        """清理资源"""
        self._client = None
        self._initialized = False
