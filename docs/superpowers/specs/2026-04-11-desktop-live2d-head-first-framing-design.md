# Desktop Live2D Head-First Framing Design

## Goal

调整桌面端 `/desktop` 的 Live2D 默认构图。

当 Electron 窗口高度变低、模型无法完整留在画框内时，优先保证头部留在画框内，让腿部或下半身先离开画框，而不是继续使用“整体居中”的裁切方式。

## Current Behavior

- 桌面端默认构图由 [`echobot/app/web/features/live2d/default-transform.js`](/Users/zytwd/Code/workflow/EchoBot/echobot/app/web/features/live2d/default-transform.js) 计算。
- 当前桌面端逻辑保持水平居中，并使用固定比例的纵向中心点。
- 当窗口高度变小且模型按当前比例缩放后超过舞台高度时，模型会上下同时出框，导致头部也容易被裁掉。

## Constraints

- 保留独立 `/desktop` 页面和现有 Electron 壳，不推翻当前桌面端架构。
- 不修改桌面端按钮布局、隐藏兼容 DOM、或现有交互结构。
- 网页端 `/web` 的默认构图行为保持不变。
- 改动应尽量集中在默认构图计算层，方便后续维护。

## Proposed Design

### 1. Keep desktop horizontal centering

桌面端继续保持角色的水平居中，这样不会影响现有舞台视觉和按钮布局。

### 2. Replace vertical centering with head-safe anchoring

桌面端默认构图不再直接使用固定 `y` 百分比。

改为：

- 先沿用现有桌面端缩放思路，保持当前“桌宠更近、更主体化”的视觉方向。
- 计算缩放后的模型高度。
- 为模型顶部预留一段固定比例的“头部安全留白”。
- 用这个留白反推模型中心点 `y`，使模型顶部尽量留在舞台内。

这样当模型太高、舞台放不下时：

- 顶部会尽量保留在画框内。
- 溢出的部分主要落到底部。
- 结果就是腿部先离开画框。

### 3. Preserve existing web framing

网页端继续使用现有 fit-based 逻辑，不共享这次桌面端的头部优先裁切策略。

## Testing

- 补充桌面端默认构图测试，明确验证“低窗口时顶部更安全、底部先溢出”。
- 保留网页端默认构图测试，确认未被回归影响。
- 运行 Node 测试与语法检查，覆盖本次修改涉及的文件。

## Files In Scope

- [`echobot/app/web/features/live2d/default-transform.js`](/Users/zytwd/Code/workflow/EchoBot/echobot/app/web/features/live2d/default-transform.js)
- [`tests/live2d-default-transform.test.mjs`](/Users/zytwd/Code/workflow/EchoBot/tests/live2d-default-transform.test.mjs)

## Out Of Scope

- Electron 窗口管理与拖拽逻辑
- `/desktop` 页面结构与按钮顺序
- Web 控制台布局
- Live2D 模型资源本身
