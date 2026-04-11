# Desktop Live2D Head-First Framing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the desktop Live2D default framing keep the head inside the stage when the window becomes short, allowing the lower body to leave the frame first.

**Architecture:** Keep the current desktop-only default transform entry point and replace only its vertical framing rule. The desktop branch will still center horizontally and keep the existing scale strategy, but it will compute `y` from a head-safe top margin instead of a fixed center ratio. The web branch remains unchanged.

**Tech Stack:** Vanilla JavaScript, Node test runner, existing Live2D frontend modules

---

### Task 1: Lock the desired framing behavior in tests

**Files:**
- Modify: `tests/live2d-default-transform.test.mjs`
- Test: `tests/live2d-default-transform.test.mjs`

- [ ] **Step 1: Write the failing test**

```javascript
test("resolveDefaultLive2DTransform keeps desktop head visible when stage is short", () => {
    const stageWidth = 420;
    const stageHeight = 320;
    const baseWidth = 280;
    const baseHeight = 700;
    const result = resolveDefaultLive2DTransform({
        stageWidth,
        stageHeight,
        baseWidth,
        baseHeight,
        desktopTransparentStage: true,
    });

    const scaledHeight = baseHeight * result.scale;
    const topEdge = result.y - scaledHeight * 0.5;
    const bottomEdge = result.y + scaledHeight * 0.5;

    assert.equal(result.x, 210);
    assert.ok(topEdge >= 0, "desktop framing should keep the model top inside the stage");
    assert.ok(topEdge <= stageHeight * 0.12, "desktop framing should leave only a small head margin");
    assert.ok(bottomEdge > stageHeight, "desktop framing should let the lower body leave the stage first");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-default-transform.test.mjs`

Expected: FAIL because the current desktop branch still uses a fixed vertical center, so `topEdge >= 0` does not hold.

- [ ] **Step 3: Write minimal implementation**

```javascript
if (desktopTransparentStage) {
    const nextScale = Math.max(widthRatio * 0.96, heightRatio * 1.18);
    const scaledHeight = baseHeight * nextScale;
    const topMargin = clampDesktopTopMargin(stageHeight);
    return {
        x: stageWidth * 0.5,
        y: topMargin + scaledHeight * 0.5,
        scale: roundToTransform(nextScale),
    };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-default-transform.test.mjs`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/live2d-default-transform.test.mjs echobot/app/web/features/live2d/default-transform.js
git commit -m "fix: keep desktop live2d framing head-first"
```

### Task 2: Verify no regression in related desktop behavior

**Files:**
- Modify: `echobot/app/web/features/live2d/default-transform.js`
- Test: `tests/live2d-application-options.test.mjs`

- [ ] **Step 1: Keep the web branch unchanged**

```javascript
return {
    x: stageWidth * 0.5,
    y: stageHeight * 0.62,
    scale: roundToTransform(Math.min(widthRatio, heightRatio) * 0.82),
};
```

- [ ] **Step 2: Run targeted checks**

Run: `node --test /Users/zytwd/Code/workflow/EchoBot/tests/live2d-default-transform.test.mjs /Users/zytwd/Code/workflow/EchoBot/tests/live2d-application-options.test.mjs`

Expected: PASS

- [ ] **Step 3: Run syntax checks for touched frontend files**

Run: `node --check /Users/zytwd/Code/workflow/EchoBot/echobot/app/web/features/live2d/default-transform.js`

Expected: no output

- [ ] **Step 4: Commit**

```bash
git add echobot/app/web/features/live2d/default-transform.js tests/live2d-default-transform.test.mjs
git commit -m "test: cover desktop head-first framing"
```
