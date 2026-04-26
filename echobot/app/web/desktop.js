import { createUiStatusController } from "./bootstrap/ui-status.js";
import { DOM } from "./core/dom.js";
import { appState, asrState, audioState, sessionState } from "./core/store.js";
import { createAsrModule } from "./features/asr.js";
import { createLive2DModule } from "./features/live2d/index.js";
import { createLive2DBroadcastReceiver } from "./features/live2d/broadcast.js";
import {
    findDesktopMouseCaptureIntent,
    getDesktopResizeEdge,
    hasUsableDesktopPassthroughBridge,
    isCursorInsideDesktopWindow,
    isDesktopMouseCaptureRegion,
} from "./features/live2d/desktop-passthrough.js";
import {
    hasUsableDesktopCursorBridge,
    mapScreenPointToStagePoint,
} from "./features/live2d/desktop-cursor.js";
import { createTtsModule } from "./features/tts.js";
import {
    DEFAULT_STAGE_EFFECT_SETTINGS,
    LIVE2D_SELECTION_STORAGE_KEY,
    STAGE_EFFECTS_STORAGE_KEY,
} from "./features/live2d/constants.js";
import {
    addMessage,
    addSystemMessage,
    initializeMessageInteractions,
} from "./modules/messages.js";
import { messageContentToText } from "./modules/content.js";
import {
    clamp,
    delay,
    normalizeSessionName,
    roundTo,
    smoothValue,
} from "./modules/utils.js";
import {
    requestChatJob,
    requestChatStream,
    requestJson,
    responseToError,
} from "./modules/api.js";

const DESKTOP_WEB_URL = "http://127.0.0.1:8000/web";
const DESKTOP_ROUTE_URL = "http://127.0.0.1:8000/desktop";
const DESKTOP_CURSOR_POLL_INTERVAL_MS = 33;
const DESKTOP_LIVE2D_SYNC_POLL_INTERVAL_MS = 2000;
const DESKTOP_PING_INTERVAL_MS = 3000;

let desktopCursorPollTimerId = 0;
let desktopLive2DSyncPollTimerId = 0;
let desktopPingTimerId = 0;
let desktopLive2DSyncInFlight = false;
let desktopMousePassthroughEnabled = null;
let desktopResizeResetTimerId = 0;

const status = createUiStatusController();
const live2d = createLive2DModule({
    clamp,
    requestJson,
    roundTo,
    responseToError,
    setRunStatus: status.setRunStatus,
});
const live2dBroadcastReceiver = createLive2DBroadcastReceiver({
    async onModelChanged(payload) {
        const live2dConfig = payload?.live2dConfig;
        if (!live2dConfig || !live2dConfig.available) {
            return;
        }

        if (!appState.config) {
            return;
        }

        appState.config.live2d = live2dConfig;
        live2d.renderLive2DControls(live2dConfig);
        await live2d.loadLive2DModel(live2dConfig);
    },
    async onExpressionToggled(payload) {
        const live2dConfig = appState.config?.live2d;
        if (!live2dConfig || !live2dConfig.available) {
            return;
        }

        const selectionKey = String(payload?.selectionKey || live2dConfig.selection_key || "").trim();
        const expressionFile = String(payload?.expressionItem?.file || "").trim();
        if (!selectionKey || !expressionFile) {
            return;
        }

        const expressionItem = (live2dConfig.expressions || []).find(
            (item) => item.file === expressionFile,
        ) || payload.expressionItem;
        if (!expressionItem) {
            return;
        }

        const desiredActive = Boolean(payload?.active);
        const currentActive = live2d.isExpressionActive(selectionKey, expressionFile);
        if (currentActive === desiredActive) {
            return;
        }

        await live2d.toggleExpression(expressionItem, selectionKey);
        live2d.renderLive2DControls(live2dConfig);
    },
    async onMotionPlayed(payload) {
        const live2dConfig = appState.config?.live2d;
        if (!live2dConfig || !live2dConfig.available) {
            return;
        }

        const selectionKey = String(payload?.selectionKey || live2dConfig.selection_key || "").trim();
        const motionFile = String(payload?.motionItem?.file || "").trim();
        if (!selectionKey || !motionFile) {
            return;
        }

        const motionItem = (live2dConfig.motions || []).find(
            (item) => item.file === motionFile,
        ) || payload.motionItem;
        if (!motionItem) {
            return;
        }

        await live2d.playMotion(motionItem, selectionKey);
    },
    onMouthValue(payload) {
        const mouthValue = Number(payload?.value);
        if (!Number.isFinite(mouthValue)) {
            return;
        }

        live2d.applyMouthValue(appState.config?.live2d, mouthValue);
    },
    onMouseFollowChanged(payload) {
        const enabled = Boolean(payload?.enabled);
        live2d.setLive2DMouseFollowEnabled(enabled);
        if (DOM.live2dMouseFollowCheckbox) {
            DOM.live2dMouseFollowCheckbox.checked = enabled;
        }
    },
});
const tts = createTtsModule({
    addMessage,
    applyMouthValue: live2d.applyMouthValue,
    clamp,
    requestJson,
    responseToError,
    setConnectionState: status.setConnectionState,
    setRunStatus: status.setRunStatus,
    smoothValue,
});
const asr = createAsrModule({
    addSystemMessage,
    clamp,
    delay,
    ensureAudioContextReady: tts.ensureAudioContextReady,
    requestJson,
    responseToError,
    setRunStatus: status.setRunStatus,
    stopSpeechPlayback: tts.stopSpeechPlayback,
});

