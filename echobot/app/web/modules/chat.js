import { buildUserMessageContent } from "./content.js";
import { DEFAULT_SESSION_NAME, DOM, UI_STATE } from "./state.js";

const MAX_COMPOSER_IMAGES = 6;

export function createChatModule(deps) {
    const {
        addMessage,
        applySessionSummaries,
        cancelChatJob,
        createSpeechSession,
        drainVoicePromptQueue,
        ensureAudioContextReady,
        finalizeSpeechSession,
        normalizeSessionName,
        queueSpeechSessionText,
        removeMessage,
        requestChatJob,
        requestChatJobTrace,
        requestChatStream,
        requestSessionSummaries,
        resetTracePanel,
        setActiveBackgroundJob,
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
        if (UI_STATE.chatBusy) {
            return;
        }

        const prompt = String(DOM.promptInput?.value || "").trim();
        const composerImages = [...(UI_STATE.composerImages || [])];
        if (!prompt && composerImages.length === 0) {
            return;
        }

        await ensureAudioContextReady();

        const sessionName = normalizeSessionName(
            UI_STATE.currentSessionName || DEFAULT_SESSION_NAME,
        );
        UI_STATE.currentSessionName = sessionName;
        DOM.sessionLabel.textContent = `会话: ${sessionName}`;
        window.localStorage.setItem("echobot.web.session", sessionName);

        DOM.promptInput.value = "";
        clearComposerImages();
        stopSpeechPlayback();
        setActiveBackgroundJob("");
        resetTracePanel();
        setChatBusy(true);
        const speechSession = UI_STATE.ttsEnabled ? createSpeechSession() : null;
        setRunStatus("正在请求回复...");

        addMessage(
            "user",
            buildUserMessageContent(
                prompt,
                composerImages.map((image) => image.dataUrl),
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
        // 拉取视觉上下文（始终启用，不依赖视频通话开关）
        const visionResp = await fetch('/api/web/video/context').then(r => r.json()).catch(() => null);
        const visionList = visionResp?.vision_list?.length ? visionResp.vision_list : null;

            const chatPayload = {
                prompt: prompt,
                session_name: sessionName,
                role_name: UI_STATE.currentRoleName || "default",
                route_mode: UI_STATE.currentRouteMode || "auto",
                images: composerImages.map((image) => ({
                    data_url: image.dataUrl,
                })),
            };

            // 如果有视觉数据，拼入上下文
            if (visionList && visionList.length > 0) {
                chatPayload.vision_context = visionList;
            }

            const response = await requestChatStream(
                chatPayload,
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

            if (response.session_name) {
                UI_STATE.currentSessionName = normalizeSessionName(response.session_name);
                DOM.sessionLabel.textContent = `会话: ${UI_STATE.currentSessionName}`;
                window.localStorage.setItem("echobot.web.session", UI_STATE.currentSessionName);
            }
            UI_STATE.currentRoleName = response.role_name || UI_STATE.currentRoleName;

            const immediateText = String(response.response || streamedText || "").trim();
            const hideImmediateReply = Boolean(
                response.job_id
                && response.status === "running"
                && !immediateText,
            );
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
                    finalText,
                    response.completed ? "Echo" : "处理中",
                );
            }

            if (response.job_id && response.status === "running") {
                setActiveBackgroundJob(response.job_id);
                setRunStatus("Agent 正在后台处理...");
                startTracePanel(response.job_id);

                const finalJob = await pollChatJob(response.job_id);
                finalText = finalJob.response || finalText || "任务已结束，但没有返回内容。";
                if (assistantMessageId) {
                    updateMessage(assistantMessageId, finalText, "Echo");
                } else {
                    assistantMessageId = addMessage("assistant", finalText, "Echo");
                }

                await startupSpeech;
                if (finalText === immediateText || finalJob.status === "cancelled") {
                    speakFinalText = false;
                }

                if (finalJob.status === "cancelled") {
                    setRunStatus("后台任务已停止");
                } else if (finalJob.status === "failed") {
                    setRunStatus("后台任务失败");
                } else {
                    setRunStatus("回复已完成");
                }
            } else {
                speakFinalText = false;
                setRunStatus("回复已完成");
            }

            if (UI_STATE.ttsEnabled && speakFinalText && finalText.trim()) {
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
            addMessage("system", `请求失败：${error.message || error}`, "状态");
            setRunStatus(error.message || "请求失败");
        } finally {
            setActiveBackgroundJob("");
            setChatBusy(false);
            void drainVoicePromptQueue();
        }
    }

    async function pollChatJob(jobId) {
        const maxAttempts = 240;

        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
            const [payload, tracePayload] = await Promise.all([
                requestChatJob(jobId),
                loadChatJobTrace(jobId),
            ]);
            if (tracePayload) {
                applyTracePayload(jobId, tracePayload);
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

    async function loadChatJobTrace(jobId) {
        try {
            return await requestChatJobTrace(jobId);
        } catch (error) {
            console.warn("Failed to load agent trace", error);
            return null;
        }
    }

    async function handleStopBackgroundJob() {
        const jobId = UI_STATE.activeChatJobId;
        if (!jobId) {
            return;
        }

        if (DOM.stopAgentButton) {
            DOM.stopAgentButton.disabled = true;
        }
        setRunStatus("正在停止后台任务...");

        try {
            const payload = await cancelChatJob(jobId);
            if (payload.status === "cancelled") {
                setRunStatus("后台任务已停止");
                return;
            }
            if (payload.status === "completed") {
                setRunStatus("后台任务已完成");
                return;
            }
            if (payload.status === "failed") {
                setRunStatus("后台任务已失败");
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

    function handleComposerImageButtonClick() {
        if (
            !DOM.composerImageInput
            || UI_STATE.chatBusy
            || UI_STATE.activeChatJobId
        ) {
            return;
        }
        DOM.composerImageInput.click();
    }

    async function handleComposerImageInputChange() {
        if (!DOM.composerImageInput) {
            return;
        }

        const selectedFiles = Array.from(DOM.composerImageInput.files || []);
        DOM.composerImageInput.value = "";
        if (!selectedFiles.length) {
            return;
        }

        try {
            const nextImages = await readComposerImages(selectedFiles);
            if (!nextImages.length) {
                return;
            }

            const existingImages = UI_STATE.composerImages || [];
            const availableSlots = Math.max(
                MAX_COMPOSER_IMAGES - existingImages.length,
                0,
            );
            if (availableSlots <= 0) {
                setRunStatus(`最多只能附加 ${MAX_COMPOSER_IMAGES} 张图片`);
                return;
            }

            const acceptedImages = nextImages.slice(0, availableSlots);
            if (acceptedImages.length < nextImages.length) {
                setRunStatus(`最多只能附加 ${MAX_COMPOSER_IMAGES} 张图片`);
            }
            UI_STATE.composerImages = [...existingImages, ...acceptedImages];
            renderComposerImages();
        } catch (error) {
            console.error("Failed to load composer images", error);
            setRunStatus(error.message || "图片加载失败");
        }
    }

    function handleComposerImagesClick(event) {
        const removeButton = event.target.closest("[data-composer-image-id]");
        if (!removeButton) {
            return;
        }

        const imageId = String(removeButton.dataset.composerImageId || "").trim();
        if (!imageId) {
            return;
        }

        UI_STATE.composerImages = (UI_STATE.composerImages || []).filter(
            (image) => image.id !== imageId,
        );
        renderComposerImages();
    }

    function refreshComposerImages() {
        renderComposerImages();
    }

    return {
        handleChatSubmit: handleChatSubmit,
        handleStopBackgroundJob: handleStopBackgroundJob,
        handleComposerImageButtonClick: handleComposerImageButtonClick,
        handleComposerImageInputChange: handleComposerImageInputChange,
        handleComposerImagesClick: handleComposerImagesClick,
        refreshComposerImages: refreshComposerImages,
    };
}

function clearComposerImages() {
    UI_STATE.composerImages = [];
    renderComposerImages();
}

async function readComposerImages(files) {
    const imageFiles = files.filter((file) => String(file.type || "").startsWith("image/"));
    const nextImages = await Promise.all(
        imageFiles.map(async (file, index) => ({
            id: `img-${Date.now()}-${index}-${Math.random().toString(16).slice(2, 8)}`,
            name: file.name || "image",
            dataUrl: await readFileAsDataUrl(file),
        })),
    );
    return nextImages.filter((image) => String(image.dataUrl || "").trim());
}

function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.addEventListener("load", () => {
            resolve(String(reader.result || ""));
        });
        reader.addEventListener("error", () => {
            reject(reader.error || new Error("Failed to read image file."));
        });
        reader.readAsDataURL(file);
    });
}

function renderComposerImages() {
    if (!DOM.composerImages) {
        return;
    }

    const composerImages = Array.isArray(UI_STATE.composerImages)
        ? UI_STATE.composerImages
        : [];
    DOM.composerImages.innerHTML = "";
    DOM.composerImages.hidden = composerImages.length === 0;

    composerImages.forEach((image) => {
        const card = document.createElement("div");
        card.className = "composer-image-chip";

        const preview = document.createElement("img");
        preview.className = "composer-image-thumb";
        preview.src = image.dataUrl;
        preview.alt = image.name || "Selected image";
        preview.loading = "lazy";
        card.appendChild(preview);

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "composer-image-remove";
        removeButton.dataset.composerImageId = image.id;
        removeButton.textContent = "×";
        removeButton.title = "移除图片";
        removeButton.disabled = UI_STATE.chatBusy || Boolean(UI_STATE.activeChatJobId);
        card.appendChild(removeButton);

        DOM.composerImages.appendChild(card);
    });
}
