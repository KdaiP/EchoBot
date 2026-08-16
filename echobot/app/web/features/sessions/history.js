import {
    messageContentEquals,
    messageContentToText,
    normalizeMessageContent,
} from "../../modules/content.js";

export function normalizeHistory(history) {
    if (!Array.isArray(history)) {
        return [];
    }
    return history.map((message) => ({
        role: String((message && message.role) || ""),
        content: normalizeMessageContent(message && message.content),
        name: message && message.name ? String(message.name) : null,
        tool_call_id: message && message.tool_call_id ? String(message.tool_call_id) : null,
    }));
}

export function renderSessionHistory(history, deps) {
    const { addMessage, addSystemMessage, clearMessages } = deps;
    clearMessages();

    const messageHistory = normalizeHistory(history);
    if (messageHistory.length === 0) {
        addSystemMessage("当前会话还没有消息，开始聊吧。");
        return;
    }

    messageHistory.forEach((message) => {
        const renderedMessage = resolveHistoryMessage(message);
        addMessage(
            renderedMessage.kind,
            message.content,
            renderedMessage.label,
            renderedMessage.options,
        );
    });
}

export function shouldAnnounceNewMessages(
    options,
    sessionId,
    currentSessionId,
    currentHistory,
) {
    return Boolean(options && options.announceNewMessages)
        && currentSessionId === sessionId
        && Array.isArray(currentHistory)
        && currentHistory.length > 0;
}

export function findAppendedMessages(previousHistory, nextHistory) {
    if (!Array.isArray(previousHistory) || previousHistory.length === 0) {
        return [];
    }
    if (!Array.isArray(nextHistory) || nextHistory.length <= previousHistory.length) {
        return [];
    }

    for (let index = 0; index < previousHistory.length; index += 1) {
        if (!isSameHistoryMessage(previousHistory[index], nextHistory[index])) {
            return [];
        }
    }

    return nextHistory.slice(previousHistory.length);
}

export function buildSpokenText(messages) {
    const assistantMessages = Array.isArray(messages)
        ? messages.filter((message) => message.role === "assistant")
        : [];
    if (assistantMessages.length === 0) {
        return "";
    }

    return assistantMessages
        .map((message) => messageContentToText(message.content, { includeImageMarker: false }).trim())
        .filter(Boolean)
        .join("\n\n");
}

function resolveHistoryMessage(message) {
    if (message.role === "user") {
        return {
            kind: "user",
            label: message.name || "你",
            options: { renderMode: "plain" },
        };
    }
    if (message.role === "assistant") {
        return {
            kind: "assistant",
            label: message.name || "Echo",
            options: {},
        };
    }
    if (message.role === "system") {
        return {
            kind: "system",
            label: message.name || "系统",
            options: { renderMode: "plain" },
        };
    }
    return {
        kind: "system",
        label: message.name || message.role || "记录",
        options: { renderMode: "plain" },
    };
}

function isSameHistoryMessage(left, right) {
    return (
        String((left && left.role) || "") === String((right && right.role) || "")
        && messageContentEquals(left && left.content, right && right.content)
        && String((left && left.name) || "") === String((right && right.name) || "")
        && String((left && left.tool_call_id) || "") === String((right && right.tool_call_id) || "")
    );
}
