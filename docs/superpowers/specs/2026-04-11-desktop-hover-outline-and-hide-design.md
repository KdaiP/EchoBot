# Desktop Hover Outline And Character Hide Design

## Goal

为桌面端透明桌宠新增两个交互效果：

- 鼠标进入 Electron 画框时，在画框周边显示一圈白色描边。
- 鼠标移动到人物图层上时，临时隐藏人物；移开后恢复显示。

## Feasibility

第二个需求不需要大改架构。

当前桌面端已经能通过 Electron bridge 获取全局鼠标位置，并在前端将其映射到舞台坐标。Live2D 模型对象也已经可读到屏幕边界，因此可以基于现有轮询链路完成“命中人物边界时隐藏”的行为，无需改造 Electron 窗口模型或重写渲染结构。

## Constraints

- 不推翻现有 `/desktop` 页面结构。
- 不改按钮顺序与现有点穿透主逻辑。
- 人物隐藏应是临时状态，鼠标离开人物区域后自动恢复。
- 只在桌面端生效，不影响 `/web`。

## Design

### 1. Window hover outline

- 在桌面舞台元素上增加一个 hover 状态 class。
- 当全局鼠标位置位于当前 Electron 窗口矩形内时，为舞台加上白色描边。
- 鼠标离开窗口矩形后移除描边。

### 2. Character hit detection

- 使用现有全局鼠标轮询结果，换算为舞台坐标。
- 使用 Live2D 模型当前 `getBounds()` 结果判断鼠标是否命中人物可见边界矩形。
- 命中时将模型临时隐藏；未命中时恢复显示。

### 3. Scope boundary

- 先以模型边界矩形作为命中判断，不做像素级透明区域判断。
- 这是最小可维护实现，足够验证交互方向。

## Files In Scope

- [`echobot/app/web/desktop.js`](/Users/zytwd/Code/workflow/EchoBot/echobot/app/web/desktop.js)
- [`echobot/app/web/styles/desktop.css`](/Users/zytwd/Code/workflow/EchoBot/echobot/app/web/styles/desktop.css)
- [`echobot/app/web/features/live2d/model.js`](/Users/zytwd/Code/workflow/EchoBot/echobot/app/web/features/live2d/model.js)
- [`tests/live2d-model-external-focus.test.mjs`](/Users/zytwd/Code/workflow/EchoBot/tests/live2d-model-external-focus.test.mjs)
- [`tests/live2d-desktop-passthrough.test.mjs`](/Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-passthrough.test.mjs)
