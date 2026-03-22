# EchoBot 视频通话插件 - 架构与使用说明

## 功能概述

视频通话插件（`video_call`）为 EchoBot 提供实时视觉感知能力：

- **实时摄像头画面处理**：通过 WebSocket 接收前端推送的视频帧
- **图像语义描述**：调用视觉模型对每帧画面生成自然语言描述
- **人脸识别与绑定**：用 InsightFace 提取 512 维特征向量，与已知人脸库做余弦相似度匹配
- **视觉上下文自动注入**：每次对话时，coordinator 自动将当前视觉信息追加为临时系统消息，模型无需工具调用即可感知画面
- **主动记忆陌生人**：检测到未识别人脸时，模型会主动询问姓名并通过工具绑定，重启后仍可识别

---

## 架构概览

```
前端摄像头
    │  WebSocket /api/web/video/stream
    ▼
VisionProcessingService
    ├── ImageDescriptionService   # 视觉模型：图像 → 自然语言描述
    └── FaceRecognitionService    # InsightFace：检测 + 512 维特征提取 + 余弦匹配
    │
    ▼
VisionContextProvider（内存缓存，最近 80 帧）
    │
    │  on_startup 注入
    ▼
ConversationCoordinator._vision_context_provider
    │  每次对话自动调用 _build_vision_message()
    ▼
transient_system_messages → RoleplayEngine / AgentRunner
    │
    ▼
大模型（感知视觉 + 主动询问陌生人）
    │  用户回答名字 → decision engine 路由到 agent
    ▼
BindFaceToNameTool（BaseTool）
    ├── FaceRecognitionService.add_known_face_from_frame()  # 内存
    └── FaceFeatureInterceptor.add_or_update_feature()      # 持久化到 .echobot/face_features.json
```

### 工具注入方式（非侵入）

插件工具通过 `create_app.py` 里包装 `tool_registry_factory` 注入，不修改框架代码：

```python
# create_app.py
def _plugin_context_builder(opts):
    ctx = build_runtime_context(opts, load_session_state=False)
    original_factory = ctx.tool_registry_factory

    def wrapped_factory(session_name, scheduled_context):
        registry = original_factory(session_name, scheduled_context)
        for tool in video_plugin.get_tool_instances():
            registry.register(tool)
        return registry

    ctx.tool_registry_factory = wrapped_factory
    return ctx
```

### 视觉上下文注入方式

插件在 `on_startup` 时调用：

```python
coordinator.set_vision_context_provider(self.vision_provider)
```

Coordinator 在每次 `handle_user_turn_stream` 时自动调用 `_build_vision_message()`，将视觉信息作为 `transient_system_messages` 注入，**不写入会话历史**。

---

## 目录结构

```
echobot/plugins/video_call/
├── __init__.py                  # VideoCallPlugin：插件入口，on_startup/on_shutdown
├── models.py                    # 数据模型：Face, VisionContext
├── vision_provider.py           # VisionContextProvider：帧缓存与合并
├── interceptors/
│   └── __init__.py              # FaceFeatureInterceptor：特征持久化（JSON）
├── routers/
│   └── __init__.py              # API 路由：WebSocket 视频流、人脸绑定接口
├── services/
│   ├── __init__.py              # VisionProcessingService：双链路并行处理
│   ├── face_recognition.py      # FaceRecognitionService：InsightFace 封装
│   └── image_description.py     # ImageDescriptionService：视觉描述
└── tools/
    ├── __init__.py              # VISION_TOOLS：旧格式工具定义（兼容用）
    ├── face_tools.py            # BaseTool 实现：bind_face_to_name / list_known_faces / forget_face
    └── handlers.py              # VisionToolHandler：工具调用处理器
```

---

## 可用工具（Agent 路径）

| 工具名 | 触发场景 | 功能 |
|--------|----------|------|
| `bind_face_to_name` | "我叫XXX" / "记住我" / "这是XXX" | 从当前摄像头帧提取人脸特征并绑定姓名 |
| `list_known_faces` | "你认识哪些人" | 列出所有已绑定的人脸姓名 |
| `forget_face` | "忘掉XXX" | 删除某人的人脸绑定记录 |

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| WS | `/api/web/video/stream` | 视频帧推送（JPEG bytes） |
| GET | `/api/web/video/context` | 获取当前视觉上下文列表 |
| GET | `/api/web/video/snapshot` | 获取最新一帧快照 |
| POST | `/api/web/video/face-bind` | 通过特征向量绑定人脸 |
| POST | `/api/web/video/face-bind-frame` | 通过图像帧绑定人脸 |
| GET | `/api/web/video/face-list` | 查询已绑定人脸列表 |

---

## 启用方式

在 `.env` 中设置：

```env
ECHOBOT_ENABLE_VIDEO_CALL=true
```

前端摄像头页面：`http://localhost:8000/web/camera`

---

## 人脸数据持久化

绑定的人脸特征向量（512 维，InsightFace buffalo_sc）保存在：

```
.echobot/face_features.json
```

重启后自动加载，无需重新绑定。

---

## 依赖

```
insightface
onnxruntime
Pillow
numpy
```
