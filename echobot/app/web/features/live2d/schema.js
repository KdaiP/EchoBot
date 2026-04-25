import { DEFAULT_LIP_SYNC_IDS } from "../../core/store.js";

export function resolveLive2DModelOptions(live2dConfig) {
    const modelOptions = Array.isArray(live2dConfig && live2dConfig.models)
        ? live2dConfig.models
        : [];
    const normalizedOptions = modelOptions
        .map(normalizeLive2DModelOption)
        .filter((item) => item.model_url);

    if (normalizedOptions.length > 0) {
        return normalizedOptions;
    }

    const fallbackOption = normalizeLive2DModelOption(live2dConfig);
    return fallbackOption.model_url ? [fallbackOption] : [];
}

export function normalizeLive2DConfig(live2dConfig) {
    if (!live2dConfig || typeof live2dConfig !== "object") {
        return buildLive2DConfig(null, []);
    }

    const normalizedConfig = normalizeLive2DModelOption(live2dConfig);
    return {
        available: Boolean(live2dConfig.available),
        source: normalizedConfig.source,
        selection_key: normalizedConfig.selection_key,
        model_name: normalizedConfig.model_name,
        model_url: normalizedConfig.model_url,
        directory_name: normalizedConfig.directory_name,
        lip_sync_parameter_ids: normalizedConfig.lip_sync_parameter_ids,
        mouth_form_parameter_id: normalizedConfig.mouth_form_parameter_id,
        expressions: normalizedConfig.expressions,
        motions: normalizedConfig.motions,
        hotkeys: normalizedConfig.hotkeys,
        annotations_writable: normalizedConfig.annotations_writable,
        models: Array.isArray(live2dConfig.models)
            ? live2dConfig.models.map(normalizeLive2DModelOption)
            : [],
    };
}

export function buildLive2DConfig(selectedOption, modelOptions) {
    const normalizedOptions = Array.isArray(modelOptions)
        ? modelOptions.map(normalizeLive2DModelOption).filter((item) => item.model_url)
        : [];

    if (!selectedOption) {
        return {
            available: false,
            source: "",
            selection_key: "",
            model_name: "",
            model_url: "",
            directory_name: "",
            lip_sync_parameter_ids: DEFAULT_LIP_SYNC_IDS.slice(),
            mouth_form_parameter_id: null,
            expressions: [],
            motions: [],
            hotkeys: [],
            annotations_writable: false,
            models: normalizedOptions,
        };
    }

    const normalizedOption = normalizeLive2DModelOption(selectedOption);
    return {
        available: true,
        source: normalizedOption.source,
        selection_key: normalizedOption.selection_key,
        model_name: normalizedOption.model_name,
        model_url: normalizedOption.model_url,
        directory_name: normalizedOption.directory_name,
        lip_sync_parameter_ids: normalizedOption.lip_sync_parameter_ids,
        mouth_form_parameter_id: normalizedOption.mouth_form_parameter_id,
        expressions: normalizedOption.expressions,
        motions: normalizedOption.motions,
        hotkeys: normalizedOption.hotkeys,
        annotations_writable: normalizedOption.annotations_writable,
        models: normalizedOptions,
    };
}

export function normalizeLive2DModelOption(modelOption) {
    const lipSyncParameterIds = Array.isArray(modelOption && modelOption.lip_sync_parameter_ids)
        ? modelOption.lip_sync_parameter_ids.filter((item) => typeof item === "string")
        : [];

    return {
        source: String((modelOption && modelOption.source) || ""),
        selection_key: String(
            (modelOption && modelOption.selection_key)
            || (modelOption && modelOption.model_url)
            || "",
        ),
        model_name: String((modelOption && modelOption.model_name) || ""),
        model_url: String((modelOption && modelOption.model_url) || ""),
        directory_name: String((modelOption && modelOption.directory_name) || ""),
        lip_sync_parameter_ids: lipSyncParameterIds,
        mouth_form_parameter_id: typeof (modelOption && modelOption.mouth_form_parameter_id) === "string"
            ? modelOption.mouth_form_parameter_id
            : null,
        expressions: normalizeLive2DExpressions(modelOption && modelOption.expressions),
        motions: normalizeLive2DMotions(modelOption && modelOption.motions),
        hotkeys: normalizeLive2DHotkeys(modelOption && modelOption.hotkeys),
        annotations_writable: Boolean(modelOption && modelOption.annotations_writable),
    };
}

function normalizeLive2DExpressions(items) {
    return Array.isArray(items)
        ? items
            .filter((item) => item && typeof item === "object")
            .map((item) => ({
                name: String(item.name || item.file || ""),
                file: String(item.file || ""),
                url: String(item.url || ""),
                note: String(item.note || ""),
            }))
            .filter((item) => item.file && item.url)
        : [];
}

function normalizeLive2DMotions(items) {
    return Array.isArray(items)
        ? items
            .filter((item) => item && typeof item === "object")
            .map((item) => ({
                name: String(item.name || item.file || ""),
                file: String(item.file || ""),
                url: String(item.url || ""),
                note: String(item.note || ""),
                group: String(item.group || ""),
                index: Number.isInteger(item.index) ? item.index : 0,
            }))
            .filter((item) => item.file && item.url && item.group)
        : [];
}

function normalizeLive2DHotkeys(items) {
    return Array.isArray(items)
        ? items
            .filter((item) => item && typeof item === "object")
            .map((item) => ({
                hotkey_key: String(item.hotkey_key || item.hotkey_id || ""),
                hotkey_id: String(item.hotkey_id || ""),
                name: String(item.name || item.action || "热键"),
                action: String(item.action || ""),
                file: String(item.file || ""),
                shortcut_tokens: Array.isArray(item.shortcut_tokens)
                    ? item.shortcut_tokens.filter((token) => typeof token === "string")
                    : [],
                shortcut_label: String(item.shortcut_label || ""),
                target_kind: String(item.target_kind || ""),
                supported: Boolean(item.supported),
            }))
        : [];
}
