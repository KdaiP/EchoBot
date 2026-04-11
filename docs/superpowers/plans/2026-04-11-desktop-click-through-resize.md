# Desktop Click-Through And Resize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Electron desktop pet window resizable while keeping the window click-through by default and limiting interactivity to the bottom-right toolbar.

**Architecture:** The Electron main process owns native mouse passthrough and resize behavior, while the `/desktop` renderer reports whether the pointer is inside a known interactive toolbar region. Browser-safe helper functions in the desktop frontend keep the page compatible outside Electron and make the logic easy to test in isolation.

**Tech Stack:** Electron, preload IPC bridge, vanilla JavaScript modules, Node test runner, Python unittest route coverage

---

## File Structure

- Modify: `desktop/main.js`
  - Enable resizing and add a focused IPC handler for mouse passthrough toggling.
- Modify: `desktop/preload.js`
  - Expose a small bridge method for toggling passthrough from the renderer.
- Modify: `echobot/app/web/desktop.js`
  - Wire toolbar hover state to the preload bridge and keep desktop behavior browser-safe.
- Create: `echobot/app/web/features/live2d/desktop-passthrough.js`
  - Hold small pure helper functions for passthrough bridge checks and toolbar hotspot detection.
- Modify: `echobot/app/web/desktop.html`
  - Add explicit marker attributes for toolbar interaction boundaries.
- Modify: `echobot/app/web/styles/desktop.css`
  - Keep the stage non-interactive and the toolbar interactive without changing the current visual structure.
- Create: `tests/live2d-desktop-passthrough.test.mjs`
  - Cover passthrough helper logic and interactive hotspot detection.
- Re-run: `tests/test_app_api.py`
  - Confirm `/desktop` structure remains compatible.

### Task 1: Add a failing passthrough helper test

**Files:**
- Create: `tests/live2d-desktop-passthrough.test.mjs`
- Test: `tests/live2d-desktop-passthrough.test.mjs`

- [ ] **Step 1: Write the failing test**

```javascript
import test from "node:test";
import assert from "node:assert/strict";

import {
    hasUsableDesktopPassthroughBridge,
    isDesktopInteractiveRegion,
} from "../echobot/app/web/features/live2d/desktop-passthrough.js";

test("hasUsableDesktopPassthroughBridge accepts bridge with setMousePassthrough", () => {
    assert.equal(
        hasUsableDesktopPassthroughBridge({
            setMousePassthrough() {},
        }),
        true,
    );
    assert.equal(hasUsableDesktopPassthroughBridge({}), false);
    assert.equal(hasUsableDesktopPassthroughBridge(null), false);
});

test("isDesktopInteractiveRegion detects toolbar hotspot markers", () => {
    const toolbar = {
        closest(selector) {
            return selector === "[data-desktop-interactive='true']" ? this : null;
        },
    };

    assert.equal(isDesktopInteractiveRegion(toolbar), true);
    assert.equal(isDesktopInteractiveRegion(null), false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-passthrough.test.mjs`
Expected: FAIL because `desktop-passthrough.js` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```javascript
export function hasUsableDesktopPassthroughBridge(desktopBridge) {
    return Boolean(
        desktopBridge
        && typeof desktopBridge.setMousePassthrough === "function",
    );
}

export function isDesktopInteractiveRegion(target) {
    return Boolean(
        target
        && typeof target.closest === "function"
        && target.closest("[data-desktop-interactive='true']"),
    );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-passthrough.test.mjs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/live2d-desktop-passthrough.test.mjs echobot/app/web/features/live2d/desktop-passthrough.js
git commit -m "test: cover desktop passthrough helpers"
```

### Task 2: Add Electron passthrough bridge and resize support

**Files:**
- Modify: `desktop/main.js`
- Modify: `desktop/preload.js`
- Test: `tests/live2d-desktop-passthrough.test.mjs`

- [ ] **Step 1: Extend the failing test with bridge expectations**

```javascript
test("hasUsableDesktopPassthroughBridge rejects cursor-only bridge", () => {
    assert.equal(
        hasUsableDesktopPassthroughBridge({
            getGlobalCursorState() {},
        }),
        false,
    );
});
```