tts.bindHooks({
    updateVoiceInputControls() {
        asr.updateVoiceInputControls();
        renderVoiceButton();
    },
});

document.addEventListener("DOMContentLoaded", initializeDesktopPage);

async function initializeDesktopPage() {
    initializeMessageInteractions();
    wireDesktopEvents();
    applyDesktopStageDefaults();
    await setDesktopMousePassthrough(true);
    status.setConnectionState("idle", "初始化中");
    status.setRunStatus("正在连接 EchoBot…");
    setVoiceDetail("语音待命");
    DOM.stopAudioButton.disabled = true;

    try {
        const config = await requestJson("/api/web/config");
        appState.config = config;
        sessionState.currentSessionName = normalizeSessionName(config.session_name);
        DOM.sessionLabel.textContent = `会话: ${sessionState.currentSessionName}`;

        const live2dConfig = live2d.applyConfigToUI(config);
        applyDesktopStageDefaults();
        live2d.initializePixiApplication();
        await live2d.loadLive2DModel(live2dConfig);
        startDesktopCursorPolling();
        startDesktopLive2DSyncPolling();
        startDesktopPing();

        await tts.loadTtsOptions(config.tts);
        asr.applyAsrStatus(config.asr);
        asr.startAsrStatusPolling();

        status.setConnectionState("ready", "已连接");
        status.setRunStatus("桌宠已就绪");
        renderVoiceButton();
    } catch (error) {
        console.error(error);
        status.setConnectionState("error", "初始化失败");
        status.setRunStatus(error.message || "初始化失败");
        live2d.setStageMessage(error.message || "初始化失败");
        setVoiceDetail("语音不可用");
    }
}

function wireDesktopEvents() {
    DOM.chatForm?.addEventListener("submit", handleChatSubmit);
    DOM.desktopVoiceButton?.addEventListener("click", handleDesktopVoiceButtonClick);
    DOM.desktopWebButton?.addEventListener("click", handleDesktopWebButtonClick);
    DOM.desktopDragButton?.addEventListener("pointerdown", handleDesktopDragStart);
    document.addEventListener("pointerover", handleDesktopPointerOver, true);
    document.addEventListener("pointerout", handleDesktopPointerOut, true);
    DOM.recordButton?.addEventListener("click", () => {
        void asr.handleRecordButtonClick();
        renderVoiceButton();
    });
    DOM.alwaysListenCheckbox?.addEventListener("change", () => {
        void asr.handleAlwaysListenToggle();
        renderVoiceButton();
    });
    DOM.stopAudioButton?.addEventListener("click", () => {
        tts.stopSpeechPlayback();
        renderVoiceButton();
    });
    DOM.resetViewButton?.addEventListener("click", () => {
        live2d.resetLive2DViewToDefault();
    });
    DOM.stageElement?.addEventListener("wheel", live2d.handleStageWheel, { passive: false });
    window.addEventListener("resize", handleDesktopWindowResize);

    window.addEventListener("beforeunload", () => {
        stopDesktopCursorPolling();
        stopDesktopLive2DSyncPolling();
        stopDesktopPing();
        asr.handleBeforeUnload();
        tts.stopSpeechPlayback();
        live2dBroadcastReceiver.close();
        if (desktopResizeResetTimerId) {
            window.clearTimeout(desktopResizeResetTimerId);
            desktopResizeResetTimerId = 0;
        }
        window.removeEventListener("resize", handleDesktopWindowResize);
        document.removeEventListener("pointerover", handleDesktopPointerOver, true);
        document.removeEventListener("pointerout", handleDesktopPointerOut, true);
        DOM.stageElement?.removeEventListener("wheel", live2d.handleStageWheel);
    });
}

