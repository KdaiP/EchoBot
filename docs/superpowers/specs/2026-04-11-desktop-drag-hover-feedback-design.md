# Desktop Drag Hover Feedback Design

## Goal

让桌面端右下角“拖拽”按钮在鼠标悬浮时，拥有和上方两个圆形按钮一致的轻微浮动与边框加深反馈。

## Constraints

- 不改按钮顺序与结构。
- 不改 `desktop-drag-target` 的点击热区逻辑。
- 不改点穿透与窗口拖拽逻辑。
- 仅调整视觉反馈，保持当前淡水印风格。

## Design

- 继续保留拖拽按钮由透明命中层和可视层组成的结构。
- 给 `.desktop-drag-visual` 增加与 `.desktop-tool-button` 一致的过渡反馈。
- 在 `.desktop-drag-control:hover` 时，为 `.desktop-drag-visual` 添加：
  - 轻微上浮
  - 更深的边框颜色
  - 更清晰的文字/图标颜色
  - 更明显的阴影
- 在 `.desktop-drag-control:active` 时，把按钮位置压回去，和其它按钮一致。

## Files In Scope

- [`echobot/app/web/styles/desktop.css`](/Users/zytwd/Code/workflow/EchoBot/echobot/app/web/styles/desktop.css)
