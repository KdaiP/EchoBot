import { DOM } from "../../core/dom.js";
import { audioState, chatState, roleState, sessionState } from "../../core/store.js";
import {
    buildUserMessageContent,
    hasMessageContent,
    messageContentToText,
} from "../../modules/content.js";

export function createChatRunner(deps) {
    const {
        addErrorMessage,
        addMessage,
        applySessionSummaries,
        cancelAgentRun,
        clearComposerAttachments,
        createSpeechSession,
        drainVoicePromptQueue,
        ensureAudioContextReady,
        finalizeSpeechSession,
        queueSpeechSessionText,
        removeMessage,
        requestAgentRun,
        requestAgentRunEvents,
        requestChatStream,
        requestSessionSummaries,
        resetTracePanel,
        setActiveAgentRun,
        setChatBusy,
        setRunStatus,
        speakText,
        startTracePanel,
        stopSpeechPlayback,
        syncCurrentSessionFromServer,
        applyTracePayload,
        updateMessage,
    } = deps;

    async function handleChatSubmit(event) {
        event.preventDefault();
        if (chatState.chatBusy) {
            return;
        }

        const prompt = String(DOM.promptInput?.value || "").trim();
        const composerImages = [...(chatState.composerImages || [])];
        const composerFiles = [...(chatState.composerFiles || [])];
        if (!prompt && composerImages.length === 0 && composerFiles.length === 0) {
            return;
        }

        await ensureAudioContextReady();

        const sessionId = sessionState.currentSessionId;
        DOM.sessionLabel.textContent = `会话: ${sessionState.currentSessionTitle}`;
        window.localStorage.setItem("echobot.web.session", sessionId);

        stopSpeechPlayback();
        setActiveAgentRun("");
        resetTracePanel();
        setChatBusy(true);
        const speechSession = audioState.ttsEnabled ? createSpeechSession() : null;
        setRunStatus("正在请求回复...");

        addMessage(
            "user",
            buildUserMessageContent(
                prompt,
                composerImages.map((image) => ({
                    attachment_id: image.attachmentId,
                    url: image.url,
                    preview_url: image.previewUrl,
                })),
                composerFiles.map((file) => ({
                    attachment_id: file.attachmentId,
                    download_url: file.downloadUrl,
                    name: file.name,
                    content_type: file.contentType,
                    size_bytes: file.sizeBytes,
                    workspace_path: file.workspacePath,
                })),
            ),
            "你",
            { renderMode: "plain" },
        );
        let assistantMessageId = addMessage(
            "assistant",
            "...",
            "Echo",
            { renderMode: "plain" },
        );
        let streamedText = "";

        try {
            const response = await requestChatStream(
                {
                    prompt,
                    session_id: sessionId,
                    role_name: roleState.currentRoleName || "default",
                    route_mode: sessionState.currentRouteMode || "auto",
                    images: composerImages.map((image) => ({
                        attachment_id: image.attachmentId,
                    })),
                    files: composerFiles.map((file) => ({
                        attachment_id: file.attachmentId,
                    })),
                },
                {
                    onChunk(delta) {
                        streamedText += delta;
                        updateMessage(
                            assistantMessageId,
                            streamedText || "...",
                            "Echo",
                            { renderMode: "plain" },
                        );
                        queueSpeechSessionText(speechSession, delta);
                    },
                },
            );
            DOM.promptInput.value = "";
            clearComposerAttachments();

            if (response.session_id) {
                sessionState.currentSessionId = String(response.session_id);
                sessionState.currentSessionTitle = String(response.session_title || "未命名会话");
                DOM.sessionLabel.textContent = `会话: ${sessionState.currentSessionTitle}`;
                window.localStorage.setItem("echobot.web.session", sessionState.currentSessionId);
            }
            roleState.currentRoleName = response.role_name || roleState.currentRoleName;

            const immediateContent = response.response_content ?? response.response ?? streamedText ?? "";
            const immediateText = messageContentToText(
                immediateContent,
                { includeImageMarker: false },
            ).trim();
            const hideImmediateReply = Boolean(
                response.run_id
                && response.status === "running"
                && !hasMessageContent(immediateContent),
            );
            let finalContent = immediateContent;
            let finalText = immediateText || "处理中...";
            let speakFinalText = true;
            const startupSpeech = hideImmediateReply
                ? Promise.resolve()
                : finalizeSpeechSession(speechSession, finalText);
            if (hideImmediateReply) {
                removeMessage(assistantMessageId);
                assistantMessageId = "";
                finalText = "";
            } else {
                updateMessage(
                    assistantMessageId,
                    finalContent,
                    response.completed ? "Echo" : "处理中",
                );
            }

            if (response.run_id && response.status === "running") {
                setActiveAgentRun(response.run_id);
                setRunStatus("Agent 正在后台处理...");
                startTracePanel(response.run_id);

                const finalRun = await pollAgentRun(response.run_id);
                finalContent = finalRun.response_content ?? finalRun.response ?? finalContent;
                finalText = messageContentToText(
                    finalContent,
                    { includeImageMarker: false },
                ).trim() || "任务已结束，但没有返回内容。";
                const runFailed = finalRun.status === "failed";
                if (runFailed) {
                    if (assistantMessageId) {
                        removeMessage(assistantMessageId);
                        assistantMessageId = "";
                    }
                    addErrorMessage(
                        "后台任务失败",
                        String(finalRun.error || "").trim() || finalText,
                    );
                    finalText = "";
                    speakFinalText = false;
                } else if (assistantMessageId) {
                    updateMessage(assistantMessageId, finalContent, "Echo");
                } else {
                    assistantMessageId = addMessage("assistant", finalContent, "Echo");
                }

                await startupSpeech;
                if (finalText === immediateText || finalRun.status === "cancelled") {
                    speakFinalText = false;
                }

                if (finalRun.status === "cancelled") {
                    setRunStatus("后台任务已停止");
                } else if (finalRun.status === "waiting_for_input") {
                    setRunStatus("等待你的补充信息");
                } else if (finalRun.status === "failed") {
                    setRunStatus("后台任务失败");
                } else {
                    setRunStatus("回复已完成");
                }
            } else {
                speakFinalText = false;
                setRunStatus("回复已完成");
            }

            if (audioState.ttsEnabled && speakFinalText && finalText.trim()) {
                await speakText(finalText);
            }

            try {
                applySessionSummaries(await requestSessionSummaries());
            } catch (sessionError) {
                console.error("Failed to refresh session list after chat", sessionError);
            }
            await syncCurrentSessionFromServer({
                force: true,
                announceNewMessages: false,
            });
        } catch (error) {
            console.error(error);
            stopSpeechPlayback();
            if (assistantMessageId && !streamedText.trim()) {
                removeMessage(assistantMessageId);
            }
            addErrorMessage("请求失败", error);
            setRunStatus("请求失败");
        } finally {
            setActiveAgentRun("");
            setChatBusy(false);
            void drainVoicePromptQueue();
        }
    }

    async function pollAgentRun(runId) {
        const maxAttempts = 240;

        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
            const [payload, tracePayload] = await Promise.all([
                requestAgentRun(runId),
                loadAgentRunEvents(runId),
            ]);
            if (tracePayload) {
                applyTracePayload(runId, tracePayload);
            }
            if (payload.status !== "running") {
                return payload;
            }
            await new Promise((resolve) => {
                window.setTimeout(resolve, 1000);
            });
        }

        throw new Error("Agent 后台任务等待超时");
    }

    async function loadAgentRunEvents(runId) {
        try {
            return await requestAgentRunEvents(runId);
        } catch (error) {
            console.warn("Failed to load agent trace", error);
            return null;
        }
    }

    async function handleStopAgentRun() {
        const runId = chatState.activeAgentRunId;
        if (!runId) {
            return;
        }

        if (DOM.stopAgentButton) {
            DOM.stopAgentButton.disabled = true;
        }
        setRunStatus("正在停止后台任务...");

        try {
            const payload = await cancelAgentRun(runId);
            if (payload.status === "cancelled") {
                setRunStatus("后台任务已停止");
                return;
            }
            if (payload.status === "completed") {
                setRunStatus("后台任务已完成");
                return;
            }
            if (payload.status === "failed") {
                const errorMessage = String(payload.error || "").trim();
                if (errorMessage) {
                    addErrorMessage("后台任务失败", errorMessage);
                }
                setRunStatus("后台任务已失败");
                return;
            }
            if (payload.status === "waiting_for_input") {
                setRunStatus("等待你的补充信息");
                return;
            }

            if (DOM.stopAgentButton) {
                DOM.stopAgentButton.disabled = false;
            }
        } catch (error) {
            console.error(error);
            if (DOM.stopAgentButton) {
                DOM.stopAgentButton.disabled = false;
            }
            addMessage("system", `停止后台任务失败：${error.message || error}`, "状态");
            setRunStatus(error.message || "停止后台任务失败");
        }
    }

    return {
        handleChatSubmit,
        handleStopAgentRun,
    };
}
