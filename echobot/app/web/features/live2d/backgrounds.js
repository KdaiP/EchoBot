import { DOM } from "../../core/dom.js";
import { appState, live2dState } from "../../core/store.js";
import {
    readJson,
    readString,
    removeStoredValue,
    writeJson,
    writeString,
} from "../../core/storage.js";
import {
    DEFAULT_STAGE_BACKGROUND_TRANSFORM,
    DEFAULT_STAGE_EFFECT_SETTINGS,
    STAGE_BACKGROUND_STORAGE_KEY,
} from "./constants.js";

const STAGE_BACKGROUND_TRANSFORM_SAVE_DELAY_MS = 160;

export function createStageBackgroundController(deps) {
    const {
        clamp,
        roundTo,
        responseToError,
        setRunStatus,
        applyStageEffectsToRuntime,
    } = deps;

    let transformSaveTimerId = 0;
    let pendingTransformSave = null;

    function normalizeStageConfig(stageConfig) {
        const backgrounds = Array.isArray(stageConfig && stageConfig.backgrounds)
            ? stageConfig.backgrounds
                .map((item) => ({
                    key: String((item && item.key) || "").trim(),
                    label: String((item && item.label) || "").trim(),
                    url: String((item && item.url) || "").trim(),
                    kind: String((item && item.kind) || "uploaded").trim() || "uploaded",
                }))
                .filter((item) => item.key)
            : [];

        const defaultBackgroundKey = String(
            (stageConfig && stageConfig.default_background_key) || "default",
        ).trim() || "default";

        if (!backgrounds.some((item) => item.key === defaultBackgroundKey)) {
            backgrounds.unshift({
                key: defaultBackgroundKey,
                label: "不使用背景",
                url: "",
                kind: "none",
            });
        }

        return {
            default_background_key: defaultBackgroundKey,
            backgrounds: backgrounds,
        };
    }

    function loadSavedStageBackgroundKey() {
        return readString(STAGE_BACKGROUND_STORAGE_KEY).trim();
    }

    function persistStageBackgroundKey(backgroundKey) {
        writeString(
            STAGE_BACKGROUND_STORAGE_KEY,
            String(backgroundKey || "default"),
        );
    }

    function normalizeStageBackgroundTransform(transform) {
        const positionX = Number.parseFloat(String(transform && transform.positionX));
        const positionY = Number.parseFloat(String(transform && transform.positionY));
        const scale = Number.parseFloat(String(transform && transform.scale));

        return {
            positionX: roundTo(
                clamp(
                    Number.isFinite(positionX)
                        ? positionX
                        : DEFAULT_STAGE_BACKGROUND_TRANSFORM.positionX,
                    0,
                    100,
                ),
                0,
            ),
            positionY: roundTo(
                clamp(
                    Number.isFinite(positionY)
                        ? positionY
                        : DEFAULT_STAGE_BACKGROUND_TRANSFORM.positionY,
                    0,
                    100,
                ),
                0,
            ),
            scale: roundTo(
                clamp(
                    Number.isFinite(scale)
                        ? scale
                        : DEFAULT_STAGE_BACKGROUND_TRANSFORM.scale,
                    60,
                    200,
                ),
                0,
            ),
        };
    }

    function stageBackgroundTransformStorageKey(backgroundKey) {
        return `echobot.web.stage.background.transform.${String(backgroundKey || "default").trim() || "default"}`;
    }

    function loadSavedStageBackgroundTransform(backgroundKey) {
        const payload = readJson(stageBackgroundTransformStorageKey(backgroundKey));
        if (!payload) {
            return null;
        }

        try {
            return normalizeStageBackgroundTransform(payload);
        } catch (error) {
            console.warn("Failed to read saved stage background transform", error);
            return null;
        }
    }

    function persistStageBackgroundTransform(backgroundKey, transform) {
        writeJson(
            stageBackgroundTransformStorageKey(backgroundKey),
            normalizeStageBackgroundTransform(transform),
        );
    }

    function scheduleStageBackgroundTransformPersist(backgroundKey, transform, options = {}) {
        pendingTransformSave = {
            backgroundKey: String(backgroundKey || "default"),
            transform: normalizeStageBackgroundTransform(transform),
        };

        if (transformSaveTimerId) {
            window.clearTimeout(transformSaveTimerId);
            transformSaveTimerId = 0;
        }

        if (options.immediate) {
            flushStageBackgroundTransformPersist();
            return;
        }

        transformSaveTimerId = window.setTimeout(() => {
            transformSaveTimerId = 0;
            flushStageBackgroundTransformPersist();
        }, STAGE_BACKGROUND_TRANSFORM_SAVE_DELAY_MS);
    }

    function flushStageBackgroundTransformPersist() {
        if (transformSaveTimerId) {
            window.clearTimeout(transformSaveTimerId);
            transformSaveTimerId = 0;
        }

        if (!pendingTransformSave) {
            return;
        }

        persistStageBackgroundTransform(
            pendingTransformSave.backgroundKey,
            pendingTransformSave.transform,
        );
        pendingTransformSave = null;
    }

    function cancelPendingStageBackgroundTransform(backgroundKey) {
        if (!pendingTransformSave || pendingTransformSave.backgroundKey !== backgroundKey) {
            return;
        }

        if (transformSaveTimerId) {
            window.clearTimeout(transformSaveTimerId);
            transformSaveTimerId = 0;
        }
        pendingTransformSave = null;
    }

    function clearSavedStageBackgroundTransform(backgroundKey) {
        cancelPendingStageBackgroundTransform(backgroundKey);
        removeStoredValue(stageBackgroundTransformStorageKey(backgroundKey));
    }

    function resolveInitialStageBackgroundKey(stageConfig) {
        const savedKey = loadSavedStageBackgroundKey();
        if (findStageBackgroundOption(stageConfig, savedKey)) {
            return savedKey;
        }
        return stageConfig.default_background_key || "default";
    }

    function findStageBackgroundOption(stageConfig, backgroundKey) {
        const normalizedKey = String(backgroundKey || "").trim();
        if (!normalizedKey || !stageConfig || !Array.isArray(stageConfig.backgrounds)) {
            return null;
        }
        return stageConfig.backgrounds.find((item) => item.key === normalizedKey) || null;
    }

    function currentStageBackgroundOption() {
        if (!appState.config || !appState.config.stage) {
            return null;
        }

        return findStageBackgroundOption(
            appState.config.stage,
            live2dState.selectedStageBackgroundKey,
        );
    }

    function resolveStageBackgroundTransform(backgroundOption) {
        if (!backgroundOption || !backgroundOption.url) {
            return {
                ...DEFAULT_STAGE_BACKGROUND_TRANSFORM,
            };
        }

        return loadSavedStageBackgroundTransform(backgroundOption.key) || {
            ...DEFAULT_STAGE_BACKGROUND_TRANSFORM,
        };
    }

    function renderStageBackgroundOptions(stageConfig, selectedKey) {
        if (!DOM.stageBackgroundSelect) {
            return;
        }

        const backgrounds = Array.isArray(stageConfig && stageConfig.backgrounds)
            ? stageConfig.backgrounds
            : [];
        DOM.stageBackgroundSelect.innerHTML = "";

        backgrounds.forEach((background) => {
            const option = document.createElement("option");
            option.value = background.key;
            option.textContent = background.label || background.key;
            DOM.stageBackgroundSelect.appendChild(option);
        });

        if (backgrounds.length === 0) {
            const option = document.createElement("option");
            option.value = "default";
            option.textContent = "不使用背景";
            DOM.stageBackgroundSelect.appendChild(option);
        }

        DOM.stageBackgroundSelect.value = selectedKey || stageConfig.default_background_key || "default";
    }

    function applyStageBackgroundByKey(stageConfig, backgroundKey) {
        flushStageBackgroundTransformPersist();

        const selectedOption = findStageBackgroundOption(stageConfig, backgroundKey)
            || findStageBackgroundOption(stageConfig, stageConfig.default_background_key)
            || null;
        const nextKey = selectedOption ? selectedOption.key : (stageConfig.default_background_key || "default");
        const nextTransform = resolveStageBackgroundTransform(selectedOption);

        live2dState.selectedStageBackgroundKey = nextKey;
        live2dState.currentStageBackgroundTransform = nextTransform;
        persistStageBackgroundKey(nextKey);
        renderStageBackgroundOptions(stageConfig, nextKey);
        applyStageBackgroundOption(selectedOption, nextTransform);
        syncStageBackgroundTransformInputs(selectedOption, nextTransform);
        updateStageBackgroundDetail(selectedOption, nextTransform);
        updateStageBackgroundControls();
    }

    function calculateStageBackgroundMetrics(normalizedTransform) {
        if (!DOM.stageElement) {
            return null;
        }

        const containerWidth = DOM.stageElement.offsetWidth;
        const containerHeight = DOM.stageElement.offsetHeight;
        if (containerWidth <= 0 || containerHeight <= 0) {
            return null;
        }

        const naturalSize = live2dState.currentBackgroundImageNaturalSize;
        if (!naturalSize || naturalSize.w <= 0 || naturalSize.h <= 0) {
            return {
                containerW: containerWidth,
                containerH: containerHeight,
                bgW: containerWidth,
                bgH: containerHeight,
                offsetX: 0,
                offsetY: 0,
            };
        }

        const coverFactor = Math.max(containerWidth / naturalSize.w, containerHeight / naturalSize.h);
        const scaleFactor = normalizedTransform.scale / 100;
        const bgWidth = Math.round(naturalSize.w * coverFactor * scaleFactor);
        const bgHeight = Math.round(naturalSize.h * coverFactor * scaleFactor);

        return {
            containerW: containerWidth,
            containerH: containerHeight,
            bgW: bgWidth,
            bgH: bgHeight,
            offsetX: Math.round((containerWidth - bgWidth) * (normalizedTransform.positionX / 100)),
            offsetY: Math.round((containerHeight - bgHeight) * (normalizedTransform.positionY / 100)),
        };
    }

    function applyDomStageBackgroundTransform(normalizedTransform) {
        if (!DOM.stageElement) {
            return;
        }

        const metrics = calculateStageBackgroundMetrics(normalizedTransform);
        if (metrics) {
            DOM.stageElement.style.setProperty(
                "--stage-background-size",
                `${metrics.bgW}px ${metrics.bgH}px`,
            );
        } else {
            DOM.stageElement.style.removeProperty("--stage-background-size");
        }

        DOM.stageElement.style.setProperty(
            "--stage-background-position-x",
            `${normalizedTransform.positionX}%`,
        );
        DOM.stageElement.style.setProperty(
            "--stage-background-position-y",
            `${normalizedTransform.positionY}%`,
        );
    }

    function updateStageBackgroundSpriteTransform(normalizedTransform) {
        if (!live2dState.stageBackgroundSprite) {
            return;
        }

        const metrics = calculateStageBackgroundMetrics(normalizedTransform);
        if (!metrics) {
            return;
        }

        live2dState.stageBackgroundSprite.position.set(metrics.offsetX, metrics.offsetY);
        live2dState.stageBackgroundSprite.width = metrics.bgW;
        live2dState.stageBackgroundSprite.height = metrics.bgH;
    }

    function applyStageBackgroundTransform(transform) {
        const normalizedTransform = normalizeStageBackgroundTransform(transform);
        applyDomStageBackgroundTransform(normalizedTransform);
        updateStageBackgroundSpriteTransform(normalizedTransform);
    }

    function clearStageBackgroundTransformStyles() {
        if (!DOM.stageElement) {
            return;
        }

        DOM.stageElement.style.removeProperty("--stage-background-position-x");
        DOM.stageElement.style.removeProperty("--stage-background-position-y");
        DOM.stageElement.style.removeProperty("--stage-background-size");
    }

    function applyDomStageBackgroundOption(backgroundOption, transform) {
        if (!DOM.stageElement || !DOM.stageBackgroundImage) {
            return;
        }

        const url = backgroundOption ? String(backgroundOption.url || "").trim() : "";
        if (!url) {
            DOM.stageBackgroundImage.hidden = true;
            DOM.stageBackgroundImage.style.backgroundImage = "";
            clearStageBackgroundTransformStyles();
            DOM.stageElement.classList.remove("has-custom-background");
            return;
        }

        const safeUrl = url.replace(/"/g, "%22");
        DOM.stageBackgroundImage.style.backgroundImage = `url("${safeUrl}")`;
        DOM.stageBackgroundImage.hidden = false;
        applyDomStageBackgroundTransform(normalizeStageBackgroundTransform(transform));
        DOM.stageElement.classList.add("has-custom-background");

        const image = new Image();
        image.onload = () => {
            if (DOM.stageBackgroundImage.style.backgroundImage !== `url("${safeUrl}")`) {
                return;
            }
            live2dState.currentBackgroundImageNaturalSize = {
                w: image.naturalWidth,
                h: image.naturalHeight,
            };
            applyStageBackgroundTransform(live2dState.currentStageBackgroundTransform || transform);
        };
        image.src = safeUrl;
    }

    function applyStageBackgroundOption(backgroundOption, transform) {
        applyDomStageBackgroundOption(backgroundOption, transform);
        void syncPixiStageBackground(backgroundOption, transform);
    }

    function updateStageBackgroundTransformValueLabels(transform) {
        const normalizedTransform = normalizeStageBackgroundTransform(transform);

        if (DOM.stageBackgroundPositionXValue) {
            DOM.stageBackgroundPositionXValue.textContent = `${normalizedTransform.positionX}%`;
        }
        if (DOM.stageBackgroundPositionYValue) {
            DOM.stageBackgroundPositionYValue.textContent = `${normalizedTransform.positionY}%`;
        }
        if (DOM.stageBackgroundScaleValue) {
            DOM.stageBackgroundScaleValue.textContent = `${normalizedTransform.scale}%`;
        }
    }

    function syncStageBackgroundTransformInputs(backgroundOption, transform) {
        const normalizedTransform = normalizeStageBackgroundTransform(transform);
        live2dState.currentStageBackgroundTransform = normalizedTransform;

        if (DOM.stageBackgroundPositionXInput) {
            DOM.stageBackgroundPositionXInput.value = String(normalizedTransform.positionX);
        }
        if (DOM.stageBackgroundPositionYInput) {
            DOM.stageBackgroundPositionYInput.value = String(normalizedTransform.positionY);
        }
        if (DOM.stageBackgroundScaleInput) {
            DOM.stageBackgroundScaleInput.value = String(normalizedTransform.scale);
        }

        if (!backgroundOption || !backgroundOption.url) {
            updateStageBackgroundTransformValueLabels(DEFAULT_STAGE_BACKGROUND_TRANSFORM);
            return;
        }

        updateStageBackgroundTransformValueLabels(normalizedTransform);
    }

    function readStageBackgroundTransformFromInputs() {
        return normalizeStageBackgroundTransform({
            positionX: DOM.stageBackgroundPositionXInput
                ? DOM.stageBackgroundPositionXInput.value
                : DEFAULT_STAGE_BACKGROUND_TRANSFORM.positionX,
            positionY: DOM.stageBackgroundPositionYInput
                ? DOM.stageBackgroundPositionYInput.value
                : DEFAULT_STAGE_BACKGROUND_TRANSFORM.positionY,
            scale: DOM.stageBackgroundScaleInput
                ? DOM.stageBackgroundScaleInput.value
                : DEFAULT_STAGE_BACKGROUND_TRANSFORM.scale,
        });
    }

    function updateStageBackgroundDetail(backgroundOption, transform) {
        if (!DOM.stageBackgroundDetail) {
            return;
        }

        if (!backgroundOption || !backgroundOption.url) {
            DOM.stageBackgroundDetail.textContent = "当前未使用背景";
            return;
        }

        const normalizedTransform = normalizeStageBackgroundTransform(transform);
        DOM.stageBackgroundDetail.textContent = `当前背景：${backgroundOption.label || backgroundOption.key} · 位置 ${normalizedTransform.positionX}% / ${normalizedTransform.positionY}% · 缩放 ${normalizedTransform.scale}%`;
    }

    function updateStageBackgroundControls(options = {}) {
        const isUploading = Boolean(options.isUploading);
        const selectedOption = currentStageBackgroundOption();
        const hasCustomBackground = Boolean(selectedOption && selectedOption.url);

        if (DOM.stageBackgroundSelect) {
            DOM.stageBackgroundSelect.disabled = isUploading;
        }
        if (DOM.stageBackgroundUploadButton) {
            DOM.stageBackgroundUploadButton.disabled = isUploading;
        }
        if (DOM.stageBackgroundResetButton) {
            DOM.stageBackgroundResetButton.disabled = isUploading
                || live2dState.selectedStageBackgroundKey === (
                    appState.config
                    && appState.config.stage
                    && appState.config.stage.default_background_key
                );
        }
        if (DOM.stageBackgroundPositionXInput) {
            DOM.stageBackgroundPositionXInput.disabled = isUploading || !hasCustomBackground;
        }
        if (DOM.stageBackgroundPositionYInput) {
            DOM.stageBackgroundPositionYInput.disabled = isUploading || !hasCustomBackground;
        }
        if (DOM.stageBackgroundScaleInput) {
            DOM.stageBackgroundScaleInput.disabled = isUploading || !hasCustomBackground;
        }
        if (DOM.stageBackgroundTransformResetButton) {
            DOM.stageBackgroundTransformResetButton.disabled = isUploading || !hasCustomBackground;
        }
    }

    function handleStageBackgroundChange(backgroundKey) {
        if (!appState.config || !appState.config.stage) {
            return;
        }

        applyStageBackgroundByKey(appState.config.stage, backgroundKey);
        const selectedOption = currentStageBackgroundOption();
        if (!selectedOption || !selectedOption.url) {
            setRunStatus("已切换为不使用背景");
            return;
        }
        setRunStatus(`已切换舞台背景：${selectedOption.label || selectedOption.key}`);
    }

    function handleStageBackgroundReset() {
        if (!appState.config || !appState.config.stage) {
            return;
        }

        applyStageBackgroundByKey(
            appState.config.stage,
            appState.config.stage.default_background_key,
        );
        setRunStatus("已切换为不使用背景");
    }

    function handleStageBackgroundTransformInput() {
        const selectedOption = currentStageBackgroundOption();
        if (!selectedOption || !selectedOption.url) {
            syncStageBackgroundTransformInputs(null, DEFAULT_STAGE_BACKGROUND_TRANSFORM);
            updateStageBackgroundControls();
            return;
        }

        const nextTransform = readStageBackgroundTransformFromInputs();
        live2dState.currentStageBackgroundTransform = nextTransform;
        applyStageBackgroundTransform(nextTransform);
        scheduleStageBackgroundTransformPersist(selectedOption.key, nextTransform);
        updateStageBackgroundTransformValueLabels(nextTransform);
        updateStageBackgroundDetail(selectedOption, nextTransform);
        updateStageBackgroundControls();
    }

    function handleStageBackgroundTransformReset() {
        const selectedOption = currentStageBackgroundOption();
        if (!selectedOption || !selectedOption.url) {
            return;
        }

        clearSavedStageBackgroundTransform(selectedOption.key);
        const nextTransform = {
            ...DEFAULT_STAGE_BACKGROUND_TRANSFORM,
        };
        live2dState.currentStageBackgroundTransform = nextTransform;
        applyStageBackgroundTransform(nextTransform);
        syncStageBackgroundTransformInputs(selectedOption, nextTransform);
        updateStageBackgroundDetail(selectedOption, nextTransform);
        updateStageBackgroundControls();
        setRunStatus(`已重置背景取景：${selectedOption.label || selectedOption.key}`);
    }

    async function handleStageBackgroundUpload() {
        if (!DOM.stageBackgroundUploadInput || !appState.config) {
            return;
        }

        const [file] = DOM.stageBackgroundUploadInput.files || [];
        DOM.stageBackgroundUploadInput.value = "";
        if (!file) {
            return;
        }

        const previousStageConfig = appState.config.stage || normalizeStageConfig(null);
        const previousKeys = new Set(
            (previousStageConfig.backgrounds || []).map((item) => item.key),
        );

        updateStageBackgroundControls({ isUploading: true });
        setRunStatus("正在上传舞台背景…");

        try {
            const formData = new FormData();
            formData.append("image", file);

            const response = await fetch("/api/web/stage/backgrounds", {
                method: "POST",
                body: formData,
            });
            if (!response.ok) {
                throw await responseToError(response);
            }

            const payload = await response.json();
            const nextStageConfig = normalizeStageConfig(payload);
            appState.config.stage = nextStageConfig;

            const uploadedOption = nextStageConfig.backgrounds.find(
                (item) => item.kind !== "default" && !previousKeys.has(item.key),
            ) || nextStageConfig.backgrounds[nextStageConfig.backgrounds.length - 1] || null;

            const nextKey = uploadedOption
                ? uploadedOption.key
                : nextStageConfig.default_background_key;
            applyStageBackgroundByKey(nextStageConfig, nextKey);
            setRunStatus(`已上传舞台背景：${uploadedOption ? uploadedOption.label : file.name}`);
        } catch (error) {
            console.error(error);
            applyStageBackgroundByKey(previousStageConfig, live2dState.selectedStageBackgroundKey);
            setRunStatus(error.message || "舞台背景上传失败");
        } finally {
            updateStageBackgroundControls();
        }
    }

    function ensureDefaultStageBackgroundTexture() {
        if (live2dState.defaultStageBackgroundTexture) {
            return live2dState.defaultStageBackgroundTexture;
        }

        const canvas = document.createElement("canvas");
        canvas.width = 1200;
        canvas.height = 900;
        const context = canvas.getContext("2d");
        if (!context) {
            live2dState.defaultStageBackgroundTexture = window.PIXI.Texture.WHITE;
            return live2dState.defaultStageBackgroundTexture;
        }

        const baseGradient = context.createLinearGradient(0, 0, 0, canvas.height);
        baseGradient.addColorStop(0, "#f8f0e7");
        baseGradient.addColorStop(0.46, "#edd9c5");
        baseGradient.addColorStop(1, "#d8bba0");
        context.fillStyle = baseGradient;
        context.fillRect(0, 0, canvas.width, canvas.height);

        const topGlow = context.createRadialGradient(
            canvas.width * 0.5,
            canvas.height * 0.16,
            0,
            canvas.width * 0.5,
            canvas.height * 0.16,
            canvas.width * 0.42,
        );
        topGlow.addColorStop(0, "rgba(255,255,255,0.72)");
        topGlow.addColorStop(1, "rgba(255,255,255,0)");
        context.fillStyle = topGlow;
        context.fillRect(0, 0, canvas.width, canvas.height);

        const warmGlow = context.createRadialGradient(
            canvas.width * 0.5,
            canvas.height * 0.98,
            0,
            canvas.width * 0.5,
            canvas.height * 0.98,
            canvas.width * 0.54,
        );
        warmGlow.addColorStop(0, "rgba(202,92,54,0.18)");
        warmGlow.addColorStop(1, "rgba(202,92,54,0)");
        context.fillStyle = warmGlow;
        context.fillRect(0, 0, canvas.width, canvas.height);

        live2dState.defaultStageBackgroundTexture = window.PIXI.Texture.from(canvas);
        return live2dState.defaultStageBackgroundTexture;
    }

    function loadPixiTexture(url) {
        return new Promise((resolve, reject) => {
            const texture = window.PIXI.Texture.from(url);
            const baseTexture = texture.baseTexture;

            if (baseTexture.valid) {
                resolve(texture);
                return;
            }

            const handleLoaded = () => {
                cleanup();
                resolve(texture);
            };
            const handleError = (error) => {
                cleanup();
                reject(error || new Error(`Failed to load texture: ${url}`));
            };
            const cleanup = () => {
                baseTexture.off("loaded", handleLoaded);
                baseTexture.off("error", handleError);
            };

            baseTexture.on("loaded", handleLoaded);
            baseTexture.on("error", handleError);
        });
    }

    function ensureStageBackgroundSprite() {
        if (live2dState.stageBackgroundSprite) {
            return live2dState.stageBackgroundSprite;
        }

        const sprite = new window.PIXI.Sprite(window.PIXI.Texture.WHITE);
        sprite.anchor.set(0, 0);
        sprite.zIndex = 0;
        live2dState.stageBackgroundSprite = sprite;

        if (live2dState.live2dBackgroundLayer) {
            live2dState.live2dBackgroundLayer.addChild(sprite);
        }

        return sprite;
    }

    async function syncPixiStageBackground(backgroundOption, transform) {
        if (!live2dState.pixiApp || !live2dState.live2dBackgroundLayer) {
            return;
        }

        const loadToken = ++live2dState.stageBackgroundLoadToken;
        const hasCustomBackground = Boolean(backgroundOption && String(backgroundOption.url || "").trim());

        try {
            let texture;
            if (hasCustomBackground) {
                const url = String(backgroundOption.url || "").trim().replace(/"/g, "%22");
                texture = await loadPixiTexture(url);
            } else {
                texture = ensureDefaultStageBackgroundTexture();
            }

            if (loadToken !== live2dState.stageBackgroundLoadToken) {
                return;
            }

            const sprite = ensureStageBackgroundSprite();
            sprite.texture = texture;
            sprite.visible = true;
            sprite.alpha = hasCustomBackground ? 0.98 : 1;

            const realWidth = texture.baseTexture && texture.baseTexture.realWidth
                ? texture.baseTexture.realWidth
                : texture.width;
            const realHeight = texture.baseTexture && texture.baseTexture.realHeight
                ? texture.baseTexture.realHeight
                : texture.height;
            live2dState.currentBackgroundImageNaturalSize = {
                w: realWidth,
                h: realHeight,
            };

            applyStageBackgroundTransform(transform);
            applyStageEffectsToRuntime(
                live2dState.stageEffects || DEFAULT_STAGE_EFFECT_SETTINGS,
            );
        } catch (error) {
            console.warn("Failed to sync Pixi stage background", error);
        }
    }

    return {
        applyStageBackgroundByKey,
        applyStageBackgroundTransform,
        currentStageBackgroundOption,
        handleStageBackgroundChange,
        handleStageBackgroundReset,
        handleStageBackgroundTransformInput,
        handleStageBackgroundTransformReset,
        handleStageBackgroundUpload,
        normalizeStageConfig,
        renderStageBackgroundOptions,
        resolveInitialStageBackgroundKey,
        syncPixiStageBackground,
    };
}
