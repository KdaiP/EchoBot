# Desktop Hover Outline And Character Hide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a white outline when the mouse enters the desktop pet window and temporarily hide the Live2D character when the cursor moves onto the character layer.

**Architecture:** Reuse the existing desktop global-cursor polling loop. Desktop hover state stays in the desktop page, while model hit detection and visibility toggling live in the Live2D model controller so the behavior stays close to the rendering state.

**Tech Stack:** Vanilla JavaScript, CSS, Node test runner

---

### Task 1: Lock mouse-in-window and model-hit behavior in tests

**Files:**
- Modify: `tests/live2d-desktop-passthrough.test.mjs`
- Modify: `tests/live2d-model-external-focus.test.mjs`

- [ ] **Step 1: Add a failing window hit test**

```javascript
test("is cursor inside desktop window bounds detects in-window hover", () => {
    assert.equal(
        isCursorInsideDesktopWindow(
            { cursorX: 120, cursorY: 140, windowBounds: { x: 100, y: 100, width: 80, height: 60 } },
        ),
        true,
    );
    assert.equal(
        isCursorInsideDesktopWindow(
            { cursorX: 90, cursorY: 140, windowBounds: { x: 100, y: 100, width: 80, height: 60 } },
        ),
        false,
    );
});
```

- [ ] **Step 2: Add a failing model hit / hide test**

```javascript
test("setDesktopCursorOverlap toggles model visibility from stage point hit", async () => {
    // stub model.getBounds() and assert visible flips false/true
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-passthrough.test.mjs /Users/zytwd/Code/workflow/EchoBot/tests/live2d-model-external-focus.test.mjs`

Expected: FAIL because the new helpers and visibility toggle API do not exist yet.

### Task 2: Implement minimal desktop hover outline and character hide logic

**Files:**
- Modify: `echobot/app/web/features/live2d/desktop-passthrough.js`
- Modify: `echobot/app/web/features/live2d/model.js`
- Modify: `echobot/app/web/desktop.js`
- Modify: `echobot/app/web/styles/desktop.css`

- [ ] **Step 1: Add a pure helper for window-bound hover detection**

```javascript
export function isCursorInsideDesktopWindow(cursorState) {
    // compare cursorX/cursorY against windowBounds rectangle
}
```

- [ ] **Step 2: Add a model controller API for temporary cursor-overlap visibility**

```javascript
function setDesktopCursorOverlap(overlapping) {
    const model = live2dState.live2dModel;
    if (!model) {
        return false;
    }
    model.visible = !overlapping;
    return overlapping;
}
```

- [ ] **Step 3: Wire desktop polling to outline and model visibility**

```javascript
const insideWindow = isCursorInsideDesktopWindow(cursorState);
DOM.stageElement.classList.toggle("is-desktop-hovered", insideWindow);
live2d.setDesktopCursorOverlap(isPointInsideCharacterBounds(stagePoint));
```

- [ ] **Step 4: Add the white outline style**

```css
.desktop-stage.is-desktop-hovered::after {
    opacity: 1;
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-passthrough.test.mjs /Users/zytwd/Code/workflow/EchoBot/tests/live2d-model-external-focus.test.mjs`

Expected: PASS

### Task 3: Run regression checks

**Files:**
- Modify: none

- [ ] **Step 1: Run related desktop tests**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-scene.test.mjs /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-stage-mode.test.mjs /Users/zytwd/Code/workflow/EchoBot/tests/live2d-desktop-passthrough.test.mjs /Users/zytwd/Code/workflow/EchoBot/tests/live2d-model-external-focus.test.mjs /Users/zytwd/Code/workflow/EchoBot/tests/live2d-default-transform.test.mjs`

Expected: PASS

- [ ] **Step 2: Run syntax checks**

Run: `node --check /Users/zytwd/Code/workflow/EchoBot/echobot/app/web/desktop.js`

Run: `node --check /Users/zytwd/Code/workflow/EchoBot/echobot/app/web/features/live2d/model.js`

Run: `node --check /Users/zytwd/Code/workflow/EchoBot/echobot/app/web/features/live2d/desktop-passthrough.js`

Expected: no output
