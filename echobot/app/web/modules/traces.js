import { messageContentToText } from "./content.js";
import { DOM } from "../core/dom.js";

const SKILL_TOOL_NAMES = new Set([
    "activate_skill",
    "list_skill_resources",
    "read_skill_resource",
]);

export function createTraceModule() {
    let currentRunId = "";

    function resetTracePanel() {
        currentRunId = "";

        if (DOM.agentTracePanel) {
            DOM.agentTracePanel.hidden = true;
            DOM.agentTracePanel.open = false;
        }
        if (DOM.agentTraceSummaryText) {
            DOM.agentTraceSummaryText.textContent = "等待后台任务";
        }
        if (DOM.agentTraceCount) {
            DOM.agentTraceCount.textContent = "0 条";
        }
        if (DOM.agentTraceEvents) {
            DOM.agentTraceEvents.replaceChildren(
                buildEmptyState("当前没有可显示的 trace。"),
            );
        }
    }

    function startTracePanel(runId) {
        const normalizedRunId = String(runId || "").trim();
        if (!normalizedRunId) {
            resetTracePanel();
            return;
        }

        currentRunId = normalizedRunId;
        renderTracePayload({
            run_id: normalizedRunId,
            status: "running",
            events: [],
        });
    }

    function applyTracePayload(runId, payload) {
        const normalizedRunId = String(runId || "").trim();
        if (!normalizedRunId || normalizedRunId !== currentRunId) {
            return;
        }
        renderTracePayload(payload);
    }

    function renderTracePayload(payload) {
        if (
            !DOM.agentTracePanel
            || !DOM.agentTraceSummaryText
            || !DOM.agentTraceCount
            || !DOM.agentTraceEvents
        ) {
            return;
        }

        const status = String(payload?.status || "running");
        const events = Array.isArray(payload?.events) ? payload.events : [];
        DOM.agentTracePanel.hidden = false;
        DOM.agentTraceSummaryText.textContent = buildTraceSummaryText(
            status,
            events.length,
        );
        DOM.agentTraceCount.textContent = `${events.length} 条`;

        if (!events.length) {
            DOM.agentTraceEvents.replaceChildren(
                buildEmptyState(buildEmptyStateText(status)),
            );
            return;
        }

        DOM.agentTraceEvents.replaceChildren(
            ...events.map((event, index) => buildTraceEventCard(event, index)),
        );
    }

    return {
        applyTracePayload: applyTracePayload,
        resetTracePanel: resetTracePanel,
        startTracePanel: startTracePanel,
    };
}

function buildTraceSummaryText(status, eventCount) {
    if (status === "failed") {
        return `后台任务失败，已记录 ${eventCount} 条事件`;
    }
    if (status === "cancelled") {
        return `后台任务已停止，已记录 ${eventCount} 条事件`;
    }
    if (status === "waiting_for_input") {
        return `后台任务暂停中，等待你的补充信息，已记录 ${eventCount} 条事件`;
    }
    if (status === "completed") {
        return `后台任务已完成，共 ${eventCount} 条事件`;
    }
    if (eventCount > 0) {
        return `后台任务运行中，已记录 ${eventCount} 条事件`;
    }
    return "后台任务已启动，等待第一条 trace…";
}

function buildEmptyStateText(status) {
    if (status === "failed") {
        return "任务已失败，但暂时没有可显示的 trace。";
    }
    if (status === "cancelled") {
        return "任务已停止，暂时没有更多 trace。";
    }
    if (status === "waiting_for_input") {
        return "Agent 正在等待你的补充信息。";
    }
    if (status === "completed") {
        return "任务已完成，但暂时没有可显示的 trace。";
    }
    return "Agent 已接管请求，正在等待第一条 trace。";
}

function buildEmptyState(text) {
    const element = document.createElement("p");
    element.className = "agent-trace-empty";
    element.textContent = text;
    return element;
}