function handleDesktopPointerOver(event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!isDesktopMouseCaptureRegion(target)) {
        return;
    }

    void setDesktopMousePassthrough(false);
}

function handleDesktopPointerOut(event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!isDesktopMouseCaptureRegion(target)) {
        return;
    }

    const nextTarget = event.relatedTarget instanceof Element ? event.relatedTarget : null;
    if (isDesktopMouseCaptureRegion(nextTarget)) {
        return;
    }

    void setDesktopMousePassthrough(true);
}

async function setDesktopMousePassthrough(enabled) {
    const passthrough = Boolean(enabled);
    if (desktopMousePassthroughEnabled === passthrough) {
        return passthrough;
    }
    if (!hasUsableDesktopPassthroughBridge(window.echobotDesktop)) {
        desktopMousePassthroughEnabled = passthrough;
        return passthrough;
    }

    try {
        const applied = await window.echobotDesktop.setMousePassthrough(passthrough);
        desktopMousePassthroughEnabled = Boolean(applied);
    } catch (error) {
        console.error(error);
    }

    return desktopMousePassthroughEnabled;
}

function startDesktopCursorPolling() {
    stopDesktopCursorPolling();

    if (!hasUsableDesktopCursorBridge(window.echobotDesktop)) {
        return;
    }

    desktopCursorPollTimerId = window.setInterval(() => {
        void syncDesktopCursorFocus();
    }, DESKTOP_CURSOR_POLL_INTERVAL_MS);
}

function stopDesktopCursorPolling() {
    if (!desktopCursorPollTimerId) {
        return;
    }

    window.clearInterval(desktopCursorPollTimerId);
    desktopCursorPollTimerId = 0;
}

function startDesktopLive2DSyncPolling() {
    stopDesktopLive2DSyncPolling();

    desktopLive2DSyncPollTimerId = window.setInterval(() => {
        void syncDesktopLive2DSelectionFromServer();
    }, DESKTOP_LIVE2D_SYNC_POLL_INTERVAL_MS);
}

function stopDesktopLive2DSyncPolling() {
    if (!desktopLive2DSyncPollTimerId) {
        return;
    }

    window.clearInterval(desktopLive2DSyncPollTimerId);
    desktopLive2DSyncPollTimerId = 0;
}

function stopDesktopPing() {
    if (!desktopPingTimerId) {
        return;
    }

    window.clearInterval(desktopPingTimerId);
    desktopPingTimerId = 0;
}

function startDesktopPing() {
    stopDesktopPing();

    const ping = () => {
        void fetch("/api/web/desktop/ping", { method: "POST" }).catch((error) => {
            console.warn("Desktop presence ping failed", error);
        });
    };

    ping();
    desktopPingTimerId = window.setInterval(ping, DESKTOP_PING_INTERVAL_MS);
}

async function syncDesktopLive2DSelectionFromServer() {
    if (desktopLive2DSyncInFlight) {
        return;
    }

    if (!appState.config?.live2d?.available) {
        return;
    }

    desktopLive2DSyncInFlight = true;
    try {
        const config = await requestJson("/api/web/config");
        const serverSelectionKey = String(config?.live2d?.selection_key || "").trim();
        const localSelectionKey = String(appState.config?.live2d?.selection_key || "").trim();
        if (!serverSelectionKey || serverSelectionKey === localSelectionKey) {
            return;
        }

        const serverLive2DConfig = config.live2d;
        appState.config = config;
        window.localStorage.setItem(LIVE2D_SELECTION_STORAGE_KEY, serverSelectionKey);
        live2d.renderLive2DControls(serverLive2DConfig);
        await live2d.loadLive2DModel(serverLive2DConfig);
        status.setRunStatus(`桌宠已同步模型：${serverLive2DConfig.model_name || serverSelectionKey}`);
    } catch (error) {
        console.warn("Desktop Live2D sync polling failed", error);
    } finally {
        desktopLive2DSyncInFlight = false;
    }
}

