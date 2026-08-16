import { DOM } from "../../core/dom.js";
import { appState, runtimeState } from "../../core/store.js";

const DEFAULT_SHELL_SAFETY_MODE = "danger-full-access";
const DEFAULT_RUNTIME_CONFIG = Object.freeze({
    delegated_ack_enabled: true,
    shell_safety_mode: DEFAULT_SHELL_SAFETY_MODE,
    file_write_enabled: true,
    cron_mutation_enabled: true,
    web_private_network_enabled: false,
});

const SHELL_SAFETY_MODE_LABELS = {
    "danger-full-access": "full access",
    "workspace-write": "workspace write",
    "read-only": "read only",
};

export function createRuntimeController(deps) {
    const { addMessage, requestJson, setRunStatus } = deps;

    function currentRuntimeConfig() {
        return {
            revision: runtimeState.settingsRevision,
            delegated_ack_enabled: runtimeState.delegatedAckEnabled,
            shell_safety_mode: runtimeState.shellSafetyMode,
            file_write_enabled: runtimeState.fileWriteEnabled,
            cron_mutation_enabled: runtimeState.cronMutationEnabled,
            web_private_network_enabled: runtimeState.webPrivateNetworkEnabled,
        };
    }

    function normalizeRuntimeConfig(runtimeConfig) {
        return {
            delegated_ack_enabled: runtimeConfig
                ? runtimeConfig.delegated_ack_enabled !== false
                : DEFAULT_RUNTIME_CONFIG.delegated_ack_enabled,
            shell_safety_mode: runtimeConfig?.shell_safety_mode
                || DEFAULT_RUNTIME_CONFIG.shell_safety_mode,
            file_write_enabled: runtimeConfig
                ? runtimeConfig.file_write_enabled !== false
                : DEFAULT_RUNTIME_CONFIG.file_write_enabled,
            cron_mutation_enabled: runtimeConfig
                ? runtimeConfig.cron_mutation_enabled !== false
                : DEFAULT_RUNTIME_CONFIG.cron_mutation_enabled,
            web_private_network_enabled: runtimeConfig
                ? runtimeConfig.web_private_network_enabled === true
                : DEFAULT_RUNTIME_CONFIG.web_private_network_enabled,
        };
    }

    function applyRuntimeConfig(runtimeConfig) {
        const normalizedConfig = normalizeRuntimeConfig(runtimeConfig);
        runtimeState.settingsRevision = Number(runtimeConfig?.revision || 0);
        runtimeState.delegatedAckEnabled = normalizedConfig.delegated_ack_enabled;
        runtimeState.shellSafetyMode = normalizedConfig.shell_safety_mode;
        runtimeState.fileWriteEnabled = normalizedConfig.file_write_enabled;
        runtimeState.cronMutationEnabled = normalizedConfig.cron_mutation_enabled;
        runtimeState.webPrivateNetworkEnabled = normalizedConfig.web_private_network_enabled;

        if (DOM.delegatedAckCheckbox) {
            DOM.delegatedAckCheckbox.checked = runtimeState.delegatedAckEnabled;
        }
        if (DOM.shellSafetyModeSelect) {
            DOM.shellSafetyModeSelect.value = runtimeState.shellSafetyMode;
        }
        if (DOM.fileWriteEnabledCheckbox) {
            DOM.fileWriteEnabledCheckbox.checked = runtimeState.fileWriteEnabled;
        }
        if (DOM.cronMutationEnabledCheckbox) {
            DOM.cronMutationEnabledCheckbox.checked = runtimeState.cronMutationEnabled;
        }
        if (DOM.webPrivateNetworkEnabledCheckbox) {
            DOM.webPrivateNetworkEnabledCheckbox.checked = runtimeState.webPrivateNetworkEnabled;
        }
        updateRuntimeControls();
    }

    function applyLlmConfig(llmConfig) {
        const providers = Array.isArray(llmConfig?.providers)
            ? llmConfig.providers
            : [];
        runtimeState.llmConfig = {
            revision: Number(llmConfig?.revision || 0),
            config_revision: Number(llmConfig?.config_revision || 0),
            active_provider: String(llmConfig?.active_provider || ""),
            providers,
        };
        runtimeState.settingsRevision = runtimeState.llmConfig.revision;

        if (!DOM.llmProviderSelect) {
            return;
        }
        DOM.llmProviderSelect.innerHTML = "";
        if (providers.length === 0) {
            const option = document.createElement("option");
            option.value = "";
            option.textContent = "尚未配置 LLM Provider";
            DOM.llmProviderSelect.appendChild(option);
        }
        providers.forEach((provider) => {
            const option = document.createElement("option");
            option.value = provider.name;
            option.textContent = `${provider.label} — ${provider.model}`;
            DOM.llmProviderSelect.appendChild(option);
        });
        DOM.llmProviderSelect.value = runtimeState.llmConfig.active_provider;
        updateRuntimeControls();
    }

    async function handleLlmProviderChange() {
        if (!DOM.llmProviderSelect || runtimeState.llmProviderUpdating) {
            return;
        }
        const previousProvider = runtimeState.llmConfig?.active_provider || "";
        const provider = String(DOM.llmProviderSelect.value || "").trim();
        if (!provider || provider === previousProvider) {
            return;
        }

        runtimeState.llmProviderUpdating = true;
        updateRuntimeControls();
        setRunStatus("Switching LLM provider...");
        try {
            const payload = await requestJson("/api/web/llm/provider", {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    provider,
                    expected_revision: runtimeState.settingsRevision,
                }),
            });
            if (appState.config) {
                appState.config.llm = payload;
            }
            applyLlmConfig(payload);
            setRunStatus(`LLM provider switched to ${provider}`);
        } catch (error) {
            console.error(error);
            DOM.llmProviderSelect.value = previousProvider;
            addMessage(
                "system",
                `Failed to switch LLM provider: ${error.message || error}`,
                "Status",
            );
            setRunStatus(error.message || "Failed to switch LLM provider");
        } finally {
            runtimeState.llmProviderUpdating = false;
            updateRuntimeControls();
        }
    }

    async function handleRuntimeCheckboxToggle(options) {
        const {
            element,
            key,
            currentValue,
            enabledText,
            disabledText,
            settingLabel,
        } = options;
        if (!element || runtimeState.runtimeConfigLoading) {
            return;
        }

        const nextValue = Boolean(element.checked);
        if (nextValue === currentValue) {
            updateRuntimeControls();
            return;
        }

        await saveRuntimeConfig(
            { [key]: nextValue },
            nextValue ? enabledText : disabledText,
            settingLabel,
        );
    }

    async function handleDelegatedAckToggle() {
        await handleRuntimeCheckboxToggle({
            element: DOM.delegatedAckCheckbox,
            key: "delegated_ack_enabled",
            currentValue: runtimeState.delegatedAckEnabled,
            enabledText: "Task-start notice enabled",
            disabledText: "Task-start notice disabled",
            settingLabel: "task-start notice",
        });
    }

    async function handleShellSafetyModeChange() {
        if (!DOM.shellSafetyModeSelect || runtimeState.runtimeConfigLoading) {
            return;
        }

        const nextValue = String(DOM.shellSafetyModeSelect.value || "").trim();
        if (!nextValue || nextValue === runtimeState.shellSafetyMode) {
            updateRuntimeControls();
            return;
        }

        await saveRuntimeConfig(
            { shell_safety_mode: nextValue },
            `Shell safety mode set to ${formatShellSafetyModeLabel(nextValue)}`,
            "shell safety mode",
        );
    }

    async function handleFileWriteToggle() {
        await handleRuntimeCheckboxToggle({
            element: DOM.fileWriteEnabledCheckbox,
            key: "file_write_enabled",
            currentValue: runtimeState.fileWriteEnabled,
            enabledText: "File write tools enabled",
            disabledText: "File write tools disabled",
            settingLabel: "file write tools",
        });
    }

    async function handleCronMutationToggle() {
        await handleRuntimeCheckboxToggle({
            element: DOM.cronMutationEnabledCheckbox,
            key: "cron_mutation_enabled",
            currentValue: runtimeState.cronMutationEnabled,
            enabledText: "CRON mutation enabled",
            disabledText: "CRON mutation disabled",
            settingLabel: "CRON mutation",
        });
    }

    async function handleWebPrivateNetworkToggle() {
        await handleRuntimeCheckboxToggle({
            element: DOM.webPrivateNetworkEnabledCheckbox,
            key: "web_private_network_enabled",
            currentValue: runtimeState.webPrivateNetworkEnabled,
            enabledText: "Private network access enabled",
            disabledText: "Private network access disabled",
            settingLabel: "private network access",
        });
    }

    async function handleRuntimeReset() {
        if (runtimeState.runtimeConfigLoading) {
            return;
        }
        await resetRuntimeConfig();
    }

    async function saveRuntimeConfig(changes, successText, settingLabel) {
        const previousConfig = currentRuntimeConfig();
        runtimeState.runtimeConfigLoading = true;
        updateRuntimeControls();
        setRunStatus(`Updating ${settingLabel}...`);

        try {
            const payload = await requestJson("/api/web/runtime", {
                method: "PATCH",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    ...changes,
                    expected_revision: runtimeState.settingsRevision,
                }),
            });
            if (appState.config) {
                appState.config.runtime = payload;
            }
            applyRuntimeConfig(payload);
            setRunStatus(successText);
        } catch (error) {
            console.error(error);
            applyRuntimeConfig(previousConfig);
            addMessage(
                "system",
                `Failed to update ${settingLabel}: ${error.message || error}`,
                "Status",
            );
            setRunStatus(error.message || `Failed to update ${settingLabel}`);
        } finally {
            runtimeState.runtimeConfigLoading = false;
            updateRuntimeControls();
        }
    }

    async function resetRuntimeConfig() {
        const previousConfig = currentRuntimeConfig();
        runtimeState.runtimeConfigLoading = true;
        updateRuntimeControls();
        setRunStatus("Resetting runtime config...");

        try {
            const payload = await requestJson(
                `/api/web/runtime/reset?expected_revision=${runtimeState.settingsRevision}`,
                { method: "POST" },
            );
            if (appState.config) {
                appState.config.runtime = payload;
            }
            applyRuntimeConfig(payload);
            setRunStatus("Runtime overrides cleared");
        } catch (error) {
            console.error(error);
            applyRuntimeConfig(previousConfig);
            addMessage(
                "system",
                `Failed to reset runtime config: ${error.message || error}`,
                "Status",
            );
            setRunStatus(error.message || "Failed to reset runtime config");
        } finally {
            runtimeState.runtimeConfigLoading = false;
            updateRuntimeControls();
        }
    }

    function formatShellSafetyModeLabel(mode) {
        return SHELL_SAFETY_MODE_LABELS[mode] || mode;
    }

    function updateRuntimeControls() {
        if (DOM.delegatedAckCheckbox) {
            DOM.delegatedAckCheckbox.disabled = runtimeState.runtimeConfigLoading;
        }
        if (DOM.shellSafetyModeSelect) {
            DOM.shellSafetyModeSelect.disabled = runtimeState.runtimeConfigLoading;
        }
        if (DOM.fileWriteEnabledCheckbox) {
            DOM.fileWriteEnabledCheckbox.disabled = runtimeState.runtimeConfigLoading;
        }
        if (DOM.cronMutationEnabledCheckbox) {
            DOM.cronMutationEnabledCheckbox.disabled = runtimeState.runtimeConfigLoading;
        }
        if (DOM.webPrivateNetworkEnabledCheckbox) {
            DOM.webPrivateNetworkEnabledCheckbox.disabled = runtimeState.runtimeConfigLoading;
        }
        if (DOM.runtimeResetButton) {
            DOM.runtimeResetButton.disabled = runtimeState.runtimeConfigLoading;
        }
        if (DOM.llmProviderSelect) {
            const providerCount = runtimeState.llmConfig?.providers?.length || 0;
            DOM.llmProviderSelect.disabled = (
                runtimeState.llmProviderUpdating || providerCount <= 1
            );
        }
    }

    return {
        applyRuntimeConfig,
        applyLlmConfig,
        handleCronMutationToggle,
        handleDelegatedAckToggle,
        handleFileWriteToggle,
        handleLlmProviderChange,
        handleRuntimeReset,
        handleShellSafetyModeChange,
        handleWebPrivateNetworkToggle,
    };
}