function buildTraceEventCard(event, index) {
    const article = document.createElement("article");
    article.className = `agent-trace-event ${resolveTraceEventClassName(event)}`;

    const header = document.createElement("div");
    header.className = "agent-trace-event-header";

    const title = document.createElement("strong");
    title.className = "agent-trace-event-title";
    title.textContent = buildTraceEventTitle(event);

    const meta = document.createElement("span");
    meta.className = "muted-text agent-trace-event-meta";
    meta.textContent = buildTraceEventMeta(event, index);

    header.appendChild(title);
    header.appendChild(meta);
    article.appendChild(header);

    const summary = buildTraceEventSummary(event);
    if (summary) {
        const summaryElement = document.createElement("p");
        summaryElement.className = "agent-trace-event-summary";
        summaryElement.textContent = summary;
        article.appendChild(summaryElement);
    }

    const details = buildTraceEventDetails(event);
    if (details) {
        const detailsElement = document.createElement("pre");
        detailsElement.className = "agent-trace-event-details";
        detailsElement.textContent = details;
        article.appendChild(detailsElement);
    }

    return article;
}

function resolveTraceEventClassName(event) {
    if (event?.event === "turn_completed") {
        if (event?.status === "waiting_for_input") {
            return "";
        }
        return "agent-trace-event-success";
    }
    if (event?.event === "turn_failed" || event?.is_error) {
        return "agent-trace-event-error";
    }
    return "";
}

function buildTraceEventMeta(event, index) {
    const parts = [`#${index + 1}`];
    if (Number.isFinite(event?.step)) {
        parts.push(`第 ${event.step} 步`);
    }
    const timeText = formatTraceTime(event?.created_at);
    if (timeText) {
        parts.push(timeText);
    }
    return parts.join(" · ");
}

function buildTraceEventTitle(event) {
    const eventName = String(event?.event || "");
    if (eventName === "turn_started") {
        return "开始处理";
    }
    if (eventName === "turn_completed") {
        if (event?.status === "waiting_for_input") {
            return "等待用户回复";
        }
        return "处理完成";
    }
    if (eventName === "turn_failed") {
        return "处理失败";
    }
    if (eventName === "assistant_message") {
        const toolCalls = Array.isArray(event?.message?.tool_calls)
            ? event.message.tool_calls
            : [];
        if (toolCalls.length === 1) {
            return buildToolCallTraceTitle(toolCalls[0]?.name);
        }
        if (toolCalls.length > 1) {
            return `[tool-call] ${toolCalls
                .map((item) => String(item?.name || "unknown-tool"))
                .join(", ")}`;
        }
        return "模型回复";
    }
    if (eventName === "tool_result") {
        return buildToolResultTraceTitle(
            String(event?.tool_name || "unknown-tool"),
            traceMessageRawContent(event?.message),
        );
    }
    const customTitle = String(event?.title || "").trim();
    if (customTitle) {
        return customTitle;
    }
    return eventName || "trace";
}

function buildTraceEventSummary(event) {
    const eventName = String(event?.event || "");
    if (eventName === "turn_started") {
        return "Agent 已接管当前请求。";
    }
    if (eventName === "assistant_message") {
        const toolCalls = Array.isArray(event?.message?.tool_calls)
            ? event.message.tool_calls
            : [];
        if (toolCalls.length > 0) {
            return `计划调用 ${toolCalls.length} 个工具。`;
        }
        const content = traceMessageText(event?.message).trim();
        if (content) {
            return buildExcerpt(content);
        }
        return "模型返回了一条空消息。";
    }
    if (eventName === "tool_result") {
        return event?.is_error ? "工具执行失败。" : "工具执行完成。";
    }
    if (eventName === "turn_completed") {
        if (event?.status === "waiting_for_input") {
            return "后台任务已暂停，等待你的补充信息。";
        }
        const steps = Number.isFinite(event?.steps) ? event.steps : null;
        if (steps !== null) {
            return `后台任务已完成，共执行 ${steps} 步。`;
        }
        return "后台任务已完成。";
    }
    if (eventName === "turn_failed") {
        return String(event?.error || "后台任务执行失败。");
    }
    const customSummary = String(event?.summary || "").trim();
    if (customSummary) {
        return customSummary;
    }
    return "";
}

