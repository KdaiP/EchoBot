export async function requestJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
        throw await responseToError(response);
    }
    return await response.json();
}

export async function uploadChatImage(file) {
    const formData = new FormData();
    formData.append("file", file);
    return await requestJson("/api/attachments/images", {
        method: "POST",
        body: formData,
    });
}

export async function uploadChatFile(file) {
    const formData = new FormData();
    formData.append("file", file);
    return await requestJson("/api/attachments/files", {
        method: "POST",
        body: formData,
    });
}

export async function deleteAttachment(attachmentId) {
    const response = await fetch(`/api/attachments/${encodeURIComponent(attachmentId)}`, {
        method: "DELETE",
    });
    if (!response.ok) {
        throw await responseToError(response);
    }
}

export async function requestChatStream(payload, handlers) {
    const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
    });

    if (!response.ok) {
        throw await responseToError(response);
    }

    if (!response.body) {
        return await requestJson("/api/chat", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        });
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalPayload = null;

    while (true) {
        const { done, value } = await reader.read();
        if (done) {
            break;
        }

        buffer += decoder.decode(value, { stream: true });
        const parsed = await consumeChatStreamBuffer(buffer, handlers);
        buffer = parsed.buffer;
        if (parsed.finalPayload) {
            finalPayload = parsed.finalPayload;
        }
    }

    buffer += decoder.decode();
    const parsed = await consumeChatStreamBuffer(buffer, handlers);
    if (parsed.finalPayload) {
        finalPayload = parsed.finalPayload;
    }

    if (!finalPayload) {
        throw new Error("Chat stream ended without a final response.");
    }

    return finalPayload;
}

export async function requestAgentRun(runId) {
    return await requestJson(`/api/chat/runs/${encodeURIComponent(runId)}`);
}

export async function requestAgentRunEvents(runId) {
    return await requestJson(`/api/chat/runs/${encodeURIComponent(runId)}/events`);
}

export async function cancelAgentRun(runId) {
    return await requestJson(
        `/api/chat/runs/${encodeURIComponent(runId)}/cancel`,
        {
            method: "POST",
        },
    );
}

export async function consumeChatStreamBuffer(buffer, handlers) {
    let remaining = buffer;
    let finalPayload = null;

    while (true) {
        const newlineIndex = remaining.indexOf("\n");
        if (newlineIndex === -1) {
            break;
        }

        const line = remaining.slice(0, newlineIndex).trim();
        remaining = remaining.slice(newlineIndex + 1);
        if (!line) {
            continue;
        }

        let event;
        try {
            event = JSON.parse(line);
        } catch (_error) {
            throw new Error(`Invalid chat stream event: ${line}`);
        }

        if (event.type === "chunk") {
            if (handlers && typeof handlers.onChunk === "function") {
                await handlers.onChunk(event.delta || "");
            }
            continue;
        }
        if (event.type === "done") {
            finalPayload = event;
            continue;
        }
        if (event.type === "error") {
            throw new Error(event.message || "Chat stream failed.");
        }
    }

    return {
        buffer: remaining,
        finalPayload: finalPayload,
    };
}

export async function responseToError(response) {
    let detail = `${response.status} ${response.statusText}`;
    try {
        const payload = await response.json();
        if (payload && typeof payload.detail === "string") {
            detail = payload.detail;
        }
    } catch (error) {
        console.warn("Non-JSON error response", error);
    }
    return new Error(detail);
}
