import { DOM } from "../../core/dom.js";
import {
    TTS_STREAM_FIRST_SEGMENT_SENTENCES,
    appState,
    audioState,
    live2dState,
} from "../../core/store.js";
import { drainSpeechSessionSegments, prepareTextForTts } from "./text.js";

export function createTtsPlaybackController(deps) {
    const {
        addMessage,
        applyMouthValue,
        clamp,
        getHooks,
        responseToError,
        setConnectionState,
        setRunStatus,
        smoothValue,
    } = deps;

    async function speakText(text, options = {}) {
        const preparedText = prepareTextForTts(text);
        if (!preparedText || !audioState.ttsEnabled || audioState.desktopActive) {
            return;
        }

        stopSpeechPlayback();
        const speechSession = createSpeechSession();
        enqueueSpeechSegment(speechSession, preparedText);
        const completionPromise = finalizeSpeechSession(speechSession);

        if (Boolean(options.waitUntilEnd)) {
            await completionPromise;
            return;
        }

        await waitForSpeechSessionStart(speechSession);
    }

    function createSpeechSession() {
        if (audioState.desktopActive) {
            return null;
        }

        const speechSession = {
            turnId: ++audioState.speechTurnCounter,
            rawText: "",
            pendingText: "",
            nextSentenceTarget: TTS_STREAM_FIRST_SEGMENT_SENTENCES,
            queue: [],
            nextPlaybackIndex: 0,
            finalized: false,
            cancelled: false,
            eventResolvers: [],
            firstPlaybackStarted: false,
            resolveFirstPlaybackStarted: null,
            firstPlaybackStartedPromise: null,
            playbackPromise: null,
        };

        speechSession.firstPlaybackStartedPromise = new Promise((resolve) => {
            speechSession.resolveFirstPlaybackStarted = resolve;
        });

        audioState.activeSpeechSession = speechSession;
        DOM.stopAudioButton.disabled = false;
        return speechSession;
    }

    function cancelSpeechSession(speechSession) {
        if (!speechSession || speechSession.cancelled) {
            return;
        }

        speechSession.cancelled = true;
        speechSession.finalized = true;
        resolveSpeechSessionStart(speechSession);
        notifySpeechSessionEvent(speechSession);

        if (audioState.activeSpeechSession === speechSession) {
            audioState.activeSpeechSession = null;
        }
    }

    function queueSpeechSessionText(speechSession, delta) {
        if (!speechSession || speechSession.cancelled || !audioState.ttsEnabled) {
            return;
        }

        const text = String(delta || "");
        if (!text) {
            return;
        }

        speechSession.rawText += text;
        speechSession.pendingText += text;
        drainSpeechSessionSegments(speechSession, false, (segmentText) => {
            enqueueSpeechSegment(speechSession, segmentText);
        });
    }

    function finalizeSpeechSession(speechSession, finalText = "") {
        if (!speechSession || speechSession.cancelled || !audioState.ttsEnabled) {
            return Promise.resolve();
        }

        appendSpeechSessionFinalText(speechSession, finalText);
        speechSession.finalized = true;
        drainSpeechSessionSegments(speechSession, true, (segmentText) => {
            enqueueSpeechSegment(speechSession, segmentText);
        });
        notifySpeechSessionEvent(speechSession);

        if (!speechSession.queue.length) {
            resolveSpeechSessionStart(speechSession);
            if (audioState.activeSpeechSession === speechSession) {
                audioState.activeSpeechSession = null;
            }
            DOM.stopAudioButton.disabled = true;
            setConnectionState("ready", "已连接");
            return Promise.resolve();
        }

        return waitForSpeechSession(speechSession);
    }

    function appendSpeechSessionFinalText(speechSession, finalText) {
        const finalValue = String(finalText || "");
        if (!finalValue) {
            return;
        }

        if (!speechSession.rawText) {
            speechSession.rawText = finalValue;
            speechSession.pendingText += finalValue;
            return;
        }

        if (finalValue.startsWith(speechSession.rawText)) {
            const suffix = finalValue.slice(speechSession.rawText.length);
            if (suffix) {
                speechSession.pendingText += suffix;
            }
            speechSession.rawText = finalValue;
            return;
        }

        if (!speechSession.queue.length && !speechSession.pendingText.trim()) {
            speechSession.pendingText = finalValue;
        }
        speechSession.rawText = finalValue;
    }

    function enqueueSpeechSegment(speechSession, text) {
        const preparedText = prepareTextForTts(text);
        if (!preparedText) {
            return;
        }

        speechSession.queue.push({
            audioBufferPromise: synthesizeSpeechAudioBuffer(
                preparedText,
                speechSession.turnId,
            ),
        });
        DOM.stopAudioButton.disabled = false;
        ensureSpeechSessionPlayback(speechSession);
    }

    function ensureSpeechSessionPlayback(speechSession) {
        if (!speechSession.playbackPromise) {
            speechSession.playbackPromise = runSpeechSessionPlaybackLoop(speechSession);
        }
        return speechSession.playbackPromise;
    }

    function waitForSpeechSession(speechSession) {
        if (!speechSession) {
            return Promise.resolve();
        }
        return speechSession.playbackPromise || Promise.resolve();
    }

    async function waitForSpeechSessionStart(speechSession) {
        if (!speechSession) {
            return;
        }

        await Promise.race([
            speechSession.firstPlaybackStartedPromise,
            waitForSpeechSession(speechSession),
        ]);
    }

    async function runSpeechSessionPlaybackLoop(speechSession) {
        try {
            while (true) {
                if (isSpeechSessionInactive(speechSession)) {
                    break;
                }

                if (speechSession.nextPlaybackIndex < speechSession.queue.length) {
                    const item = speechSession.queue[speechSession.nextPlaybackIndex];
                    DOM.stopAudioButton.disabled = false;

                    let audioBuffer;
                    try {
                        audioBuffer = await item.audioBufferPromise;
                    } catch (error) {
                        if (!isSpeechSessionInactive(speechSession)) {
                            reportSpeechError(error);
                        }
                        break;
                    }

                    if (isSpeechSessionInactive(speechSession)) {
                        break;
                    }

                    speechSession.nextPlaybackIndex += 1;
                    if (!audioBuffer) {
                        continue;
                    }

                    resolveSpeechSessionStart(speechSession);

                    try {
                        await playSpeechAudioBuffer(audioBuffer, speechSession.turnId);
                    } catch (error) {
                        if (!isSpeechSessionInactive(speechSession)) {
                            reportSpeechError(error);
                        }
                        break;
                    }
                    continue;
                }

                if (speechSession.finalized) {
                    break;
                }

                await waitForSpeechSessionEvent(speechSession);
            }
        } finally {
            resolveSpeechSessionStart(speechSession);
            if (audioState.activeSpeechSession === speechSession) {
                audioState.activeSpeechSession = null;
            }
            if (!audioState.audioSourceNode && !audioState.activeSpeechSession) {
                DOM.stopAudioButton.disabled = true;
                setConnectionState("ready", "已连接");
            }
            notifySpeechSessionEvent(speechSession);
        }
    }

    function isSpeechSessionInactive(speechSession) {
        return (
            !speechSession
            || speechSession.cancelled
            || audioState.activeSpeechSession !== speechSession
        );
    }

    function waitForSpeechSessionEvent(speechSession) {
        return new Promise((resolve) => {
            speechSession.eventResolvers.push(resolve);
        });
    }

    function notifySpeechSessionEvent(speechSession) {
        if (!speechSession || speechSession.eventResolvers.length === 0) {
            return;
        }

        const resolvers = speechSession.eventResolvers.splice(0);
        resolvers.forEach((resolve) => resolve());
    }

    function resolveSpeechSessionStart(speechSession) {
        if (!speechSession || speechSession.firstPlaybackStarted) {
            return;
        }

        speechSession.firstPlaybackStarted = true;
        if (speechSession.resolveFirstPlaybackStarted) {
            speechSession.resolveFirstPlaybackStarted();
            speechSession.resolveFirstPlaybackStarted = null;
        }
    }

    async function synthesizeSpeechAudioBuffer(text, turnId) {
        if (!isSpeechTurnActive(turnId)) {
            return null;
        }

        await ensureAudioContextReady();
        if (!isSpeechTurnActive(turnId)) {
            return null;
        }

        setConnectionState("busy", "语音合成中...");
        DOM.stopAudioButton.disabled = false;

        const response = await fetch("/api/web/tts", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                text: text,
                provider: audioState.selectedTtsProvider || appState.config.tts.default_provider,
                voice: audioState.selectedVoice
                    || (appState.config.tts.default_voices || {})[audioState.selectedTtsProvider]
                    || appState.config.tts.default_voice,
            }),
        });

        if (!response.ok) {
            throw await responseToError(response);
        }
        if (!isSpeechTurnActive(turnId)) {
            return null;
        }

        const audioBytes = await response.arrayBuffer();
        if (!isSpeechTurnActive(turnId)) {
            return null;
        }

        return await audioState.audioContext.decodeAudioData(audioBytes.slice(0));
    }

    async function playSpeechAudioBuffer(audioBuffer, turnId) {
        if (!audioBuffer || !isSpeechTurnActive(turnId)) {
            return;
        }

        const sourceNode = audioState.audioContext.createBufferSource();
        const analyserNode = audioState.audioContext.createAnalyser();
        analyserNode.fftSize = 1024;

        sourceNode.buffer = audioBuffer;
        sourceNode.connect(analyserNode);
        analyserNode.connect(audioState.audioContext.destination);

        audioState.audioSourceNode = sourceNode;
        audioState.audioAnalyser = analyserNode;
        audioState.volumeBuffer = new Uint8Array(analyserNode.fftSize);
        audioState.speaking = true;
        getHooks().updateVoiceInputControls();

        const playbackEnded = new Promise((resolve) => {
            audioState.speechEndedResolver = resolve;
        });

        sourceNode.onended = () => {
            clearSpeechState();
        };

        startLipSyncLoop();
        sourceNode.start(0);
        setRunStatus("正在播报回复");
        await playbackEnded;
    }

    function isSpeechTurnActive(turnId) {
        const speechSession = audioState.activeSpeechSession;
        return Boolean(
            speechSession
            && speechSession.turnId === turnId
            && !speechSession.cancelled
            && audioState.ttsEnabled,
        );
    }

    function reportSpeechError(error) {
        console.error(error);
        clearSpeechState();
        DOM.stopAudioButton.disabled = true;
        setRunStatus(error.message || "语音播放失败");
        addMessage("system", `TTS 失败：${error.message || error}`, "状态");
    }

    function stopSpeechPlayback() {
        cancelSpeechSession(audioState.activeSpeechSession);
        if (audioState.audioSourceNode) {
            try {
                audioState.audioSourceNode.stop();
            } catch (error) {
                console.warn("Audio stop ignored", error);
            }
        }
        clearSpeechState();
        DOM.stopAudioButton.disabled = true;
        setConnectionState("ready", "已连接");
    }

    function clearSpeechState() {
        if (audioState.audioSourceNode) {
            try {
                audioState.audioSourceNode.disconnect();
            } catch (error) {
                console.warn("Audio source disconnect ignored", error);
            }
        }
        if (audioState.audioAnalyser) {
            try {
                audioState.audioAnalyser.disconnect();
            } catch (error) {
                console.warn("Audio analyser disconnect ignored", error);
            }
        }

        audioState.audioSourceNode = null;
        audioState.audioAnalyser = null;
        audioState.volumeBuffer = null;
        audioState.speaking = false;
        live2dState.currentMouthValue = 0;
        getHooks().updateVoiceInputControls();

        if (live2dState.lipSyncFrameId) {
            window.cancelAnimationFrame(live2dState.lipSyncFrameId);
            live2dState.lipSyncFrameId = 0;
        }

        if (appState.config && appState.config.live2d) {
            applyMouthValue(appState.config.live2d, 0);
        }

        const hasPendingSpeech = Boolean(audioState.activeSpeechSession);
        DOM.stopAudioButton.disabled = !hasPendingSpeech;
        setConnectionState(
            hasPendingSpeech ? "busy" : "ready",
            hasPendingSpeech ? "语音合成中..." : "已连接",
        );
        resolveSpeechWaiter();
    }

    function resolveSpeechWaiter() {
        if (!audioState.speechEndedResolver) {
            return;
        }

        const resolve = audioState.speechEndedResolver;
        audioState.speechEndedResolver = null;
        resolve();
    }

    function startLipSyncLoop() {
        if (!audioState.audioAnalyser || !audioState.volumeBuffer) {
            return;
        }

        const updateFrame = () => {
            if (!audioState.audioAnalyser || !audioState.volumeBuffer || !audioState.speaking) {
                return;
            }

            audioState.audioAnalyser.getByteTimeDomainData(audioState.volumeBuffer);

            let total = 0;
            for (let index = 0; index < audioState.volumeBuffer.length; index += 1) {
                const sample = (audioState.volumeBuffer[index] - 128) / 128;
                total += sample * sample;
            }

            const rms = Math.sqrt(total / audioState.volumeBuffer.length);
            const scaledValue = clamp((rms - 0.02) * 5.4, 0, 1);
            live2dState.currentMouthValue = smoothValue(
                live2dState.currentMouthValue,
                scaledValue,
                0.38,
            );

            const live2dConfig = appState.config && appState.config.live2d
                ? appState.config.live2d
                : null;
            applyMouthValue(live2dConfig, live2dState.currentMouthValue);
            live2dState.lipSyncFrameId = window.requestAnimationFrame(updateFrame);
        };

        if (live2dState.lipSyncFrameId) {
            window.cancelAnimationFrame(live2dState.lipSyncFrameId);
        }
        live2dState.lipSyncFrameId = window.requestAnimationFrame(updateFrame);
    }

    async function ensureAudioContextReady() {
        if (!window.AudioContext && !window.webkitAudioContext) {
            return;
        }

        if (!audioState.audioContext) {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            audioState.audioContext = new AudioContextClass();
        }

        if (audioState.audioContext.state === "suspended") {
            await audioState.audioContext.resume();
        }
    }

    return {
        createSpeechSession: createSpeechSession,
        ensureAudioContextReady: ensureAudioContextReady,
        finalizeSpeechSession: finalizeSpeechSession,
        queueSpeechSessionText: queueSpeechSessionText,
        speakText: speakText,
        stopSpeechPlayback: stopSpeechPlayback,
    };
}
