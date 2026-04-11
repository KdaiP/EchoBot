import { DEFAULT_LIP_SYNC_IDS, appState, live2dState } from "../../core/store.js";
import { isDesktopTransparentStageEnabled } from "./desktop-stage-mode.js";
import { resolveDefaultLive2DTransform } from "./default-transform.js";

export function createLive2DModelController(deps) {
    const {
        clamp,
        roundTo,
        readJson,
        removeStoredValue,
        setStageMessage,
        writeJson,
    } = deps;

    function normalizeSelectionKey(value) {
        return String(value || "").trim();
    }

    function selectionKeyFromConfig(live2dConfig) {
        return normalizeSelectionKey(
            live2dConfig && (live2dConfig.selection_key || live2dConfig.model_url),
        );
    }

    function markSelectionLoading(selectionKey) {
        live2dState.live2dLoading = true;
        live2dState.live2dPendingSelectionKey = normalizeSelectionKey(selectionKey);
        suspendCurrentModelInteractions();
    }

    function finishSelectionLoad(selectionKey) {
        const normalizedSelectionKey = normalizeSelectionKey(selectionKey);
        live2dState.live2dLoading = false;
        live2dState.live2dPendingSelectionKey = normalizedSelectionKey;
        live2dState.live2dActiveSelectionKey = normalizedSelectionKey;
    }

    function restoreSelectionState() {
        live2dState.live2dLoading = false;
        live2dState.live2dPendingSelectionKey = live2dState.live2dActiveSelectionKey;
        resumeCurrentModelInteractions();
    }

    function clearSelectionState() {
        live2dState.live2dLoading = false;
        live2dState.live2dPendingSelectionKey = "";
        live2dState.live2dActiveSelectionKey = "";
    }

    function getSelectionRuntimeState(selectionKey) {
        const normalizedSelectionKey = normalizeSelectionKey(selectionKey);
        const activeSelectionKey = live2dState.live2dActiveSelectionKey;
        const pendingSelectionKey = live2dState.live2dPendingSelectionKey;
        const hasModel = Boolean(live2dState.live2dModel);
        const isActiveSelection = Boolean(
            normalizedSelectionKey
            && normalizedSelectionKey === activeSelectionKey,
        );
        const isPendingSelection = Boolean(
            normalizedSelectionKey
            && normalizedSelectionKey === pendingSelectionKey,
        );

        return {
            isLoading: live2dState.live2dLoading,
            hasModel: hasModel,
            activeSelectionKey: activeSelectionKey,
            pendingSelectionKey: pendingSelectionKey,
            isActiveSelection: isActiveSelection,
            isPendingSelection: isPendingSelection,
            canInteract: Boolean(
                hasModel
                && !live2dState.live2dLoading
                && isActiveSelection,
            ),
        };
    }

    function resolveActionSelectionKey(selectionKey) {
        return normalizeSelectionKey(selectionKey)
            || selectionKeyFromConfig(appState.config && appState.config.live2d);
    }

    function assertSelectionReady(selectionKey) {
        if (!getSelectionRuntimeState(selectionKey).canInteract) {
            throw new Error("Live2D 模型尚未就绪。");
        }
    }

    async function loadLive2DModel(live2dConfig) {
        const selectionKey = selectionKeyFromConfig(live2dConfig);
        markSelectionLoading(selectionKey);
        if (!live2dConfig.available || !live2dConfig.model_url) {
            disposeCurrentLive2DModel();
            clearSelectionState();
            setStageMessage("未找到 Live2D 模型。请检查 .echobot/live2d 目录。");
            return false;
        }

        const loadToken = ++live2dState.live2dLoadToken;
        setStageMessage("正在加载 Live2D 模型…");

        try {
            const model = await window.PIXI.live2d.Live2DModel.from(live2dConfig.model_url, {
                autoInteract: false,
            });
            if (loadToken !== live2dState.live2dLoadToken) {
                destroyLive2DModel(model);
                return false;
            }

            disposeCurrentLive2DModel();
            live2dState.live2dModel = model;
            if (live2dState.live2dCharacterLayer) {
                live2dState.live2dCharacterLayer.addChild(model);
            } else {
                live2dState.live2dStage.addChild(model);
            }

            model.anchor.set(0.5, 0.5);
            model.cursor = "grab";
            model.interactive = true;

            applyLive2DMouseFollowSetting();
            bindLive2DDrag(model);
            attachLipSyncHook(model, live2dConfig);
            resetLive2DView();
            finishSelectionLoad(selectionKey);

            setStageMessage("");
            return true;
        } catch (error) {
            console.error(error);
            if (loadToken === live2dState.live2dLoadToken) {
                restoreSelectionState();
                setStageMessage(`模型加载失败：${error.message || error}`);
            }
            throw new Error(`加载 Live2D 模型失败：${error.message || error}`);
        }
    }

    function suspendCurrentModelInteractions() {
        unbindLive2DDrag();
        unbindLive2DFocus();

        if (!live2dState.live2dModel) {
            return;
        }

        live2dState.live2dModel.interactive = false;
        live2dState.live2dModel.cursor = "wait";
    }

    function resumeCurrentModelInteractions() {
        const model = live2dState.live2dModel;
        if (!model) {
            return;
        }

        model.interactive = true;
        model.cursor = "grab";
        applyLive2DMouseFollowSetting();
        bindLive2DDrag(model);
    }

    function bindLive2DDrag(model) {
        unbindLive2DDrag();

        const pointerDown = (event) => {
            const point = event.data.getLocalPosition(live2dState.live2dStage);
            live2dState.dragging = true;
            live2dState.dragPointerId = event.data.pointerId;
            live2dState.dragOffsetX = model.x - point.x;
            live2dState.dragOffsetY = model.y - point.y;
            model.cursor = "grabbing";
        };

        const pointerMove = (event) => {
            if (!live2dState.dragging || event.data.pointerId !== live2dState.dragPointerId) {
                return;
            }

            const point = event.data.getLocalPosition(live2dState.live2dStage);
            model.x = point.x + live2dState.dragOffsetX;
            model.y = point.y + live2dState.dragOffsetY;
            refreshLive2DFocusFromLastPointer();
            persistLive2DTransform();
        };

        const stopDragging = () => {
            if (!live2dState.dragging) {
                return;
            }

            live2dState.dragging = false;
            live2dState.dragPointerId = null;
            model.cursor = "grab";
            persistLive2DTransform();
        };

        model.on("pointerdown", pointerDown);
        live2dState.live2dStage.on("pointermove", pointerMove);
        live2dState.live2dStage.on("pointerup", stopDragging);
        live2dState.live2dStage.on("pointerupoutside", stopDragging);
        live2dState.live2dStage.on("pointerleave", stopDragging);

        live2dState.live2dDragModel = model;
        live2dState.live2dDragHandlers = {
            pointerDown: pointerDown,
            pointerMove: pointerMove,
            stopDragging: stopDragging,
        };
    }

    function unbindLive2DDrag() {
        if (!live2dState.live2dDragHandlers || !live2dState.live2dStage) {
            return;
        }

        if (live2dState.live2dDragModel && typeof live2dState.live2dDragModel.off === "function") {
            live2dState.live2dDragModel.off("pointerdown", live2dState.live2dDragHandlers.pointerDown);
        }

        live2dState.live2dStage.off("pointermove", live2dState.live2dDragHandlers.pointerMove);
        live2dState.live2dStage.off("pointerup", live2dState.live2dDragHandlers.stopDragging);
        live2dState.live2dStage.off("pointerupoutside", live2dState.live2dDragHandlers.stopDragging);
        live2dState.live2dStage.off("pointerleave", live2dState.live2dDragHandlers.stopDragging);

        live2dState.live2dDragModel = null;
        live2dState.live2dDragHandlers = null;
    }

    function bindLive2DFocus() {
        unbindLive2DFocus();

        if (!live2dState.live2dStage) {
            return;
        }

        const pointerMove = (event) => {
            const globalPoint = event && event.data ? event.data.global : null;
            if (!globalPoint) {
                return;
            }

            live2dState.live2dLastPointerX = globalPoint.x;
            live2dState.live2dLastPointerY = globalPoint.y;
            updateLive2DFocusFromGlobalPoint(globalPoint.x, globalPoint.y);
        };

        live2dState.live2dStage.on("pointermove", pointerMove);
        live2dState.live2dFocusHandlers = {
            pointerMove: pointerMove,
        };
        refreshLive2DFocusFromLastPointer();
    }

    function unbindLive2DFocus() {
        if (!live2dState.live2dFocusHandlers || !live2dState.live2dStage) {
            return;
        }

        live2dState.live2dStage.off("pointermove", live2dState.live2dFocusHandlers.pointerMove);
        live2dState.live2dFocusHandlers = null;
    }

    function refreshLive2DFocusFromLastPointer() {
        if (
            !live2dState.live2dMouseFollowEnabled
            || !Number.isFinite(live2dState.live2dLastPointerX)
            || !Number.isFinite(live2dState.live2dLastPointerY)
        ) {
            return;
        }

        updateLive2DFocusFromGlobalPoint(
            live2dState.live2dLastPointerX,
            live2dState.live2dLastPointerY,
        );
    }

    function applyExternalFocusPoint(globalX, globalY) {
        if (
            !Number.isFinite(globalX)
            || !Number.isFinite(globalY)
        ) {
            return;
        }

        live2dState.live2dLastPointerX = globalX;
        live2dState.live2dLastPointerY = globalY;
        refreshLive2DFocusFromLastPointer();
    }

    function updateLive2DFocusFromGlobalPoint(globalX, globalY) {
        const model = live2dState.live2dModel;
        const internalModel = model && model.internalModel;
        if (
            !model
            || !internalModel
            || !internalModel.focusController
            || typeof internalModel.focusController.focus !== "function"
        ) {
            return;
        }

        const localPoint = toLive2DModelPoint(model, globalX, globalY);
        if (!localPoint) {
            return;
        }

        const rawFocusX = normalizeLive2DFocusAxis(
            localPoint.x,
            0,
            internalModel.originalWidth,
        );
        const visibleVerticalBounds = resolveVisibleLive2DVerticalBounds(model);
        const rawFocusY = visibleVerticalBounds
            ? normalizeLive2DFocusAxis(
                localPoint.y,
                visibleVerticalBounds.top,
                visibleVerticalBounds.bottom,
            )
            : normalizeLive2DFocusAxis(
                localPoint.y,
                0,
                internalModel.originalHeight,
            );

        applyLive2DFocusTarget(
            internalModel.focusController,
            rawFocusX,
            rawFocusY,
        );
    }

    function toLive2DModelPoint(model, globalX, globalY) {
        if (
            !window.PIXI
            || typeof window.PIXI.Point !== "function"
            || typeof model.toModelPosition !== "function"
        ) {
            return null;
        }

        const globalPoint = new window.PIXI.Point(globalX, globalY);
        return model.toModelPosition(globalPoint, new window.PIXI.Point());
    }

    function resolveVisibleLive2DVerticalBounds(model) {
        if (!live2dState.pixiApp || typeof model.getBounds !== "function") {
            return null;
        }

        const modelBounds = model.getBounds();
        const screen = live2dState.pixiApp.screen;
        if (
            !modelBounds
            || modelBounds.width <= 0
            || modelBounds.height <= 0
            || screen.width <= 0
            || screen.height <= 0
        ) {
            return null;
        }

        const visibleLeft = Math.max(modelBounds.x, screen.x);
        const visibleTop = Math.max(modelBounds.y, screen.y);
        const visibleRight = Math.min(
            modelBounds.x + modelBounds.width,
            screen.x + screen.width,
        );
        const visibleBottom = Math.min(
            modelBounds.y + modelBounds.height,
            screen.y + screen.height,
        );

        if (visibleRight <= visibleLeft || visibleBottom <= visibleTop) {
            return null;
        }

        const topPoint = toLive2DModelPoint(model, visibleLeft, visibleTop);
        const bottomPoint = toLive2DModelPoint(model, visibleLeft, visibleBottom);
        if (!topPoint || !bottomPoint) {
            return null;
        }

        const top = Math.min(topPoint.y, bottomPoint.y);
        const bottom = Math.max(topPoint.y, bottomPoint.y);
        if (bottom - top <= 0.0001) {
            return null;
        }

        return {
            top: top,
            bottom: bottom,
        };
    }

    function normalizeLive2DFocusAxis(value, min, max) {
        const span = max - min;
        if (!Number.isFinite(span) || Math.abs(span) <= 0.0001) {
            return 0;
        }

        return clamp(((value - min) / span) * 2 - 1, -1, 1);
    }

    function applyLive2DFocusTarget(focusController, rawX, rawY) {
        const distance = Math.hypot(rawX, rawY);
        if (!Number.isFinite(distance) || distance <= 0.0001) {
            focusController.focus(0, 0);
            return;
        }

        focusController.focus(rawX / distance, -rawY / distance);
    }

    function attachLipSyncHook(model, live2dConfig) {
        detachLive2DLipSyncHook();

        const internalModel = model.internalModel;
        if (!internalModel || typeof internalModel.on !== "function") {
            return;
        }

        live2dState.lipSyncHook = function () {
            applyMouthValue(live2dConfig, live2dState.currentMouthValue);
            applyActiveExpressions();
        };
        internalModel.on("beforeModelUpdate", live2dState.lipSyncHook);
        live2dState.live2dInternalModel = internalModel;
    }

    function applyLive2DMouseFollowSetting() {
        const model = live2dState.live2dModel;
        if (!model) {
            return;
        }

        model.interactive = true;
        model.autoInteract = false;
        if (typeof model.unregisterInteraction === "function") {
            model.unregisterInteraction();
        }

        if (!live2dState.live2dMouseFollowEnabled) {
            unbindLive2DFocus();
            resetLive2DFocus();
            return;
        }

        bindLive2DFocus();
    }

    function resetLive2DFocus() {
        const internalModel = live2dState.live2dModel && live2dState.live2dModel.internalModel;
        if (
            !internalModel
            || !internalModel.focusController
            || typeof internalModel.focusController.focus !== "function"
        ) {
            return;
        }

        internalModel.focusController.focus(0, 0, true);
    }

    function detachLive2DLipSyncHook() {
        if (
            live2dState.live2dInternalModel
            && live2dState.lipSyncHook
            && typeof live2dState.live2dInternalModel.off === "function"
        ) {
            live2dState.live2dInternalModel.off("beforeModelUpdate", live2dState.lipSyncHook);
        }

        live2dState.live2dInternalModel = null;
        live2dState.lipSyncHook = null;
    }

    function disposeCurrentLive2DModel() {
        unbindLive2DDrag();
        unbindLive2DFocus();
        detachLive2DLipSyncHook();
        clearActiveExpressions();

        if (live2dState.live2dCharacterLayer) {
            live2dState.live2dCharacterLayer.removeChildren();
        } else if (live2dState.live2dStage) {
            live2dState.live2dStage.removeChildren();
        }

        if (live2dState.live2dModel) {
            destroyLive2DModel(live2dState.live2dModel);
        }

        live2dState.live2dModel = null;
        live2dState.live2dInternalModel = null;
        live2dState.dragging = false;
        live2dState.dragPointerId = null;
    }

    function destroyLive2DModel(model) {
        if (!model || typeof model.destroy !== "function") {
            return;
        }

        try {
            model.destroy({
                children: true,
            });
        } catch (error) {
            console.warn("Failed to destroy Live2D model", error);
        }
    }

    function handleStageWheel(event) {
        if (live2dState.live2dLoading) {
            return;
        }

        if (!live2dState.live2dModel) {
            return;
        }

        if (shouldIgnoreStageWheel(event)) {
            return;
        }

        event.preventDefault();
        const scaleStep = event.deltaY < 0 ? 1.06 : 0.94;
        const nextScale = clamp(
            live2dState.live2dModel.scale.x * scaleStep,
            0.08,
            3.2,
        );
        live2dState.live2dModel.scale.set(nextScale);
        refreshLive2DFocusFromLastPointer();
        persistLive2DTransform();
    }

    function shouldIgnoreStageWheel(event) {
        const target = event && event.target;
        if (!target || typeof target.closest !== "function") {
            return false;
        }

        return Boolean(target.closest("#live2d-drawer, #live2d-drawer-backdrop"));
    }

    function resetLive2DView() {
        const model = live2dState.live2dModel;
        if (!model || !live2dState.pixiApp) {
            return;
        }

        const savedTransform = loadSavedLive2DTransform();
        if (savedTransform) {
            model.position.set(savedTransform.x, savedTransform.y);
            model.scale.set(savedTransform.scale);
            refreshLive2DFocusFromLastPointer();
            return;
        }

        applyDefaultLive2DTransform(model);
        refreshLive2DFocusFromLastPointer();
        persistLive2DTransform();
    }

    function resetLive2DViewToDefault() {
        if (live2dState.live2dLoading) {
            return;
        }

        const model = live2dState.live2dModel;
        if (!model || !live2dState.pixiApp) {
            return;
        }

        clearSavedLive2DTransform();
        applyDefaultLive2DTransform(model);
        refreshLive2DFocusFromLastPointer();
        persistLive2DTransform();
    }

    function applyDefaultLive2DTransform(model) {
        const stageWidth = live2dState.pixiApp.screen.width;
        const stageHeight = live2dState.pixiApp.screen.height;
        const baseSize = measureLive2DBaseSize(model);
        const defaultTransform = resolveDefaultLive2DTransform({
            stageWidth: stageWidth,
            stageHeight: stageHeight,
            baseWidth: baseSize.width,
            baseHeight: baseSize.height,
            desktopTransparentStage: isDesktopTransparentStageEnabled(
                document.getElementById("desktop-stage"),
            ),
        });

        model.scale.set(defaultTransform.scale);
        model.position.set(defaultTransform.x, defaultTransform.y);
    }

    function measureLive2DBaseSize(model) {
        if (typeof model.getLocalBounds === "function") {
            const bounds = model.getLocalBounds();
            if (bounds && bounds.width > 0 && bounds.height > 0) {
                return {
                    width: bounds.width,
                    height: bounds.height,
                };
            }
        }

        const scaleX = Math.max(Math.abs(model.scale.x) || 0, 0.0001);
        const scaleY = Math.max(Math.abs(model.scale.y) || 0, 0.0001);
        return {
            width: model.width / scaleX,
            height: model.height / scaleY,
        };
    }

    function persistLive2DTransform() {
        const model = live2dState.live2dModel;
        if (!model) {
            return;
        }

        writeJson(live2dStorageKey(), {
            x: roundTo(model.x, 2),
            y: roundTo(model.y, 2),
            scale: roundTo(model.scale.x, 4),
        });
    }

    function loadSavedLive2DTransform() {
        const payload = readJson(live2dStorageKey());
        if (
            payload
            && typeof payload.x === "number"
            && typeof payload.y === "number"
            && typeof payload.scale === "number"
        ) {
            return payload;
        }

        return null;
    }

    function clearSavedLive2DTransform() {
        removeStoredValue(live2dStorageKey());
    }

    function live2dStorageKey() {
        const selectionKey = appState.config && appState.config.live2d
            ? (appState.config.live2d.selection_key || appState.config.live2d.model_url)
            : "default";
        return `echobot.web.live2d.${selectionKey}`;
    }

    function applyMouthValue(live2dConfig, value) {
        if (!live2dConfig || !live2dState.live2dModel || !live2dState.live2dModel.internalModel) {
            return;
        }

        const coreModel = live2dState.live2dModel.internalModel.coreModel;
        if (!coreModel || typeof coreModel.setParameterValueById !== "function") {
            return;
        }

        const lipSyncIds = (live2dConfig.lip_sync_parameter_ids || []).length > 0
            ? live2dConfig.lip_sync_parameter_ids
            : DEFAULT_LIP_SYNC_IDS;

        lipSyncIds.forEach((parameterId) => {
            try {
                coreModel.setParameterValueById(parameterId, value);
            } catch (error) {
                console.warn(`Failed to update lip sync parameter ${parameterId}`, error);
            }
        });

        if (live2dConfig.mouth_form_parameter_id) {
            try {
                coreModel.setParameterValueById(live2dConfig.mouth_form_parameter_id, 0);
            } catch (error) {
                console.warn("Failed to reset mouth form parameter", error);
            }
        }
    }

    function applyActiveExpressions() {
        const model = live2dState.live2dModel;
        const internalModel = model && model.internalModel;
        const coreModel = internalModel && internalModel.coreModel;
        if (
            !coreModel
            || typeof coreModel.setParameterValueById !== "function"
            || live2dState.activeExpressionMap.size === 0
        ) {
            return;
        }

        live2dState.activeExpressionMap.forEach((expressionDefinition) => {
            expressionDefinition.parameters.forEach((parameter) => {
                try {
                    if (parameter.blend === "Add" && typeof coreModel.addParameterValueById === "function") {
                        coreModel.addParameterValueById(parameter.id, parameter.value);
                        return;
                    }
                    if (
                        parameter.blend === "Multiply"
                        && typeof coreModel.multiplyParameterValueById === "function"
                    ) {
                        coreModel.multiplyParameterValueById(parameter.id, parameter.value);
                        return;
                    }
                    coreModel.setParameterValueById(parameter.id, parameter.value);
                } catch (error) {
                    console.warn(`Failed to apply Live2D expression parameter ${parameter.id}`, error);
                }
            });
        });
    }

    async function toggleExpression(expressionItem, selectionKey = "") {
        const normalizedSelectionKey = resolveActionSelectionKey(selectionKey);
        const normalizedItem = normalizeExpressionItem(expressionItem);
        if (!normalizedItem) {
            throw new Error("无效的 Live2D 表情。");
        }
        assertSelectionReady(normalizedSelectionKey);

        if (live2dState.activeExpressionMap.has(normalizedItem.file)) {
            live2dState.activeExpressionMap.delete(normalizedItem.file);
            syncActiveExpressionFiles();
            return {
                active: false,
                name: normalizedItem.name,
                file: normalizedItem.file,
            };
        }

        const expressionDefinition = await loadExpressionDefinition(
            normalizedItem,
            normalizedSelectionKey,
        );
        assertSelectionReady(normalizedSelectionKey);
        live2dState.activeExpressionMap.set(normalizedItem.file, expressionDefinition);
        syncActiveExpressionFiles();
        return {
            active: true,
            name: normalizedItem.name,
            file: normalizedItem.file,
        };
    }

    function clearActiveExpressions() {
        live2dState.activeExpressionMap.clear();
        syncActiveExpressionFiles();
    }

    async function playMotion(motionItem, selectionKey = "") {
        const normalizedSelectionKey = resolveActionSelectionKey(selectionKey);
        const model = live2dState.live2dModel;
        const normalizedItem = normalizeMotionItem(motionItem);
        if (!model || !normalizedItem) {
            throw new Error("无效的 Live2D 动作。");
        }
        assertSelectionReady(normalizedSelectionKey);
        if (typeof model.motion !== "function") {
            throw new Error("当前 Live2D 运行时不支持动作播放。");
        }

        await model.motion(normalizedItem.group, normalizedItem.index);
        return {
            name: normalizedItem.name,
            file: normalizedItem.file,
        };
    }

    async function triggerHotkey(hotkeyItem, live2dConfig) {
        const selectionKey = selectionKeyFromConfig(live2dConfig);
        const normalizedHotkey = normalizeHotkeyItem(hotkeyItem);
        if (!normalizedHotkey || !normalizedHotkey.supported) {
            throw new Error("不支持的 Live2D 热键。");
        }

        if (normalizedHotkey.action === "ToggleExpression") {
            const expressionItem = (live2dConfig && live2dConfig.expressions || []).find(
                (item) => item.file === normalizedHotkey.file,
            );
            if (!expressionItem) {
                throw new Error(`未找到表情：${normalizedHotkey.file}`);
            }
            const result = await toggleExpression(expressionItem, selectionKey);
            return {
                hotkey: normalizedHotkey,
                result: result,
            };
        }

        if (normalizedHotkey.action === "TriggerAnimation") {
            const motionItem = (live2dConfig && live2dConfig.motions || []).find(
                (item) => item.file === normalizedHotkey.file,
            );
            if (!motionItem) {
                throw new Error(`未找到动作：${normalizedHotkey.file}`);
            }
            const result = await playMotion(motionItem, selectionKey);
            return {
                hotkey: normalizedHotkey,
                result: result,
            };
        }

        if (normalizedHotkey.action === "RemoveAllExpressions") {
            assertSelectionReady(selectionKey);
            clearActiveExpressions();
            return {
                hotkey: normalizedHotkey,
                result: {
                    cleared: true,
                },
            };
        }

        throw new Error(`不支持的 Live2D 热键动作：${normalizedHotkey.action}`);
    }

    function isExpressionActive(selectionKey, file) {
        if (!getSelectionRuntimeState(selectionKey).canInteract) {
            return false;
        }

        return live2dState.activeExpressionMap.has(String(file || ""));
    }

    function syncActiveExpressionFiles() {
        live2dState.activeExpressionFiles = Array.from(live2dState.activeExpressionMap.keys());
    }

    function normalizeExpressionItem(expressionItem) {
        if (!expressionItem || typeof expressionItem !== "object") {
            return null;
        }
        const file = String(expressionItem.file || "");
        const url = String(expressionItem.url || "");
        if (!file || !url) {
            return null;
        }
        return {
            name: String(expressionItem.name || file),
            file: file,
            url: url,
        };
    }

    function normalizeMotionItem(motionItem) {
        if (!motionItem || typeof motionItem !== "object") {
            return null;
        }
        const file = String(motionItem.file || "");
        const group = String(motionItem.group || "");
        const index = Number.isInteger(motionItem.index) ? motionItem.index : 0;
        if (!file || !group) {
            return null;
        }
        return {
            name: String(motionItem.name || file),
            file: file,
            group: group,
            index: index,
        };
    }

    function normalizeHotkeyItem(hotkeyItem) {
        if (!hotkeyItem || typeof hotkeyItem !== "object") {
            return null;
        }
        return {
            hotkey_id: String(hotkeyItem.hotkey_id || ""),
            name: String(hotkeyItem.name || hotkeyItem.action || "Hotkey"),
            action: String(hotkeyItem.action || ""),
            file: String(hotkeyItem.file || ""),
            supported: Boolean(hotkeyItem.supported),
        };
    }

    async function loadExpressionDefinition(expressionItem, selectionKey) {
        assertSelectionReady(selectionKey);

        const cacheKey = `${normalizeSelectionKey(selectionKey)}::${expressionItem.url}`;
        if (live2dState.expressionDataCache.has(cacheKey)) {
            return live2dState.expressionDataCache.get(cacheKey);
        }

        const response = await fetch(expressionItem.url, {
            cache: "no-store",
        });
        if (!response.ok) {
            throw new Error(`加载表情失败：${expressionItem.name}`);
        }

        const payload = await response.json();
        assertSelectionReady(selectionKey);
        const parameters = Array.isArray(payload && payload.Parameters)
            ? payload.Parameters
                .filter((item) => item && typeof item === "object")
                .map((item) => ({
                    id: String(item.Id || ""),
                    value: typeof item.Value === "number" ? item.Value : 0,
                    blend: normalizeExpressionBlend(item.Blend),
                }))
                .filter((item) => item.id)
            : [];

        const expressionDefinition = {
            name: expressionItem.name,
            file: expressionItem.file,
            parameters: parameters,
        };
        live2dState.expressionDataCache.set(cacheKey, expressionDefinition);
        return expressionDefinition;
    }

    function normalizeExpressionBlend(blend) {
        const normalizedBlend = String(blend || "").trim().toLowerCase();
        if (normalizedBlend === "add") {
            return "Add";
        }
        if (normalizedBlend === "multiply") {
            return "Multiply";
        }
        return "Set";
    }

    return {
        applyLive2DMouseFollowSetting,
        applyMouthValue,
        clearActiveExpressions,
        getSelectionRuntimeState,
        handleStageWheel,
        loadLive2DModel,
        isExpressionActive,
        playMotion,
        applyExternalFocusPoint,
        refreshLive2DFocusFromLastPointer,
        resetLive2DViewToDefault,
        toggleExpression,
        triggerHotkey,
    };
}
