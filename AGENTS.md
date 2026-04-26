# AGENTS.md

本代码库以简洁与可读性优先。
请编写 Python 初学者也能看懂、维护和扩展的代码。

## 原则 (Principles)

1.  **清晰至上 (Clarity first)**
    - 编写简单、清晰、易于理解的代码。
    - 避免过早优化和不必要的抽象。
    - 遵循《Python 之禅》(The Zen of Python) 。
    - 目标版本为 **Python 3.11+**；避免使用已弃用的写法。

2.  **单一职责 (Single responsibility)**
    - 每个函数/类/模块都应当只把 **一件事** 做好。

3.  **关注性能 (Performance-aware)**
    - 在关键路径上对性能保持敏感。
    - 在异步代码中，**绝不要**因为磁盘/网络 I/O 或 CPU 密集型任务而**阻塞事件循环**。

## 风格与规范 (Style & conventions)

- 使用描述性的名称；避免使用晦涩难懂的单行代码。
- 优先使用 `pathlib` 而非 `os.path`。
- 除非引入依赖项确实物超所值，否则优先使用标准库。
- 使用 json.dump 或 json.dumps 时，必须设置 ensure_ascii=False，以确保输出内容支持中文原样显示，提高可读性。
- 所有入口复用同一个 Core，而不是各做一套业务逻辑。

## 异步 (适用时)

- 不要在 `async def` 中运行阻塞式 I/O 或 CPU 密集型任务。
- 使用 `asyncio.to_thread(...)`（必要时使用 executor）将阻塞任务转移出去。
- 在可能的情况下，保持异步 API 从端到端的一致性。

## 桌面端实现进展 (Desktop Implementation Progress)

这一节是给后续进程看的交接说明。重点不是通用规范，而是“桌面端已经做到哪里了、哪些结论已经确定、接下来别把哪些东西推翻重做”。

### 目标与当前完成度

桌面端的目标是把 EchoBot 从纯 Web 控制台扩展成一个 Live2D 桌宠。

当前已经完成的部分：

- 已新增独立的 `/desktop` 页面。
- 已搭好最小 Electron 壳。
- Electron 会自动拉起 Python 后端，再加载桌宠页面。
- 桌宠页已能复用现有的 Live2D / TTS / ASR / chat API。
- Web 端和桌面端的 Live2D 清晰度问题已经修复。
- 桌宠控件 UI 已做过一轮定稿。

当前还没有完成到“最终产品”的部分：

- Electron 还只是最小可用壳，不是完整打包方案。
- 桌宠交互还可以继续增强，但不应推翻现在的基础结构。

### 已经确定的架构决策

这些是已经验证过、不要轻易推翻的决定：

- 不要把桌面端做成直接复用 `/web` 的重控制台。
- 要保留独立的 `/desktop` 页面。
- 桌宠可见层只放极少量控件。
- 复杂表单和依赖 DOM 放进隐藏区，继续复用现有模块。
- Electron 的 `Web` 按钮不是内部切页，而是打开 `http://127.0.0.1:8000/web` 作为控制面板。
- 后端仍然通过 `python -m echobot app` 启动，而不是另做一套 daemon 入口。

### `/desktop` 页面进展

`/desktop` 现在已经不是草图，而是一条真实可用链路：

- 路由入口已经加在 `echobot/app/create_app.py`
- 页面文件是 `echobot/app/web/desktop.html`
- 页面脚本是 `echobot/app/web/desktop.js`
- 样式文件是 `echobot/app/web/styles/desktop.css`

页面的实现策略如下：

- 可见层是桌宠舞台 + 最小控件。
- 隐藏层保留 TTS / ASR / chat / Live2D 所需的兼容 DOM。
- 这样可以复用现有模块，而不是重新写一套前端业务。

### 当前桌宠 UI 定稿状态

这一版桌宠 UI 已经有明确结论：

- 右下角状态卡已经移除，不要再加回去。
- 右下角保留一组竖排按钮。
- 当前按钮顺序从上到下是：`Web -> 语音 -> 拖拽`。
- 三个按钮不显示文字。
- 三个按钮使用淡水印风格的线性图标。
- `Web` 使用云朵图标。
- `语音` 使用麦克风图标。
- `拖拽` 使用手形图标。
- 语音按钮允许在激活态高亮。
- 不要再通过 JS 把语音按钮改回“停止播报 / 结束录音 / 语音服务”这类可见文字。
- 如果需要表达状态，只更新 `class`、`title`、`aria-label`。

### Electron 进展

Electron 已经搭好最小实现，位置在 `desktop/` 目录：

- `desktop/main.js`
- `desktop/preload.js`
- `desktop/package.json`
- `desktop/README.md`

当前 Electron 行为：

- 启动时自动拉起后端：
  - `python -m echobot app --host 127.0.0.1 --port 8000`
- 等待后端健康检查通过。
- 打开桌宠页：
  - `http://127.0.0.1:8000/desktop`
- 托盘菜单可打开控制面板：
  - `http://127.0.0.1:8000/web`

### Live2D 高清修复进展

这是桌面端里一个已经查清根因并修过的问题，后续不要再重复排查一遍。

已知问题：

- Web 和桌面端都出现过 Live2D 发糊。

已确认根因：

- Pixi renderer 初始化时没有显式设置高 DPI 参数。

已经完成的修复：

- `resolution = window.devicePixelRatio`
- `autoDensity = true`

相关文件：

- `echobot/app/web/features/live2d/scene.js`
- `echobot/app/web/features/live2d/application-options.js`

### 其他顺手修过的基线问题

- Telegram 依赖缺失时的导入回退 bug 已修：
  - `echobot/channels/platforms/telegram.py`
- heartbeat API 在 macOS 上 `/private/var/...` 路径展示不一致的问题已修：
  - `echobot/app/routers/heartbeat.py`

### 测试进展

桌面端相关测试不是空白状态，已经补过以下验证：

- `/desktop` 页面结构测试：
  - `tests/test_app_api.py`
- Pixi 高清初始化测试：
  - `tests/live2d-application-options.test.mjs`
- Telegram fallback 测试：
  - `tests/test_gateway.py`

最近确认通过的命令：

```bash
python -m unittest discover -s tests -p 'test_app_api.py' -v
python -m unittest discover -s tests -p 'test_gateway.py' -v
python -m unittest discover -s tests -p 'test_app_api.py' -k desktop -v
node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-application-options.test.mjs
node --check /Users/zytwd/Code/workflow/EchoBot/echobot/app/web/desktop.js
node --check /Users/zytwd/Code/workflow/EchoBot/echobot/app/web/features/live2d/application-options.js
node --check /Users/zytwd/Code/workflow/EchoBot/echobot/app/web/features/live2d/scene.js
node --check /Users/zytwd/Code/workflow/EchoBot/desktop/main.js
node --check /Users/zytwd/Code/workflow/EchoBot/desktop/preload.js
```

### 后续进程接手时的注意事项

- 优先在现有 `/desktop` 基础上继续做，不要重新起炉灶。
- 优先复用现有 API 和前端模块，不要重写一套聊天 / 语音 / Live2D 系统。
- 做 UI 时要坚持“人物主体优先，控件弱存在感”。
- 如果改语音按钮状态，不能再让可见文字回到按钮上。
- 如果改布局，先确认不会破坏 `tests/test_app_api.py` 里的桌宠结构断言。
