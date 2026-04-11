# Desktop Drag Hover Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the desktop drag button the same hover float and stronger border feedback as the other toolbar buttons.

**Architecture:** Keep the current drag button DOM structure and update only the CSS for its visual layer. The transparent drag hotspot remains unchanged.

**Tech Stack:** HTML, CSS

---

### Task 1: Align drag button hover feedback with the toolbar buttons

**Files:**
- Modify: `echobot/app/web/styles/desktop.css`

- [ ] **Step 1: Update the drag visual transition**

```css
.desktop-drag-visual {
    position: relative;
    z-index: 2;
    pointer-events: none;
    transition:
        transform 120ms ease,
        background 120ms ease,
        border-color 120ms ease,
        color 120ms ease,
        box-shadow 120ms ease;
}
```

- [ ] **Step 2: Add hover and active feedback**

```css
.desktop-drag-control:hover .desktop-drag-visual {
    transform: translateY(-1px);
    border-color: rgba(169, 101, 55, 0.22);
    color: rgba(89, 63, 46, 0.56);
    box-shadow: 0 16px 32px rgba(61, 33, 18, 0.1);
}

.desktop-drag-control:active .desktop-drag-visual {
    transform: translateY(0);
}
```

- [ ] **Step 3: Review the final stylesheet diff**

Run: `git diff -- echobot/app/web/styles/desktop.css`

Expected: only the drag button hover/active styling changes
