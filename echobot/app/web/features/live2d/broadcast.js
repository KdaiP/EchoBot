const LIVE2D_BROADCAST_CHANNEL = "echobot-live2d";

function createSourceId() {
    return `live2d-${Math.random().toString(36).slice(2, 10)}`;
}

function createChannel() {
    if (typeof globalThis.BroadcastChannel !== "function") {
        return null;
    }

    return new globalThis.BroadcastChannel(LIVE2D_BROADCAST_CHANNEL);
}

function postMessage(channel, sourceId, type, payload) {
    if (!channel || !type) {
        return false;
    }

    channel.postMessage({
        type,
        payload: payload ?? null,
        sourceId,
        sentAt: Date.now(),
    });
    return true;
}

export function createLive2DBroadcastSender(options = {}) {
    const sourceId = String(options.sourceId || createSourceId());
    const channel = createChannel();

    return {
        sourceId,
        isAvailable() {
            return Boolean(channel);
        },
        sendModelChanged(live2dConfig) {
            return postMessage(channel, sourceId, "model-changed", {
                live2dConfig: live2dConfig ?? null,
            });
        },
        sendExpressionToggled(payload) {
            return postMessage(channel, sourceId, "expression-toggled", payload ?? null);
        },
        sendMotionPlayed(payload) {
            return postMessage(channel, sourceId, "motion-played", payload ?? null);
        },
        sendMouthValue(value) {
            return postMessage(channel, sourceId, "mouth-value", {
                value,
            });
        },
        sendMouseFollowChanged(enabled) {
            return postMessage(channel, sourceId, "mouse-follow-changed", {
                enabled: Boolean(enabled),
            });
        },
        close() {
            channel?.close();
        },
    };
}

export function createLive2DBroadcastReceiver(handlers = {}, options = {}) {
    const channel = createChannel();
    const ignoreSourceId = String(options.ignoreSourceId || "");

    function handleMessage(event) {
        const message = event?.data;
        if (!message || typeof message !== "object") {
            return;
        }

        const sourceId = String(message.sourceId || "");
        if (ignoreSourceId && sourceId && sourceId === ignoreSourceId) {
            return;
        }

        const type = String(message.type || "").trim();
        const payload = message.payload ?? null;

        switch (type) {
            case "model-changed":
                handlers.onModelChanged?.(payload);
                break;
            case "expression-toggled":
                handlers.onExpressionToggled?.(payload);
                break;
            case "motion-played":
                handlers.onMotionPlayed?.(payload);
                break;
            case "mouth-value":
                handlers.onMouthValue?.(payload);
                break;
            case "mouse-follow-changed":
                handlers.onMouseFollowChanged?.(payload);
                break;
            default:
                break;
        }
    }

    channel?.addEventListener("message", handleMessage);

    return {
        isAvailable() {
            return Boolean(channel);
        },
        close() {
            if (!channel) {
                return;
            }
            channel.removeEventListener("message", handleMessage);
            channel.close();
        },
    };
}

export { LIVE2D_BROADCAST_CHANNEL };
