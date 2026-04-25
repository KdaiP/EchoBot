import { DOM } from "../../../core/dom.js";
import {
    buildHotkeyKey,
    buildHotkeyMetaText,
    filterStandaloneHotkeys,
    findAttachedHotkeys,
    normalizeLive2DConfig,
} from "./common.js";

export function createLive2DControlsRenderer(deps) {
    const {
        getSelectionRuntimeState,
        isExpressionActive,
        persistence,
    } = deps;

    function renderLive2DControls(live2dConfig) {
        const config = normalizeLive2DConfig(live2dConfig);
        const runtimeState = getSelectionRuntimeState(config.selection_key);

        renderDiscoverySummary(config, runtimeState);
        renderExpressionList(config, runtimeState);
        renderMotionList(config, runtimeState);
        renderHotkeyList(config, runtimeState);
    }

    function renderDiscoverySummary(config, runtimeState) {
        const countsLabel = config.available
            ? `表情 ${config.expressions.length} 项 | 动作 ${config.motions.length} 项 | 热键 ${config.hotkeys.length} 项`
            : "模型不可用";
        const hint = buildRuntimeHint(config, runtimeState);

        if (DOM.live2dDiscoverySummary) {
            DOM.live2dDiscoverySummary.textContent = hint
                ? `${countsLabel} | ${hint}`
                : countsLabel;
        }
        if (DOM.live2dDrawerSummary) {
            DOM.live2dDrawerSummary.textContent = hint
                ? `${countsLabel} | ${hint}`
                : countsLabel;
        }
    }

    function renderExpressionList(config, runtimeState) {
        if (!DOM.live2dExpressionList) {
            return;
        }

        if (!config.available || config.expressions.length === 0) {
            replaceControlListChildren(
                DOM.live2dExpressionList,
                [buildEmptyState("未发现表情文件。")],
            );
            return;
        }

        replaceControlListChildren(
            DOM.live2dExpressionList,
            config.expressions.map((expressionItem) => (
                buildAnnotatableCard({
                    item: expressionItem,
                    selectionKey: config.selection_key,
                    kind: "expression",
                    actionLabel: "切换",
                    actionName: "trigger-expression",
                    noteEnabled: config.annotations_writable && runtimeState.canInteract,
                    actionEnabled: runtimeState.canInteract,
                    active: isExpressionActive(config.selection_key, expressionItem.file),
                    hotkeys: findAttachedHotkeys(config.hotkeys, "expression", expressionItem.file),
                    hotkeysWritable: config.annotations_writable && runtimeState.canInteract,
                })
            )),
        );
    }

    function renderMotionList(config, runtimeState) {
        if (!DOM.live2dMotionList) {
            return;
        }

        if (!config.available || config.motions.length === 0) {
            replaceControlListChildren(
                DOM.live2dMotionList,
                [buildEmptyState("未发现动作文件。")],
            );
            return;
        }

        replaceControlListChildren(
            DOM.live2dMotionList,
            config.motions.map((motionItem) => (
                buildAnnotatableCard({
                    item: motionItem,
                    selectionKey: config.selection_key,
                    kind: "motion",
                    actionLabel: "播放",
                    actionName: "play-motion",
                    noteEnabled: config.annotations_writable && runtimeState.canInteract,
                    actionEnabled: runtimeState.canInteract,
                    active: false,
                    hotkeys: findAttachedHotkeys(config.hotkeys, "motion", motionItem.file),
                    hotkeysWritable: config.annotations_writable && runtimeState.canInteract,
                })
            )),
        );
    }

    function renderHotkeyList(config, runtimeState) {
        if (!DOM.live2dHotkeyList) {
            return;
        }

        const standaloneHotkeys = filterStandaloneHotkeys(config.hotkeys);
        if (!config.available || standaloneHotkeys.length === 0) {
            replaceControlListChildren(
                DOM.live2dHotkeyList,
                [buildEmptyState("未发现独立热键。")],
            );
            return;
        }

        replaceControlListChildren(
            DOM.live2dHotkeyList,
            standaloneHotkeys.map((hotkeyItem) => (
                buildHotkeyCard(
                    hotkeyItem,
                    config.annotations_writable && runtimeState.canInteract,
                    config.selection_key,
                    runtimeState.canInteract,
                )
            )),
        );
    }

    function replaceControlListChildren(listElement, children) {
        listElement.replaceChildren(...children);
    }

    function buildRuntimeHint(config, runtimeState) {
        if (!config.available) {
            return "当前无法使用这些控制项。";
        }
        if (runtimeState.isLoading && runtimeState.isPendingSelection) {
            return "模型加载中，控制项暂时禁用。";
        }
        if (!runtimeState.canInteract) {
            return "运行时尚未就绪，控制项暂时禁用。";
        }
        return config.annotations_writable ? "" : "内置模型为只读。";
    }

    function buildAnnotatableCard(options) {
        const {
            item,
            selectionKey,
            kind,
            actionLabel,
            actionName,
            noteEnabled,
            actionEnabled,
            active,
            hotkeys,
            hotkeysWritable,
        } = options;
        const card = document.createElement("article");
        card.className = "live2d-control-card";
        card.dataset.live2dSelectionKey = selectionKey;
        card.dataset.live2dKind = kind;
        card.dataset.live2dFile = item.file;

        const header = document.createElement("div");
        header.className = "live2d-control-card-head";

        const title = document.createElement("h4");
        title.className = "live2d-control-card-title";
        title.textContent = item.name || item.file;
        header.appendChild(title);

        if (actionName === "trigger-expression") {
            header.appendChild(
                buildExpressionSwitch({
                    file: item.file,
                    active: active,
                    selectionKey: selectionKey,
                    title: item.name || item.file,
                    enabled: actionEnabled,
                }),
            );
        } else {
            const actionButton = document.createElement("button");
            actionButton.type = "button";
            actionButton.className = "ghost-button ghost-button-compact";
            actionButton.dataset.live2dAction = actionName;
            actionButton.dataset.live2dFile = item.file;
            actionButton.textContent = actionLabel;
            actionButton.disabled = !actionEnabled;
            header.appendChild(actionButton);
        }

        card.appendChild(header);

        const noteInput = document.createElement("textarea");
        noteInput.className = "live2d-note-input";
        noteInput.rows = 3;
        noteInput.value = persistence.readAnnotationDraftValue(selectionKey, kind, item.file, item.note || "");
        noteInput.placeholder = kind === "motion"
            ? "添加动作备注。"
            : "添加表情备注。";
        noteInput.disabled = !noteEnabled;
        noteInput.dataset.live2dSelectionKey = selectionKey;
        noteInput.dataset.live2dKind = kind;
        noteInput.dataset.live2dFile = item.file;
        card.appendChild(noteInput);

        if (Array.isArray(hotkeys) && hotkeys.length > 0) {
            const hotkeyList = document.createElement("div");
            hotkeyList.className = "live2d-attached-hotkey-list";
            hotkeys.forEach((hotkeyItem, index) => {
                hotkeyList.appendChild(
                    buildAttachedHotkeyEditor(hotkeyItem, hotkeysWritable, {
                        selectionKey: selectionKey,
                        title: hotkeys.length > 1
                            ? (hotkeyItem.name || `热键 ${index + 1}`)
                            : "热键",
                    }),
                );
            });
            card.appendChild(hotkeyList);
        }

        return card;
    }

    function buildExpressionSwitch({ file, active, selectionKey, title, enabled }) {
        const toggle = document.createElement("label");
        toggle.className = "live2d-switch";
        toggle.dataset.live2dAction = "trigger-expression";
        toggle.dataset.live2dFile = file;
        toggle.dataset.live2dSelectionKey = selectionKey;
        toggle.dataset.live2dDisabled = String(!enabled);

        const input = document.createElement("input");
        input.type = "checkbox";
        input.className = "live2d-switch-input";
        input.checked = active;
        input.disabled = !enabled;
        input.setAttribute("aria-label", `${active ? "Disable" : "Enable"} ${title}`);
        toggle.appendChild(input);

        const track = document.createElement("span");
        track.className = "live2d-switch-track";
        track.setAttribute("aria-hidden", "true");
        toggle.appendChild(track);

        return toggle;
    }

    function buildAttachedHotkeyEditor(hotkeyItem, hotkeysWritable, options = {}) {
        const wrapper = document.createElement("div");
        wrapper.className = "live2d-attached-hotkey";
        wrapper.dataset.live2dHotkeyKey = buildHotkeyKey(hotkeyItem);
        wrapper.dataset.live2dSelectionKey = String(options.selectionKey || "");

        const header = document.createElement("div");
        header.className = "live2d-attached-hotkey-head";

        const title = document.createElement("span");
        title.className = "live2d-attached-hotkey-title";
        title.textContent = String(options.title || "热键");
        header.appendChild(title);

        wrapper.appendChild(header);

        if (!hotkeyItem.supported) {
            const meta = document.createElement("p");
            meta.className = "live2d-control-card-meta";
            meta.textContent = "当前不支持这个热键动作。";
            wrapper.appendChild(meta);
        }

        if (hotkeysWritable) {
            wrapper.appendChild(
                buildHotkeyInputField({
                    hotkeyItem: hotkeyItem,
                    selectionKey: String(options.selectionKey || ""),
                    enabled: hotkeysWritable,
                }),
            );
        } else {
            wrapper.appendChild(buildHotkeyShortcutBadge(hotkeyItem));
        }

        return wrapper;
    }

    function buildHotkeyCard(hotkeyItem, hotkeysWritable, selectionKey, actionEnabled) {
        const card = document.createElement("article");
        card.className = "live2d-control-card";
        card.dataset.live2dHotkeyKey = buildHotkeyKey(hotkeyItem);
        card.dataset.live2dSelectionKey = selectionKey;

        const header = document.createElement("div");
        header.className = "live2d-control-card-head";

        const title = document.createElement("h4");
        title.className = "live2d-control-card-title";
        title.textContent = hotkeyItem.name || hotkeyItem.action || "热键";
        header.appendChild(title);

        const triggerButton = document.createElement("button");
        triggerButton.type = "button";
        triggerButton.className = "ghost-button ghost-button-compact";
        triggerButton.dataset.live2dAction = "trigger-hotkey";
        triggerButton.dataset.live2dHotkeyId = hotkeyItem.hotkey_id;
        triggerButton.dataset.live2dHotkeyKey = buildHotkeyKey(hotkeyItem);
        triggerButton.textContent = "执行";
        triggerButton.disabled = !hotkeyItem.supported || !actionEnabled;
        header.appendChild(triggerButton);

        card.appendChild(header);

        const metaText = buildHotkeyMetaText(hotkeyItem);
        if (metaText) {
            const meta = document.createElement("p");
            meta.className = "live2d-control-card-meta";
            meta.textContent = metaText;
            card.appendChild(meta);
        }

        if (hotkeysWritable) {
            card.appendChild(
                buildHotkeyInputField({
                    hotkeyItem: hotkeyItem,
                    selectionKey: selectionKey,
                    enabled: hotkeysWritable,
                }),
            );
        } else {
            card.appendChild(buildHotkeyShortcutBadge(hotkeyItem));
        }

        return card;
    }

    function buildHotkeyInputField({ hotkeyItem, selectionKey, enabled }) {
        const shell = document.createElement("div");
        shell.className = "live2d-hotkey-input-shell";

        const shortcutInput = document.createElement("input");
        shortcutInput.type = "text";
        shortcutInput.className = "live2d-hotkey-input";
        shortcutInput.autocomplete = "off";
        shortcutInput.readOnly = true;
        shortcutInput.placeholder = "聚焦后直接按下热键。";
        shortcutInput.disabled = !enabled;
        shortcutInput.dataset.live2dSelectionKey = selectionKey;
        shortcutInput.dataset.live2dHotkeyKey = buildHotkeyKey(hotkeyItem);
        shell.appendChild(shortcutInput);

        const clearButton = document.createElement("button");
        clearButton.type = "button";
        clearButton.className = "ghost-button live2d-hotkey-clear";
        clearButton.dataset.live2dAction = "reset-hotkey";
        clearButton.dataset.live2dHotkeyKey = buildHotkeyKey(hotkeyItem);
        clearButton.dataset.live2dSelectionKey = selectionKey;
        clearButton.textContent = "x";
        clearButton.disabled = !enabled;
        clearButton.setAttribute("aria-label", "恢复默认热键");
        clearButton.title = "恢复默认热键";
        shell.appendChild(clearButton);

        persistence.setHotkeyInputValue(
            shortcutInput,
            persistence.readHotkeyDraftValue(
                selectionKey,
                buildHotkeyKey(hotkeyItem),
                hotkeyItem.shortcut_tokens || [],
            ),
        );
        return shell;
    }

    function buildHotkeyShortcutBadge(hotkeyItem) {
        const shortcuts = document.createElement("div");
        shortcuts.className = "live2d-control-shortcuts";

        const badge = document.createElement("span");
        badge.className = "live2d-hotkey-badge";
        if (!Array.isArray(hotkeyItem.shortcut_tokens) || hotkeyItem.shortcut_tokens.length === 0) {
            badge.className += " live2d-hotkey-badge-unsassigned";
        }
        badge.textContent = hotkeyItem.shortcut_label || "未分配";
        shortcuts.appendChild(badge);

        return shortcuts;
    }

    function buildEmptyState(text) {
        const element = document.createElement("p");
        element.className = "live2d-control-empty";
        element.textContent = text;
        return element;
    }

    return {
        renderLive2DControls,
    };
}