async function syncDesktopCursorFocus() {
    if (
        !hasUsableDesktopCursorBridge(window.echobotDesktop)
        || !DOM.stageElement
        || !appState.config?.live2d?.available
    ) {
        return;
    }

    const cursorState = await window.echobotDesktop.getGlobalCursorState();
    if (!cursorState) {
        return;
    }

    const stageRect = DOM.stageElement.getBoundingClientRect();
    const stagePoint = mapScreenPointToStagePoint(
        cursorState,
        cursorState.windowBounds,
        stageRect,
    );
    const captureIntent = findDesktopMouseCaptureIntent(
        cursorState,
        DOM.stageElement,
        stageRect,
    );
    setDesktopHoverState(isCursorInsideDesktopWindow(cursorState));
    void setDesktopMousePassthrough(!captureIntent.capture);
    if (!stagePoint) {
        return;
    }

    live2d.setDesktopCursorOverlapFromStagePoint(stagePoint.x, stagePoint.y);
    live2d.applyExternalFocusPoint(stagePoint.x, stagePoint.y);
}

function setDesktopHoverState(isHovered) {
    const hovered = Boolean(isHovered);
    DOM.stageElement?.classList.toggle("is-desktop-hovered", hovered);
    document.getElementById("desktop-stage")?.classList.toggle("is-desktop-hovered", hovered);
}

function applyDesktopStageDefaults() {
    window.localStorage.setItem(
        STAGE_EFFECTS_STORAGE_KEY,
        JSON.stringify({
            ...DEFAULT_STAGE_EFFECT_SETTINGS,
            enabled: false,
            backgroundBlurEnabled: false,
            lightEnabled: false,
            lightFloatEnabled: false,
            particlesEnabled: false,
            grainStrength: 0,
            vignetteStrength: 0,
            glowStrength: 0,
        }),
    );

    if (DOM.stageBackgroundImage) {
        DOM.stageBackgroundImage.hidden = true;
        DOM.stageBackgroundImage.style.backgroundImage = "";
    }
    if (DOM.stageElement) {
        DOM.stageElement.classList.remove("has-custom-background");
        DOM.stageElement.classList.remove("has-stage-color-adjustment");
    }
    if (DOM.stageLightBack) {
        DOM.stageLightBack.style.opacity = "0";
    }
    if (DOM.stageLightRim) {
        DOM.stageLightRim.style.opacity = "0";
    }
    if (DOM.stageGradient) {
        DOM.stageGradient.style.opacity = "0";
    }
    if (DOM.stageVignette) {
        DOM.stageVignette.style.opacity = "0";
    }
    if (DOM.stageGrain) {
        DOM.stageGrain.style.opacity = "0";
    }
}

async function handleDesktopVoiceButtonClick() {
    if (audioState.speaking || audioState.audioSourceNode) {
        tts.stopSpeechPlayback();
        status.setRunStatus("语音播报已停止");
        renderVoiceButton();
        return;
    }

    await asr.handleRecordButtonClick();
    renderVoiceButton();
}

async function handleChatSubmit(event) {
    event.preventDefault();

    const prompt = String(DOM.promptInput?.value || "").trim();
    if (!prompt) {
        return;
    }

    await tts.ensureAudioContextReady();
    tts.stopSpeechPlayback();
    audioState.ttsEnabled = true;
    status.setChatBusy(true);
    status.setRunStatus("正在请求回复...");
    setVoiceDetail("对话进行中");

    const sessionName = normalizeSessionName(sessionState.currentSessionName || "default");
    sessionState.currentSessionName = sessionName;
    DOM.sessionLabel.textContent = `会话: ${sessionName}`;

    const speechSession = tts.createSpeechSession();
    let streamedText = "";

    try {
        const response = await requestChatStream(
            {
                prompt,
                session_name: sessionName,
            },
            {
                onChunk(delta) {
                    streamedText += delta;
                    tts.queueSpeechSessionText(speechSession, delta);
                },
            },
        );

        DOM.promptInput.value = "";

        let finalText = messageContentToText(
            response.response_content ?? response.response ?? streamedText ?? "",
            { includeImageMarker: false },
        ).trim();

        await tts.finalizeSpeechSession(speechSession, finalText);

        if (response.job_id && response.status === "running") {
            status.setRunStatus("Agent 正在后台处理...");
            const finalJob = await pollChatJob(response.job_id);
            finalText = messageContentToText(
                finalJob.response_content ?? finalJob.response ?? finalText,
                { includeImageMarker: false },
            ).trim();
            if (finalText && !audioState.speaking) {
                await tts.speakText(finalText);
            }
        }

        status.setRunStatus("回复已完成");
        setVoiceDetail(finalText ? shortenText(finalText) : "回复已完成");
    } catch (error) {
        console.error(error);
        tts.stopSpeechPlayback();
        status.setRunStatus(error.message || "请求失败");
        setVoiceDetail("语音失败");
    } finally {
        status.setChatBusy(false);
        renderVoiceButton();
    }
}

