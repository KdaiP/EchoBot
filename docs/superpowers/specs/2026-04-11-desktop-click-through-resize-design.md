# Desktop Click-Through And Resize Design

## Background

EchoBot already has a working Electron desktop shell that opens the `/desktop` page and keeps the UI intentionally minimal.

The remaining desktop issues are:

1. The transparent Electron window cannot currently be resized.
2. The desktop pet window should click through to apps underneath almost everywhere.
3. Only the bottom-right control buttons should remain interactive.
4. Only the hand button should keep the existing window drag behavior.

These changes must preserve the current `/desktop` architecture:

- Keep the dedicated `/desktop` page.
- Keep the current bottom-right toolbar and button order.
- Keep visible controls minimal.
- Reuse the existing frontend modules instead of building a separate desktop frontend stack.

## Goal

Make the Electron desktop pet behave like a mostly non-interactive overlay:

- The window is resizable.
- The Live2D stage and the rest of the page click through to the app or desktop behind it.
- The three bottom-right buttons remain clickable.
- The hand button remains the only drag affordance for moving the window.

## Recommended Approach

Use Electron main-process mouse-event ignoring as the source of truth for click-through behavior.

### Why this approach

- CSS alone cannot forward clicks to applications behind the Electron window.
- Keeping the ignore-mouse state in Electron makes the behavior reliable and easy to reason about.
- The page only needs to report whether the pointer is over a known interactive area.
- This keeps responsibilities clear:
  - Electron owns native window behavior.
  - The `/desktop` page owns which elements count as interactive hotspots.

## Architecture

### 1. Main process window behavior

Update `desktop/main.js` to:

- create the frameless transparent window with `resizable: true`
- default the window into ignore-mouse mode after it is created
- expose a new IPC handler that toggles `mainWindow.setIgnoreMouseEvents(...)`
- keep the existing drag button behavior unchanged

The intended state model is:

- Pointer over desktop toolbar or one of its buttons:
  - disable ignore-mouse mode
  - Electron receives clicks
- Pointer anywhere else:
  - enable ignore-mouse mode with forwarding
  - clicks go to the app or desktop underneath

Using `{ forward: true }` allows hover and movement information to continue reaching the renderer while the window is ignoring clicks.

### 2. Preload bridge

Update `desktop/preload.js` to expose a small, explicit desktop API for mouse passthrough control.

The bridge will expose one method:

- `setMousePassthrough(enabled)`

This keeps the desktop page independent from Electron internals and matches the existing bridge pattern already used for opening the web console and reading cursor state.

### 3. Desktop page interaction boundaries

Update `echobot/app/web/desktop.js` and `echobot/app/web/desktop.html` so the page reports when the pointer enters or leaves interactive regions.

Interactive regions are:

- the toolbar container
- the `Web` button
- the `语音` button
- the `拖拽` button

Non-interactive regions are:

- the Live2D canvas
- the rest of the stage
- the hidden compatibility DOM

The page will:

- enable click-through by default after load
- disable click-through while the pointer is over the toolbar
- re-enable click-through when the pointer leaves the toolbar
- keep the hand button as the only drag hotspot

This avoids trying to make the model or stage partially interactive and matches the user-approved behavior exactly.

### 4. Styling

Update `echobot/app/web/styles/desktop.css` so the interactive boundary is visually and behaviorally clear:

- keep the stage itself non-interactive
- keep the toolbar interactive
- preserve the current minimal visual design
- avoid adding strong window chrome or a visible frame

The design will not attempt to remove the faint translucent look in this change set unless a code change directly affecting passthrough or resizing requires it.

## Data Flow

The pointer state flow will be:

1. Desktop page loads.
2. Desktop page requests mouse passthrough to be enabled.
3. User moves pointer over the toolbar.
4. Renderer calls the preload bridge.
5. Main process disables ignore-mouse mode.
6. Toolbar buttons receive normal click events.
7. User leaves the toolbar.
8. Renderer re-enables ignore-mouse mode.
9. Clicks once again pass through to the window underneath.

## Error Handling

- If the desktop bridge is unavailable, the page should fail safely and keep current browser-compatible behavior.
- If passthrough toggling fails, log the error and avoid breaking existing desktop controls.
- Existing non-Electron browser access to `/desktop` should continue to function without exceptions.

## Testing

Add focused tests without changing existing desktop structure assertions.

### Update or add frontend tests for:

- desktop bridge capability detection for passthrough support
- interactive hotspot detection logic
- passthrough state toggling decisions for toolbar enter/leave

### Keep existing tests passing for:

- `/desktop` route structure and button order
- Live2D transparent desktop stage behavior

## Scope Guardrails

This change does not:

- redesign the `/desktop` layout
- add a separate desktop-only frontend architecture
- make the Live2D character body draggable
- add visible resize handles or a new control panel
- remove the faint translucent look unless needed for correctness

## Implementation Notes

- Respect the current dirty worktree and avoid overwriting unrelated edits.
- Keep code beginner-friendly and explicit.
- Prefer small helper functions with clear names over clever event wiring.
