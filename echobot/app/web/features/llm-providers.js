import { DOM } from "../core/dom.js";
import { appState, runtimeState } from "../core/store.js";

const PROVIDER_PRESETS = {
    custom: {
        label: "",
        base_url: "https://api.openai.com/v1",
        model: "",
        supports_image_input: true,
    },
    openai: {
        label: "OpenAI",
        base_url: "https://api.openai.com/v1",
        model: "gpt-4o-mini",
        supports_image_input: true,
    },
    deepseek: {
        label: "DeepSeek",
        base_url: "https://api.deepseek.com/v1",
        model: "deepseek-chat",
        supports_image_input: false,
    },
    dashscope: {
        label: "阿里云百炼",
        base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: "qwen-plus",
        supports_image_input: false,
    },
    openrouter: {
        label: "OpenRouter",
        base_url: "https://openrouter.ai/api/v1",
        model: "",
        supports_image_input: true,
    },
    ollama: {
        label: "Ollama",
        base_url: "http://127.0.0.1:11434/v1",
        model: "qwen3",
        supports_image_input: false,
    },
};

export function createLlmProvidersModule(deps) {
    const { applyLlmConfig, requestJson, setRunStatus } = deps;
    let editingName = "";
    let creating = false;
    let busy = false;
    let saving = false;
    let savingPromise = null;
    let dirty = false;
    let editVersion = 0;
    let autoSaveTimer = 0;

    function providers() {
        return runtimeState.llmConfig?.providers || [];
    }

    function currentProfile() {
        return providers().find((provider) => provider.name === editingName) || null;
    }

    function openDialog() {
        if (!DOM.llmProviderDialog) {
            return;
        }
        renderProviderPicker();
        const activeName = runtimeState.llmConfig?.active_provider || "";
        const preferred = providers().find((item) => item.name === activeName)
            || providers()[0];
        if (preferred) {
            showProfile(preferred.name);
        } else {
            startCreate();
        }
        DOM.llmProviderDialog.showModal();
    }

    async function closeDialog() {
        clearAutoSaveTimer();
        if (dirty && !await saveProvider()) {
            return;
        }
        DOM.llmProviderDialog?.close();
    }

    function maybeOpenFirstRun() {
        if (providers().length === 0) {
            openDialog();
            setDialogStatus("尚未配置 LLM Provider，填写模型和地址后会自动保存。");
        }
    }

    function renderProviderPicker() {
        if (!DOM.llmProviderEditorSelect) {
            return;
        }
        DOM.llmProviderEditorSelect.innerHTML = "";
        providers().forEach((provider) => {
            const option = document.createElement("option");
            option.value = provider.name;
            const source = provider.editable ? "网页配置" : "环境变量 · 只读";
            option.textContent = `${provider.label} — ${provider.model}（${source}）`;
            DOM.llmProviderEditorSelect.appendChild(option);
        });
        DOM.llmProviderEditorSelect.disabled = providers().length === 0 || busy;
    }

    function showProfile(name) {
        const profile = providers().find((item) => item.name === name);
        if (!profile) {
            startCreate();
            return;
        }
        creating = false;
        editingName = profile.name;
        resetDraftState();
        if (DOM.llmProviderEditorSelect) {
            DOM.llmProviderEditorSelect.value = profile.name;
        }
        fillForm(profile);
        setFormEditable(Boolean(profile.editable));
        setDialogStatus(
            profile.editable
                ? "修改后会自动保存并热更新，无需重启 EchoBot。"
                : "该配置来自 .env，只能查看和选择，不能在网页中修改。",
        );
    }

    async function startCreate() {
        clearAutoSaveTimer();
        if (dirty && !await saveProvider()) {
            return;
        }
        creating = true;
        editingName = generateProviderId();
        resetDraftState();
        if (DOM.llmProviderEditorSelect) {
            DOM.llmProviderEditorSelect.value = "";
        }
        if (DOM.llmProviderPresetSelect) {
            DOM.llmProviderPresetSelect.value = "custom";
        }
        fillForm({
            name: "",
            label: "",
            model: "",
            base_url: PROVIDER_PRESETS.custom.base_url,
            timeout: 60,
            max_retries: 2,
            extra_headers: {},
            extra_body: {},
            supports_image_input: true,
            api_key_configured: false,
        });
        setFormEditable(true);
        DOM.llmProviderLabelInput?.focus();
        setDialogStatus("填写模型和 Base URL 后会自动保存。");
    }

    function fillForm(profile) {
        setValue(DOM.llmProviderLabelInput, profile.label || "");
        setValue(DOM.llmProviderModelInput, profile.model || "");
        setValue(DOM.llmProviderBaseUrlInput, profile.base_url || "");
        setValue(DOM.llmProviderApiKeyInput, "");
        setValue(DOM.llmProviderTimeoutInput, profile.timeout ?? 60);
        setValue(DOM.llmProviderMaxRetriesInput, profile.max_retries ?? 2);
        setValue(
            DOM.llmProviderExtraHeadersInput,
            JSON.stringify(profile.extra_headers || {}, null, 2),
        );
        setValue(
            DOM.llmProviderExtraBodyInput,
            JSON.stringify(profile.extra_body || {}, null, 2),
        );
        if (DOM.llmProviderImageCheckbox) {
            DOM.llmProviderImageCheckbox.checked = profile.supports_image_input !== false;
        }
        if (DOM.llmProviderClearApiKeyCheckbox) {
            DOM.llmProviderClearApiKeyCheckbox.checked = false;
        }
        if (DOM.llmProviderApiKeyStatus) {
            DOM.llmProviderApiKeyStatus.textContent = profile.api_key_configured
                ? "已保存 API Key。输入新值可以替换，留空会保留。"
                : "未保存 API Key。本地免认证服务可以保持为空。";
        }
    }

    function setFormEditable(editable) {
        const fields = [
            DOM.llmProviderPresetSelect,
            DOM.llmProviderLabelInput,
            DOM.llmProviderModelInput,
            DOM.llmProviderBaseUrlInput,
            DOM.llmProviderApiKeyInput,
            DOM.llmProviderClearApiKeyCheckbox,
            DOM.llmProviderTimeoutInput,
            DOM.llmProviderMaxRetriesInput,
            DOM.llmProviderImageCheckbox,
            DOM.llmProviderExtraHeadersInput,
            DOM.llmProviderExtraBodyInput,
        ];
        fields.forEach((field) => {
            if (field) {
                field.disabled = !editable || busy;
            }
        });
        const active = runtimeState.llmConfig?.active_provider || "";
        if (DOM.llmProviderDeleteButton) {
            DOM.llmProviderDeleteButton.disabled = (
                !editable || busy || creating || editingName === active
            );
        }
        if (DOM.llmProviderUseButton) {
            DOM.llmProviderUseButton.disabled = busy || (!creating && !editingName);
            DOM.llmProviderUseButton.textContent = (
                !creating && editingName === active ? "使用中" : "使用"
            );
        }
        if (DOM.llmProviderTestButton) {
            DOM.llmProviderTestButton.disabled = !editable || busy;
        }
        if (DOM.llmProviderDiscoverButton) {
            DOM.llmProviderDiscoverButton.disabled = !editable || busy;
        }
        if (DOM.llmProviderNewButton) {
            DOM.llmProviderNewButton.disabled = busy;
        }
    }

    function applyPreset() {
        if (!creating) {
            return;
        }
        const presetName = DOM.llmProviderPresetSelect?.value || "custom";
        const preset = PROVIDER_PRESETS[presetName] || PROVIDER_PRESETS.custom;
        setValue(DOM.llmProviderLabelInput, preset.label);
        setValue(DOM.llmProviderBaseUrlInput, preset.base_url);
        setValue(DOM.llmProviderModelInput, preset.model);
        if (DOM.llmProviderImageCheckbox) {
            DOM.llmProviderImageCheckbox.checked = preset.supports_image_input;
        }
        markDirty();
    }

    function collectProfile({ reportInvalid = true } = {}) {
        const valid = DOM.llmProviderForm?.checkValidity() !== false;
        if (!valid) {
            if (reportInvalid) {
                DOM.llmProviderForm?.reportValidity();
            }
            throw new Error("请填写所有必填项。");
        }
        return {
            name: editingName,
            label: String(DOM.llmProviderLabelInput?.value || "").trim(),
            model: String(DOM.llmProviderModelInput?.value || "").trim(),
            base_url: String(DOM.llmProviderBaseUrlInput?.value || "").trim(),
            timeout: Number(DOM.llmProviderTimeoutInput?.value || 60),
            max_retries: Number(DOM.llmProviderMaxRetriesInput?.value || 0),
            extra_headers: parseObject(
                DOM.llmProviderExtraHeadersInput?.value,
                "Extra Headers",
            ),
            extra_body: parseObject(
                DOM.llmProviderExtraBodyInput?.value,
                "Extra Body",
            ),
            supports_image_input: Boolean(DOM.llmProviderImageCheckbox?.checked),
        };
    }

    function requestDraft() {
        const profile = collectProfile();
        const apiKey = String(DOM.llmProviderApiKeyInput?.value || "").trim();
        return {
            ...profile,
            api_key: apiKey || null,
            existing_name: creating ? null : editingName,
        };
    }

    async function saveProvider({ automatic = false } = {}) {
        clearAutoSaveTimer();
        if (saving && savingPromise) {
            await savingPromise;
            if (dirty) {
                return await saveProvider({ automatic });
            }
            return true;
        }
        if (!dirty && !creating) {
            return true;
        }

        let profile;
        try {
            profile = collectProfile({ reportInvalid: !automatic });
        } catch (error) {
            setDialogStatus(
                automatic
                    ? "继续填写模型和 Base URL 后将自动保存。"
                    : error.message || String(error),
                !automatic,
            );
            return false;
        }

        const capturedVersion = editVersion;
        const wasCreating = creating;
        dirty = false;
        saving = true;
        setDialogStatus("正在自动保存…");

        savingPromise = (async () => {
            const apiKey = String(DOM.llmProviderApiKeyInput?.value || "").trim();
            if (wasCreating) {
                return await requestJson("/api/web/llm/providers", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        ...profile,
                        api_key: apiKey || null,
                        expected_config_revision: runtimeState.llmConfig?.config_revision || 0,
                    }),
                });
            }

            const { name: _name, ...updates } = profile;
            return await requestJson(
                `/api/web/llm/providers/${encodeURIComponent(editingName)}`,
                {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        ...updates,
                        api_key: apiKey || null,
                        clear_api_key: Boolean(
                            DOM.llmProviderClearApiKeyCheckbox?.checked,
                        ),
                        expected_config_revision: runtimeState.llmConfig?.config_revision || 0,
                    }),
                },
            );
        })();

        try {
            const payload = await savingPromise;
            syncConfig(payload);
            editingName = profile.name;
            creating = false;
            renderProviderPicker();
            if (editVersion === capturedVersion) {
                showProfile(profile.name);
                setDialogStatus("已自动保存。");
            } else {
                dirty = true;
                scheduleAutoSave();
            }
            setRunStatus(`LLM 配置已保存：${profile.label || profile.model}`);
            return true;
        } catch (error) {
            console.error(error);
            dirty = true;
            setDialogStatus(error.message || String(error), true);
            return false;
        } finally {
            saving = false;
            savingPromise = null;
        }
    }

    async function useProvider() {
        clearAutoSaveTimer();
        if (!await saveProvider()) {
            return;
        }
        if (!editingName || runtimeState.llmConfig?.active_provider === editingName) {
            setDialogStatus("当前已在使用此 Provider。");
            setFormEditable(creating || Boolean(currentProfile()?.editable));
            return;
        }

        setBusy(true);
        setDialogStatus("正在切换 Provider…");
        try {
            const payload = await requestJson("/api/web/llm/provider", {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    provider: editingName,
                    expected_revision: runtimeState.llmConfig?.revision || 0,
                }),
            });
            syncConfig(payload);
            setDialogStatus("已切换并开始使用此 Provider。");
            setRunStatus(`正在使用 LLM Provider：${currentProfile()?.label || editingName}`);
        } catch (error) {
            setDialogStatus(error.message || String(error), true);
        } finally {
            setBusy(false);
        }
    }

    async function testProvider() {
        let draft;
        try {
            draft = requestDraft();
        } catch (error) {
            setDialogStatus(error.message || String(error), true);
            return;
        }
        setBusy(true);
        setDialogStatus("正在调用当前模型测试连接…");
        try {
            const result = await requestJson("/api/web/llm/providers/test", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(draft),
            });
            setDialogStatus(
                result.success ? `连接成功：${result.model || draft.model}` : result.message,
                !result.success,
            );
        } catch (error) {
            setDialogStatus(error.message || String(error), true);
        } finally {
            setBusy(false);
        }
    }

    async function discoverModels() {
        let draft;
        try {
            draft = requestDraft();
        } catch (error) {
            setDialogStatus(error.message || String(error), true);
            return;
        }
        setBusy(true);
        setDialogStatus("正在获取模型列表…");
        try {
            const result = await requestJson("/api/web/llm/providers/discover-models", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(draft),
            });
            renderModelOptions(result.models || []);
            if (!DOM.llmProviderModelInput?.value && result.models?.length) {
                DOM.llmProviderModelInput.value = result.models[0];
                markDirty();
            }
            setDialogStatus(`已获取 ${result.models?.length || 0} 个模型。`);
        } catch (error) {
            setDialogStatus(error.message || String(error), true);
        } finally {
            setBusy(false);
        }
    }

    async function deleteProvider() {
        const profile = currentProfile();
        if (!profile?.editable || profile.name === runtimeState.llmConfig?.active_provider) {
            return;
        }
        if (!window.confirm(`确定删除 LLM 配置“${profile.label}”吗？`)) {
            return;
        }
        setBusy(true);
        try {
            const revision = runtimeState.llmConfig?.config_revision || 0;
            const payload = await requestJson(
                `/api/web/llm/providers/${encodeURIComponent(profile.name)}`
                    + `?expected_config_revision=${revision}`,
                { method: "DELETE" },
            );
            syncConfig(payload);
            renderProviderPicker();
            if (providers().length) {
                showProfile(providers()[0].name);
            } else {
                startCreate();
            }
            setDialogStatus("配置已删除。");
        } catch (error) {
            setDialogStatus(error.message || String(error), true);
        } finally {
            setBusy(false);
        }
    }

    function syncConfig(config) {
        if (appState.config) {
            appState.config.llm = config;
        }
        applyLlmConfig(config);
    }

    function handleFormInput() {
        markDirty();
    }

    function markDirty() {
        if (!creating && !currentProfile()?.editable) {
            return;
        }
        dirty = true;
        editVersion += 1;
        setDialogStatus("等待自动保存…");
        scheduleAutoSave();
    }

    function scheduleAutoSave() {
        clearAutoSaveTimer();
        autoSaveTimer = window.setTimeout(() => {
            autoSaveTimer = 0;
            if (busy || saving) {
                scheduleAutoSave();
                return;
            }
            void saveProvider({ automatic: true });
        }, 800);
    }

    function clearAutoSaveTimer() {
        if (autoSaveTimer) {
            window.clearTimeout(autoSaveTimer);
            autoSaveTimer = 0;
        }
    }

    function resetDraftState() {
        clearAutoSaveTimer();
        dirty = false;
        editVersion += 1;
    }

    function renderModelOptions(models) {
        if (!DOM.llmProviderModelOptions) {
            return;
        }
        DOM.llmProviderModelOptions.innerHTML = "";
        models.forEach((model) => {
            const option = document.createElement("option");
            option.value = model;
            DOM.llmProviderModelOptions.appendChild(option);
        });
    }

    function setBusy(nextBusy) {
        busy = nextBusy;
        renderProviderPicker();
        setFormEditable(creating || Boolean(currentProfile()?.editable));
    }

    function setDialogStatus(message, isError = false) {
        if (!DOM.llmProviderDialogStatus) {
            return;
        }
        DOM.llmProviderDialogStatus.textContent = message || "";
        DOM.llmProviderDialogStatus.classList.toggle("llm-provider-status-error", isError);
    }

    async function handleEditorSelection() {
        const nextName = DOM.llmProviderEditorSelect?.value || "";
        clearAutoSaveTimer();
        if (dirty && !await saveProvider()) {
            if (DOM.llmProviderEditorSelect) {
                DOM.llmProviderEditorSelect.value = editingName;
            }
            return;
        }
        showProfile(nextName);
    }

    return {
        applyPreset,
        closeDialog,
        deleteProvider,
        discoverModels,
        handleEditorSelection,
        handleFormInput,
        maybeOpenFirstRun,
        openDialog,
        startCreate,
        testProvider,
        useProvider,
    };
}

function setValue(element, value) {
    if (element) {
        element.value = String(value ?? "");
    }
}

function parseObject(rawValue, label) {
    const text = String(rawValue || "").trim() || "{}";
    let value;
    try {
        value = JSON.parse(text);
    } catch (_error) {
        throw new Error(`${label} 必须是合法 JSON。`);
    }
    if (!value || Array.isArray(value) || typeof value !== "object") {
        throw new Error(`${label} 必须是 JSON 对象。`);
    }
    return value;
}

function generateProviderId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
        return `provider-${globalThis.crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
    }
    const timestamp = Date.now().toString(36);
    const randomPart = Math.random().toString(36).slice(2, 10);
    return `provider-${timestamp}-${randomPart}`;
}
