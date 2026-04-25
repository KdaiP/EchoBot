import { DOM } from "../../core/dom.js";
import { appState, live2dState } from "../../core/store.js";
import {
    readBoolean,
    readString,
    writeBoolean,
    writeString,
} from "../../core/storage.js";
import {
    LIVE2D_HOTKEYS_STORAGE_KEY,
    LIVE2D_MOUSE_FOLLOW_STORAGE_KEY,
    LIVE2D_SELECTION_STORAGE_KEY,
} from "./constants.js";
import {
    buildLive2DConfig,
    normalizeLive2DModelOption,
    resolveLive2DModelOptions,
} from "./schema.js";

export function createLive2DConfigController(deps) {
    const {
        responseToError,
        setRunStatus,
        setStageMessage,
        applyLive2DMouseFollowSetting,
        applyStageBackgroundByKey,
        applyStageEffectsSettings,
        buildStageConfig,
        loadLive2DModel,
        loadSavedStageEffectsSettings,
        resetLive2DHotkeyState,
        renderLive2DControls,
        renderStageBackgroundOptions,
        resolveInitialStageBackgroundKey,
    } = deps;

    function applyConfigToUI(config) {
        const rememberedSessionName = String(
            window.localStorage.getItem("echobot.web.session") || config.session_name,
        ).trim() || config.session_name;
        const live2dModelOptions = resolveLive2DModelOptions(config.live2d);
        const currentLive2DConfig = resolveInitialLive2DConfig(config.live2d, live2dModelOptions);
        const stageConfig = buildStageConfig(config.stage);
        const stageBackgroundKey = resolveInitialStageBackgroundKey(stageConfig);

        live2dState.live2dHotkeysEnabled = loadSavedLive2DHotkeysEnabled();
        live2dState.live2dMouseFollowEnabled = loadSavedLive2DMouseFollowEnabled();
        appState.config.live2d = currentLive2DConfig;
        appState.config.stage = stageConfig;
        live2dState.selectedStageBackgroundKey = stageBackgroundKey;
        live2dState.stageEffects = loadSavedStageEffectsSettings();

        if (DOM.live2dHotkeysCheckbox) {
            DOM.live2dHotkeysCheckbox.checked = live2dState.live2dHotkeysEnabled;
        }
        if (DOM.live2dMouseFollowCheckbox) {
            DOM.live2dMouseFollowCheckbox.checked = live2dState.live2dMouseFollowEnabled;
        }

        DOM.sessionLabel.textContent = `会话: ${rememberedSessionName}`;
        renderLive2DModelOptions(live2dModelOptions, currentLive2DConfig.selection_key);
        renderLive2DControls(currentLive2DConfig);
        renderStageBackgroundOptions(stageConfig, stageBackgroundKey);
        applyStageBackgroundByKey(stageConfig, stageBackgroundKey);
        applyStageEffectsSettings(live2dState.stageEffects, { persist: false });

        if (!currentLive2DConfig.available) {
            setStageMessage("未找到 Live2D 模型。请检查 .echobot/live2d 目录。");
        } else {
            setStageMessage("");
        }

        return currentLive2DConfig;
    }

    function resolveInitialLive2DConfig(live2dConfig, modelOptions) {
        const selectedOption = findLive2DModelOption(modelOptions, loadSavedLive2DSelectionKey())
            || findLive2DModelOption(modelOptions, live2dConfig && live2dConfig.selection_key)
            || modelOptions[0]
            || null;
        return buildCurrentLive2DConfig(selectedOption, modelOptions, {
            persistSelection: true,
        });
    }

    function buildCurrentLive2DConfig(selectedOption, modelOptions, options = {}) {
        const normalizedOption = normalizeLive2DModelOption(selectedOption);
        if (options.persistSelection) {
            persistLive2DSelectionKey(normalizedOption.selection_key);
        }
        return buildLive2DConfig(selectedOption, modelOptions);
    }

    function renderLive2DModelOptions(modelOptions, selectedKey) {
        if (!DOM.modelSelect) {
            return;
        }

        DOM.modelSelect.innerHTML = "";

        if (!modelOptions || modelOptions.length === 0) {
            const option = document.createElement("option");
            option.value = "";
            option.textContent = "未找到 Live2D 模型";
            DOM.modelSelect.appendChild(option);
            updateLive2DUploadControls();
            return;
        }

        modelOptions.forEach((modelOption) => {
            const option = document.createElement("option");
            option.value = modelOption.selection_key;
            option.textContent = buildLive2DModelLabel(modelOption);
            DOM.modelSelect.appendChild(option);
        });

        DOM.modelSelect.value = selectedKey || modelOptions[0].selection_key;
        updateLive2DUploadControls();
    }

    function buildLive2DModelLabel(modelOption) {
        const sourceLabel = modelOption.source === "builtin" ? "内置" : "工作区";
        const baseName = modelOption.directory_name && modelOption.directory_name !== modelOption.model_name
            ? `${modelOption.directory_name} / ${modelOption.model_name}`
            : (modelOption.model_name || modelOption.directory_name || modelOption.selection_key);
        return `${baseName} (${sourceLabel})`;
    }

    function updateLive2DUploadControls(options = {}) {
        const isUploading = Boolean(options.isUploading);
        const isLoading = options.isLoading === undefined
            ? live2dState.live2dLoading
            : Boolean(options.isLoading);
        const isBusy = isUploading || isLoading;
        const modelOptions = resolveLive2DModelOptions(appState.config && appState.config.live2d);

        if (DOM.modelSelect) {
            if (isBusy || modelOptions.length === 0) {
                DOM.modelSelect.disabled = true;
            } else {
                DOM.modelSelect.disabled = modelOptions.length <= 1;
            }
        }
        if (DOM.live2dUploadButton) {
            DOM.live2dUploadButton.disabled = isBusy;
        }
        if (DOM.live2dUploadInput) {
            DOM.live2dUploadInput.disabled = isBusy;
        }
    }

    function findLive2DModelOption(modelOptions, selectionKey) {
        const normalizedSelectionKey = String(selectionKey || "").trim();
        if (!normalizedSelectionKey) {
            return null;
        }
        return modelOptions.find((item) => item.selection_key === normalizedSelectionKey) || null;
    }

    async function handleLive2DDirectoryUpload() {
        if (!DOM.live2dUploadInput || !appState.config) {
            return;
        }

        const uploadEntries = Array.from(DOM.live2dUploadInput.files || [])
            .map((file) => ({
                file: file,
                relativePath: String(file.webkitRelativePath || file.name || "").trim(),
            }))
            .filter((item) => item.relativePath);
        DOM.live2dUploadInput.value = "";
        if (uploadEntries.length === 0) {
            return;
        }

        const previousLive2DConfig = appState.config.live2d;
        const previousModelOptions = resolveLive2DModelOptions(previousLive2DConfig);
        const previousKeys = new Set(previousModelOptions.map((item) => item.selection_key));

        updateLive2DUploadControls({ isUploading: true });
        setRunStatus("正在上传 Live2D 文件夹…");

        try {
            const formData = new FormData();
            uploadEntries.forEach((item) => {
                formData.append("files", item.file, item.file.name);
                formData.append("relative_paths", item.relativePath);
            });

            const response = await fetch("/api/web/live2d", {
                method: "POST",
                body: formData,
            });
            if (!response.ok) {
                throw await responseToError(response);
            }

            const payload = await response.json();
            const nextModelOptions = resolveLive2DModelOptions(payload);
            const uploadedOption = nextModelOptions.find(
                (item) => !previousKeys.has(item.selection_key),
            ) || findLive2DModelOption(nextModelOptions, payload.selection_key)
                || nextModelOptions[0]
                || null;

            if (!uploadedOption) {
                throw new Error("上传完成后没有发现可用的 Live2D 模型。");
            }

            const nextLive2DConfig = buildCurrentLive2DConfig(uploadedOption, nextModelOptions);
            appState.config.live2d = nextLive2DConfig;
            const loadPromise = loadLive2DModel(nextLive2DConfig);
            renderLive2DModelOptions(nextModelOptions, nextLive2DConfig.selection_key);
            updateLive2DUploadControls({ isUploading: true, isLoading: true });
            renderLive2DControls(nextLive2DConfig);

            const didLoadModel = await loadPromise;
            renderLive2DControls(appState.config.live2d);
            if (
                !didLoadModel
                || appState.config.live2d.selection_key !== nextLive2DConfig.selection_key
            ) {
                return;
            }
            persistLive2DSelectionKey(nextLive2DConfig.selection_key);
            setRunStatus(`已上传 Live2D 模型：${buildLive2DModelLabel(uploadedOption)}`);
        } catch (error) {
            console.error(error);
            appState.config.live2d = previousLive2DConfig;
            renderLive2DModelOptions(previousModelOptions, previousLive2DConfig.selection_key);
            renderLive2DControls(previousLive2DConfig);
            persistLive2DSelectionKey(previousLive2DConfig.selection_key);
            setRunStatus(error.message || "Live2D 文件夹上传失败");
        } finally {
            updateLive2DUploadControls();
        }
    }

    async function handleLive2DModelChange(selectionKey) {
        if (!appState.config) {
            return;
        }
        if (live2dState.live2dLoading) {
            renderLive2DModelOptions(
                resolveLive2DModelOptions(appState.config.live2d),
                appState.config.live2d.selection_key,
            );
            return;
        }

        const modelOptions = resolveLive2DModelOptions(appState.config.live2d);
        const nextModelOption = findLive2DModelOption(modelOptions, selectionKey);
        if (!nextModelOption) {
            renderLive2DModelOptions(modelOptions, appState.config.live2d.selection_key);
            return;
        }

        if (appState.config.live2d.selection_key === nextModelOption.selection_key) {
            return;
        }

        const previousLive2DConfig = appState.config.live2d;
        const nextLive2DConfig = buildCurrentLive2DConfig(nextModelOption, modelOptions);
        appState.config.live2d = nextLive2DConfig;
        const loadPromise = loadLive2DModel(nextLive2DConfig);
        renderLive2DModelOptions(modelOptions, nextLive2DConfig.selection_key);
        updateLive2DUploadControls({ isLoading: true });
        renderLive2DControls(nextLive2DConfig);
        setRunStatus(`切换模型中：${buildLive2DModelLabel(nextModelOption)}`);

        try {
            const didLoadModel = await loadPromise;
            renderLive2DControls(appState.config.live2d);
            updateLive2DUploadControls();
            if (
                !didLoadModel
                || appState.config.live2d.selection_key !== nextLive2DConfig.selection_key
            ) {
                return;
            }
            persistLive2DSelectionKey(nextLive2DConfig.selection_key);
            setRunStatus(`已切换模型：${buildLive2DModelLabel(nextModelOption)}`);
        } catch (error) {
            console.error(error);
            if (appState.config.live2d.selection_key !== nextLive2DConfig.selection_key) {
                return;
            }
            appState.config.live2d = previousLive2DConfig;
            renderLive2DModelOptions(modelOptions, previousLive2DConfig.selection_key);
            renderLive2DControls(previousLive2DConfig);
            persistLive2DSelectionKey(previousLive2DConfig.selection_key);
            updateLive2DUploadControls();


            setRunStatus(error.message || "Live2D 模型加载失败");
        }
    }

    function loadSavedLive2DSelectionKey() {
        return readString(LIVE2D_SELECTION_STORAGE_KEY).trim();
    }

    function persistLive2DSelectionKey(selectionKey) {
        writeString(LIVE2D_SELECTION_STORAGE_KEY, String(selectionKey || ""));
    }

    function loadSavedLive2DHotkeysEnabled() {
        return readBoolean(LIVE2D_HOTKEYS_STORAGE_KEY, false);
    }

    function persistLive2DHotkeysEnabled(enabled) {
        writeBoolean(LIVE2D_HOTKEYS_STORAGE_KEY, Boolean(enabled));
    }

    function loadSavedLive2DMouseFollowEnabled() {
        return readBoolean(LIVE2D_MOUSE_FOLLOW_STORAGE_KEY, true);
    }

    function persistLive2DMouseFollowEnabled(enabled) {
        writeBoolean(LIVE2D_MOUSE_FOLLOW_STORAGE_KEY, Boolean(enabled));
    }

    function handleMouseFollowToggle() {
        if (!DOM.live2dMouseFollowCheckbox) {
            return;
        }

        live2dState.live2dMouseFollowEnabled = DOM.live2dMouseFollowCheckbox.checked;
        persistLive2DMouseFollowEnabled(live2dState.live2dMouseFollowEnabled);
        applyLive2DMouseFollowSetting();
        setRunStatus(
            live2dState.live2dMouseFollowEnabled
                ? "已开启 Live2D 眼神跟随"
                : "已关闭 Live2D 眼神跟随",
        );
    }

    function handleHotkeysToggle() {
        if (!DOM.live2dHotkeysCheckbox) {
            return;
        }

        live2dState.live2dHotkeysEnabled = DOM.live2dHotkeysCheckbox.checked;
        persistLive2DHotkeysEnabled(live2dState.live2dHotkeysEnabled);
        resetLive2DHotkeyState();
        setRunStatus(
            live2dState.live2dHotkeysEnabled
                ? "已启用 Live2D 模型热键"
                : "已关闭 Live2D 模型热键",
        );
    }

    return {
        applyConfigToUI,
        handleLive2DDirectoryUpload,
        handleLive2DModelChange,
        handleHotkeysToggle,
        handleMouseFollowToggle,
    };
}