async function pollChatJob(jobId) {
    for (let attempt = 0; attempt < 240; attempt += 1) {
        const payload = await requestChatJob(jobId);
        if (payload.status !== "running") {
            return payload;
        }
        await delay(1000);
    }

    throw new Error("后台任务等待超时");
}

function renderVoiceButton() {
    if (!DOM.desktopVoiceButton) {
        return;
    }

    const recording = liveRecording();
    const speaking = Boolean(audioState.speaking || audioState.audioSourceNode);
    DOM.desktopVoiceButton.classList.toggle("is-active", recording || speaking);
    DOM.desktopVoiceButton.setAttribute("aria-pressed", recording || speaking ? "true" : "false");
    DOM.desktopVoiceButton.setAttribute(
        "title",
        speaking ? "停止播报" : (recording ? "结束录音" : "语音服务"),
    );
    DOM.desktopVoiceButton.setAttribute(
        "aria-label",
        speaking ? "停止播报" : (recording ? "结束录音" : "语音服务"),
    );

    if (speaking) {
        setVoiceDetail("正在播报");
        return;
    }
    if (recording) {
        setVoiceDetail("正在录音");
        return;
    }

    if (!String(DOM.desktopVoiceDetail?.textContent || "").trim()) {
        setVoiceDetail("语音待命");
    }
}

function liveRecording() {
    return asrState.microphoneCaptureMode === "manual";
}

function setVoiceDetail(text) {
    if (DOM.desktopVoiceDetail) {
        DOM.desktopVoiceDetail.textContent = String(text || "").trim();
    }
}

async function handleDesktopWebButtonClick() {
    if (window.echobotDesktop && typeof window.echobotDesktop.openControlPanel === "function") {
        await window.echobotDesktop.openControlPanel();
        return;
    }

    window.open(DESKTOP_WEB_URL, "_blank", "noopener,noreferrer");
}

function handleDesktopDragStart() {
    if (window.echobotDesktop && typeof window.echobotDesktop.startWindowDrag === "function") {
        void window.echobotDesktop.startWindowDrag();
    }
}

function shortenText(text) {
    const clean = String(text || "").replace(/\s+/g, " ").trim();
    if (clean.length <= 28) {
        return clean;
    }
    return `${clean.slice(0, 27)}…`;
}

function handleDesktopWindowResize() {
    if (desktopResizeResetTimerId) {
        window.clearTimeout(desktopResizeResetTimerId);
    }

    desktopResizeResetTimerId = window.setTimeout(() => {
        desktopResizeResetTimerId = 0;
        live2d.resetLive2DViewToDefault();
    }, 120);
}

if (!DOM.stageElement) {
    DOM.stageElement = document.getElementById("desktop-stage");
}
if (!DOM.chatForm) {
    DOM.chatForm = document.getElementById("chat-form");
}
if (!DOM.desktopDragButton) {
    DOM.desktopDragButton = document.getElementById("desktop-drag-button");
}
if (!DOM.desktopVoiceButton) {
    DOM.desktopVoiceButton = document.getElementById("desktop-voice-button");
}
if (!DOM.desktopWebButton) {
    DOM.desktopWebButton = document.getElementById("desktop-web-button");
}
if (!DOM.desktopVoiceDetail) {
    DOM.desktopVoiceDetail = document.getElementById("desktop-voice-detail");
}

window.__ECHOBOT_DESKTOP_ROUTE__ = DESKTOP_ROUTE_URL;
