import { DOM } from "../core/dom.js";
import {
    ASR_STATUS_POLL_INTERVAL_MS,
    asrState,
    audioState,
    chatState,
    runtimeState,
} from "../core/store.js";
import { buildWavBlob, createAsrAudioCaptureController } from "./asr/audio.js";
import { findAsrProviderStatus, normalizeAsrConfig } from "./asr/config.js";
import { createVoicePromptQueue } from "./asr/prompts.js";
import { createRealtimeAsrClient } from "./asr/realtime.js";

export function createAsrModule(deps) {
    const {
        addSystemMessage,
        clamp,
        ensureAudioContextReady,
        requestJson,
        responseToError,
        setRunStatus,
        stopSpeechPlayback,
    } = deps;

    const promptQueue = createVoicePromptQueue({
        setRunStatus: setRunStatus,
    });
    const audioCapture = createAsrAudioCaptureController({
        clamp: clamp,
        ensureAudioContextReady: ensureAudioContextReady,
        getTargetSampleRate: currentSampleRate,
        onChunk: handleCapturedPcmChunk,
    });
    const realtimeClient = createRealtimeAsrClient({
        onEvent: handleRealtimeEvent,
        onUnexpectedClose: handleUnexpectedSocketClose,
    });

    function applyAsrStatus(asrConfig) {
        asrState.asrConfig = normalizeAsrConfig(asrConfig);
        renderAsrProviderOptions(asrState.asrConfig);
        if (DOM.asrDetail) {
            DOM.asrDetail.textContent = buildAsrDetailText();
        }
        updateVoiceInputControls();
        if (!shouldPollAsrStatus(asrState.asrConfig)) {
            stopAsrStatusPolling();
        }
    }

    function startAsrStatusPolling() {
        if (asrState.asrStatusPollTimerId || !shouldPollAsrStatus(asrState.asrConfig)) {
            return;
        }

        asrState.asrStatusPollTimerId = window.setInterval(() => {
            void refreshAsrStatus();
        }, ASR_STATUS_POLL_INTERVAL_MS);
    }

    function updateVoiceInputControls() {
        const asrReady = Boolean(asrState.asrConfig && asrState.asrConfig.available);
        const manualRecording = asrState.microphoneCaptureMode === "manual";
        const agentRunActive = Boolean(chatState.activeAgentRunId);

        if (DOM.recordButton) {
            DOM.recordButton.disabled = !manualRecording && (
                !asrReady
                || asrState.alwaysListenEnabled
                || chatState.chatBusy
            );
            DOM.recordButton.classList.toggle("is-recording", manualRecording);
            DOM.recordButton.setAttribute("aria-pressed", manualRecording ? "true" : "false");
            DOM.recordButton.setAttribute("title", manualRecording ? "结束录音" : "开始录音");
            DOM.recordButton.setAttribute("aria-label", manualRecording ? "结束录音" : "开始录音");
        }

        if (DOM.alwaysListenCheckbox) {
            DOM.alwaysListenCheckbox.checked = asrState.alwaysListenEnabled;
            DOM.alwaysListenCheckbox.disabled = !asrReady
                || !(asrState.asrConfig && asrState.asrConfig.always_listen_supported)
                || manualRecording
                || (agentRunActive && !asrState.alwaysListenEnabled);
        }

        if (DOM.asrProviderSelect) {
            const providerCount = asrState.asrConfig && Array.isArray(asrState.asrConfig.asr_providers)
                ? asrState.asrConfig.asr_providers.length
                : 0;
            DOM.asrProviderSelect.disabled = providerCount <= 1
                || manualRecording
                || asrState.asrProviderUpdating;
        }

        if (DOM.asrDetail) {
            DOM.asrDetail.textContent = buildAsrDetailText();
        }
    }

    async function handleAsrProviderChange() {
        if (!DOM.asrProviderSelect || !asrState.asrConfig) {
            return;
        }

        const nextProvider = String(DOM.asrProviderSelect.value || "").trim();
        const currentProvider = String(asrState.asrConfig.selected_asr_provider || "").trim();
        if (!nextProvider || nextProvider === currentProvider) {
            DOM.asrProviderSelect.value = currentProvider;
            return;
        }

        if (asrState.microphoneCaptureMode === "manual") {
            DOM.asrProviderSelect.value = currentProvider;
            addSystemMessage("请先结束当前录音，再切换 ASR 语音识别模型。");
            return;
        }

        if (asrState.alwaysListenEnabled) {
            if (DOM.alwaysListenCheckbox) {
                DOM.alwaysListenCheckbox.checked = false;
            }
            await stopAlwaysListen();
        }

        asrState.asrProviderUpdating = true;
        updateVoiceInputControls();
        setRunStatus("正在切换 ASR 语音识别模型...");

        try {
            const payload = await requestJson("/api/web/asr/provider", {
                method: "PATCH",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    provider: nextProvider,
                    expected_revision: runtimeState.settingsRevision,
                }),
            });
            applyAsrStatus(payload);
            runtimeState.settingsRevision = Number(payload.revision || 0);
            const providerStatus = findAsrProviderStatus(payload, nextProvider);
            setRunStatus(
                providerStatus && providerStatus.available
                    ? `${providerStatus.label} 已启用`
                    : "ASR 语音识别模型已切换，正在等待就绪。",
            );
        } catch (error) {
            console.error(error);
            DOM.asrProviderSelect.value = currentProvider;
            addSystemMessage(`切换 ASR 语音识别模型失败：${error.message || error}`);
            setRunStatus(error.message || "切换 ASR 语音识别模型失败");
        } finally {
            asrState.asrProviderUpdating = false;
            updateVoiceInputControls();
        }
    }

    async function handleRecordButtonClick() {
        if (asrState.microphoneCaptureMode === "manual") {
            await stopManualRecording();
            return;
        }
        await startManualRecording();
    }

    async function handleAlwaysListenToggle() {
        if (!DOM.alwaysListenCheckbox) {
            return;
        }

        if (DOM.alwaysListenCheckbox.checked) {
            try {
                await startAlwaysListen();
            } catch (error) {
                console.error(error);
                DOM.alwaysListenCheckbox.checked = false;
                asrState.alwaysListenEnabled = false;
                asrState.microphoneCaptureMode = "idle";
                updateVoiceInputControls();
                addSystemMessage(`常开麦启动失败：${error.message || error}`);
            }
            return;
        }

        await stopAlwaysListen();
    }

    function handleBeforeUnload() {
        void realtimeClient.close();
        audioCapture.stopMicrophoneCapture();
    }

    return {
        applyAsrStatus: applyAsrStatus,
        drainVoicePromptQueue: promptQueue.drainVoicePromptQueue,
        handleAlwaysListenToggle: handleAlwaysListenToggle,
        handleAsrProviderChange: handleAsrProviderChange,
        handleBeforeUnload: handleBeforeUnload,
        handleRecordButtonClick: handleRecordButtonClick,
        startAsrStatusPolling: startAsrStatusPolling,
        updateVoiceInputControls: updateVoiceInputControls,
    };

    function renderAsrProviderOptions(asrConfig) {
        if (!DOM.asrProviderSelect) {
            return;
        }

        DOM.asrProviderSelect.innerHTML = "";
        const providers = Array.isArray(asrConfig && asrConfig.asr_providers)
            ? asrConfig.asr_providers
            : [];

        providers.forEach((providerStatus) => {
            const option = document.createElement("option");
            option.value = providerStatus.name;
            option.textContent = providerStatus.available
                ? providerStatus.label
                : `${providerStatus.label}（未就绪）`;
            DOM.asrProviderSelect.appendChild(option);
        });

        DOM.asrProviderSelect.disabled = providers.length <= 1 || asrState.asrProviderUpdating;
        if (asrConfig && asrConfig.selected_asr_provider) {
            DOM.asrProviderSelect.value = asrConfig.selected_asr_provider;
        }
    }

    function shouldPollAsrStatus(asrConfig) {
        if (!asrConfig) {
            return true;
        }
        if (!asrConfig.available) {
            return true;
        }
        return Boolean(
            asrConfig.selected_vad_provider
            && !asrConfig.always_listen_supported,
        );
    }

    function buildAsrDetailText() {
        if (asrState.microphoneCaptureMode === "manual") {
            return "正在录音，再次点击麦克风结束。";
        }
        if (asrState.alwaysListenEnabled) {
            return asrState.alwaysListenPaused
                ? "常开麦已开启，回复期间暂停收音。"
                : "常开麦已开启，正在等待你说话。";
        }
        if (!asrState.asrConfig) {
            return "语音识别尚未初始化。";
        }
        return asrState.asrConfig.detail || "语音识别未就绪。";
    }

    function stopAsrStatusPolling() {
        if (!asrState.asrStatusPollTimerId) {
            return;
        }
        window.clearInterval(asrState.asrStatusPollTimerId);
        asrState.asrStatusPollTimerId = 0;
    }

    async function refreshAsrStatus() {
        try {
            applyAsrStatus(await requestJson("/api/web/asr/status"));
        } catch (error) {
            console.error("Failed to refresh ASR status", error);
            if (DOM.asrDetail && !asrState.asrConfig) {
                DOM.asrDetail.textContent = error.message || "语音识别状态获取失败";
            }
        }
    }

    async function startManualRecording() {
        if (!asrState.asrConfig || !asrState.asrConfig.available) {
            addSystemMessage("语音识别还没准备好。");
            return;
        }
        if (chatState.chatBusy) {
            addSystemMessage("当前正在回复，请稍后再录音。");
            return;
        }
        if (asrState.alwaysListenEnabled) {
            if (DOM.alwaysListenCheckbox) {
                DOM.alwaysListenCheckbox.checked = false;
            }
            await stopAlwaysListen();
        }

        if (audioState.activeSpeechSession || audioState.audioSourceNode || audioState.speaking) {
            stopSpeechPlayback();
        }
        await audioCapture.ensureMicrophoneCaptureReady();
        asrState.manualRecordingChunks = [];
        asrState.microphoneCaptureMode = "manual";
        updateVoiceInputControls();
        setRunStatus("正在录音...");
    }

    async function stopManualRecording() {
        if (asrState.microphoneCaptureMode !== "manual") {
            return;
        }

        asrState.microphoneCaptureMode = "idle";
        updateVoiceInputControls();
        const wavBlob = buildWavBlob(asrState.manualRecordingChunks, currentSampleRate());
        asrState.manualRecordingChunks = [];
        audioCapture.stopMicrophoneCapture();

        if (!wavBlob) {
            setRunStatus("未录到有效语音");
            addSystemMessage("未录到有效语音。");
            return;
        }

        try {
            await transcribeAndQueueWavBlob(wavBlob);
        } catch (error) {
            console.error(error);
            addSystemMessage(`语音识别失败：${error.message || error}`);
            setRunStatus(error.message || "语音识别失败");
        }
    }

    async function startAlwaysListen() {
        if (!asrState.asrConfig || !asrState.asrConfig.available) {
            throw new Error("语音识别还没准备好。");
        }
        if (!asrState.asrConfig.always_listen_supported) {
            throw new Error("当前 ASR / VAD 配置不支持常开麦。");
        }
        if (asrState.microphoneCaptureMode === "manual") {
            await stopManualRecording();
        }

        try {
            await audioCapture.ensureMicrophoneCaptureReady();
            await realtimeClient.open();
        } catch (error) {
            audioCapture.stopMicrophoneCapture();
            throw error;
        }

        asrState.alwaysListenEnabled = true;
        asrState.alwaysListenPaused = chatState.chatBusy || audioState.speaking;
        asrState.microphoneCaptureMode = "always";
        updateVoiceInputControls();
        setRunStatus(
            asrState.alwaysListenPaused
                ? "常开麦已开启，回复期间暂停收音"
                : "常开麦已开启",
        );
    }

    async function stopAlwaysListen() {
        asrState.alwaysListenEnabled = false;
        asrState.alwaysListenPaused = false;
        asrState.microphoneCaptureMode = "idle";
        updateVoiceInputControls();

        await realtimeClient.close({ flushFirst: true });
        audioCapture.stopMicrophoneCapture();
        setRunStatus("常开麦已关闭");
    }

    function handleCapturedPcmChunk(pcmChunk) {
        if (asrState.microphoneCaptureMode === "manual") {
            asrState.manualRecordingChunks.push(pcmChunk);
            return;
        }

        if (!asrState.alwaysListenEnabled || asrState.microphoneCaptureMode !== "always") {
            return;
        }

        const shouldPause = chatState.chatBusy || audioState.speaking;
        if (shouldPause) {
            if (!asrState.alwaysListenPaused) {
                asrState.alwaysListenPaused = true;
                realtimeClient.sendControl("reset");
                updateVoiceInputControls();
            }
            return;
        }

        if (asrState.alwaysListenPaused) {
            asrState.alwaysListenPaused = false;
            updateVoiceInputControls();
        }

        realtimeClient.sendChunk(pcmChunk);
    }

    async function transcribeAndQueueWavBlob(wavBlob) {
        setRunStatus("正在识别语音...");
        const response = await fetch("/api/web/asr", {
            method: "POST",
            headers: {
                "Content-Type": "audio/wav",
            },
            body: wavBlob,
        });

        if (!response.ok) {
            throw await responseToError(response);
        }

        const payload = await response.json();
        const text = String((payload && payload.text) || "").trim();
        if (!text) {
            addSystemMessage("没有识别到清晰语音。");
            setRunStatus("没有识别到清晰语音");
            return;
        }

        promptQueue.enqueueVoicePrompt(text, "录音");
    }

    function handleRealtimeEvent(payload) {
        if (payload.type === "ready") {
            if (asrState.asrConfig) {
                applyAsrStatus({
                    ...asrState.asrConfig,
                    available: true,
                    sample_rate: Number(payload.sample_rate) || asrState.asrConfig.sample_rate,
                    state: String(payload.state || "ready"),
                    detail: String(payload.detail || asrState.asrConfig.detail || ""),
                });
            }
            return;
        }
        if (payload.type === "speech_start") {
            setRunStatus("正在听你说...");
            return;
        }
        if (payload.type === "speech_end") {
            setRunStatus("正在识别语音...");
            return;
        }
        if (payload.type === "transcript") {
            promptQueue.enqueueVoicePrompt(payload.text, "语音");
            return;
        }
        if (payload.type === "error") {
            addSystemMessage(`实时语音失败：${payload.message || "未知错误"}`);
        }
    }

    function handleUnexpectedSocketClose() {
        if (!asrState.alwaysListenEnabled) {
            return;
        }

        asrState.alwaysListenEnabled = false;
        asrState.alwaysListenPaused = false;
        asrState.microphoneCaptureMode = "idle";
        if (DOM.alwaysListenCheckbox) {
            DOM.alwaysListenCheckbox.checked = false;
        }
        audioCapture.stopMicrophoneCapture();
        updateVoiceInputControls();
        addSystemMessage("实时语音连接已断开。");
    }

    function currentSampleRate() {
        const sampleRate = Number(asrState.asrConfig && asrState.asrConfig.sample_rate);
        return sampleRate > 0 ? sampleRate : 16000;
    }
}