function buildTraceEventDetails(event) {
    const eventName = String(event?.event || "");
    if (eventName === "assistant_message") {
        const toolCalls = Array.isArray(event?.message?.tool_calls)
            ? event.message.tool_calls
            : [];
        if (toolCalls.length > 0) {
            return buildToolCallDetails(toolCalls);
        }
        return traceMessageText(event?.message).trim();
    }
    if (eventName === "tool_result") {
        return formatJsonText(traceMessageRawContent(event?.message));
    }
    if (eventName === "turn_completed") {
        const finalText = traceMessageText(event?.final_message).trim();
        if (finalText) {
            return finalText;
        }
        return "";
    }
    if (eventName === "turn_failed") {
        return String(event?.error || "").trim();
    }
    const customDetails = String(event?.details || "").trim();
    if (customDetails) {
        return customDetails;
    }
    return "";
}

function buildToolCallDetails(toolCalls) {
    return toolCalls
        .map((toolCall) => {
            const toolName = String(toolCall?.name || "unknown-tool");
            const argumentsText = formatJsonText(String(toolCall?.arguments || ""));
            return `${buildToolCallTraceTitle(toolName)}\n${argumentsText}`;
        })
        .join("\n\n");
}

function buildExcerpt(text, maxLength = 120) {
    const cleaned = String(text || "").replace(/\s+/g, " ").trim();
    if (cleaned.length <= maxLength) {
        return cleaned;
    }
    return `${cleaned.slice(0, maxLength - 1).trimEnd()}…`;
}

function formatTraceTime(createdAt) {
    const rawText = String(createdAt || "").trim();
    if (!rawText) {
        return "";
    }
    const date = new Date(rawText);
    if (Number.isNaN(date.getTime())) {
        return rawText;
    }
    return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function buildToolCallTraceTitle(toolName) {
    if (SKILL_TOOL_NAMES.has(toolName)) {
        return `[skill-call] ${toolName}`;
    }
    return `[tool-call] ${toolName}`;
}

function buildToolResultTraceTitle(toolName, content) {
    const payload = parseJsonText(content);
    if (!payload || Array.isArray(payload)) {
        return `[tool-result] ${toolName}`;
    }

    const result = payload.result;
    if (!result || Array.isArray(result) || typeof result !== "object") {
        return `[tool-result] ${toolName}`;
    }

    const kind = String(result.kind || "");
    const skillName = String(result.name || "").trim();

    if (toolName === "activate_skill" && kind === "skill_activation") {
        const suffix = result.already_active ? " (already active)" : "";
        return `[skill-activate] ${skillName || "unknown-skill"}${suffix}`;
    }
    if (toolName === "list_skill_resources" && kind === "skill_resource_list") {
        const folderName = String(result.folder || "all").trim() || "all";
        return `[skill-resources] ${skillName || "unknown-skill"} (${folderName})`;
    }
    if (toolName === "read_skill_resource" && kind === "skill_resource_content") {
        const resourcePath = String(result.path || "").trim();
        if (resourcePath) {
            return `[skill-resource] ${skillName || "unknown-skill"} | ${resourcePath}`;
        }
        return `[skill-resource] ${skillName || "unknown-skill"}`;
    }

    return `[tool-result] ${toolName}`;
}

function formatJsonText(text) {
    const parsed = parseJsonText(text);
    if (parsed === null) {
        return text;
    }
    return JSON.stringify(parsed, null, 2);
}

function parseJsonText(text) {
    try {
        return JSON.parse(text);
    } catch (_error) {
        return null;
    }
}

function traceMessageText(message) {
    const explicitText = String(message?.content_text || "").trim();
    if (explicitText) {
        return explicitText;
    }
    return messageContentToText(message?.content);
}

function traceMessageRawContent(message) {
    if (typeof message?.content === "string") {
        return message.content;
    }
    return traceMessageText(message);
}