- [ ] **Step 2: Run test to verify helper expectations still hold**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-passthrough.test.mjs`
Expected: PASS

- [ ] **Step 3: Write minimal implementation in Electron files**

```javascript
ipcMain.handle("desktop:set-mouse-passthrough", async (_event, enabled) => {
    if (!mainWindow) {
        return false;
    }
    const passthrough = Boolean(enabled);
    mainWindow.setIgnoreMouseEvents(passthrough, {
        forward: passthrough,
    });
    return passthrough;
});
```

```javascript
contextBridge.exposeInMainWorld("echobotDesktop", {
    setMousePassthrough(enabled) {
        return ipcRenderer.invoke("desktop:set-mouse-passthrough", enabled);
    },
});
```

Also change the window options to `resizable: true` and enable passthrough after the window is created.

- [ ] **Step 4: Run syntax checks**

Run: `node --check /Users/zytwd/Code/workflow/EchoBot/desktop/main.js`
Expected: no output

Run: `node --check /Users/zytwd/Code/workflow/EchoBot/desktop/preload.js`
Expected: no output

- [ ] **Step 5: Commit**

```bash
git add desktop/main.js desktop/preload.js
git commit -m "feat: add desktop mouse passthrough bridge"
```

### Task 3: Wire toolbar-only interaction in the desktop renderer

**Files:**
- Modify: `echobot/app/web/desktop.js`
- Modify: `echobot/app/web/desktop.html`
- Modify: `echobot/app/web/styles/desktop.css`
- Modify: `tests/live2d-desktop-passthrough.test.mjs`

- [ ] **Step 1: Add a failing hotspot test**

```javascript
test("isDesktopInteractiveRegion matches nested toolbar content", () => {
    const toolbar = {
        marker: true,
        closest(selector) {
            if (selector !== "[data-desktop-interactive='true']") {
                return null;
            }
            return this.marker ? this : null;
        },
    };
    const icon = {
        closest(selector) {
            return selector === "[data-desktop-interactive='true']" ? toolbar : null;
        },
    };

    assert.equal(isDesktopInteractiveRegion(icon), true);
});
```

- [ ] **Step 2: Run test to verify current helper logic covers it or fails appropriately**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-passthrough.test.mjs`
Expected: PASS once helper logic is complete

- [ ] **Step 3: Write minimal renderer and markup changes**

```html
<section class="desktop-toolbar" aria-label="Desktop controls" data-desktop-interactive="true">
```

```javascript
document.addEventListener("pointerover", handleDesktopPointerOver, true);
document.addEventListener("pointerout", handleDesktopPointerOut, true);
```

```javascript
async function setDesktopMousePassthrough(enabled) {
    if (!hasUsableDesktopPassthroughBridge(window.echobotDesktop)) {
        return;
    }
    await window.echobotDesktop.setMousePassthrough(enabled);
}
```

```css
.desktop-stage {
    pointer-events: none;
}

.desktop-toolbar,
.desktop-tool-button,
.stage-message {
    pointer-events: auto;
}
```

Keep the stage itself non-interactive and do not change button order or labels.

- [ ] **Step 4: Run syntax and route checks**

Run: `node --check /Users/zytwd/Code/workflow/EchoBot/echobot/app/web/desktop.js`
Expected: no output

Run: `python -m unittest discover -s tests -p 'test_app_api.py' -k desktop -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add echobot/app/web/desktop.js echobot/app/web/desktop.html echobot/app/web/styles/desktop.css tests/live2d-desktop-passthrough.test.mjs
git commit -m "feat: limit desktop interaction to toolbar"
```

### Task 4: Verify the full desktop change set

**Files:**
- Verify: `desktop/main.js`
- Verify: `desktop/preload.js`
- Verify: `echobot/app/web/desktop.js`
- Verify: `echobot/app/web/desktop.html`
- Verify: `echobot/app/web/styles/desktop.css`
- Verify: `tests/live2d-desktop-passthrough.test.mjs`
- Verify: `tests/live2d-desktop-cursor.test.mjs`
- Verify: `tests/live2d-desktop-stage-mode.test.mjs`

- [ ] **Step 1: Run frontend tests**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-passthrough.test.mjs`
Expected: PASS

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-cursor.test.mjs`
Expected: PASS

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-stage-mode.test.mjs`
Expected: PASS

- [ ] **Step 2: Run syntax checks**

Run: `node --check /Users/zytwd/Code/workflow/EchoBot/desktop/main.js`
Expected: no output

Run: `node --check /Users/zytwd/Code/workflow/EchoBot/desktop/preload.js`
Expected: no output

Run: `node --check /Users/zytwd/Code/workflow/EchoBot/echobot/app/web/desktop.js`
Expected: no output

- [ ] **Step 3: Run desktop route verification**

Run: `python -m unittest discover -s tests -p 'test_app_api.py' -k desktop -v`
Expected: PASS

- [ ] **Step 4: Review dirty worktree**

Run: `git status --short`
Expected: only intended files plus any pre-existing unrelated changes

- [ ] **Step 5: Commit**

```bash
git add desktop/main.js desktop/preload.js echobot/app/web/desktop.js echobot/app/web/desktop.html echobot/app/web/styles/desktop.css echobot/app/web/features/live2d/desktop-passthrough.js tests/live2d-desktop-passthrough.test.mjs
git commit -m "feat: add desktop click-through toolbar behavior"
```
