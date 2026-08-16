import { DOM } from "../core/dom.js";
import { messageState } from "../core/store.js";
import {
    FILE_ATTACHMENT_CONTENT_BLOCK_TYPE,
    IMAGE_URL_CONTENT_BLOCK_TYPE,
    TEXT_CONTENT_BLOCK_TYPE,
    normalizeMessageContent,
} from "./content.js";
import {
    clearMathTypesetting,
    scheduleMathTypesetting,
} from "./math.js";
import { buildMarkdownFragment } from "./markdown.js";

let pendingScrollFrameId = 0;

export function addMessage(kind, content, label, options = {}) {
    const messageId = `msg-${++messageState.counter}`;
    const container = document.createElement("article");
    container.className = `message message-${kind}`;
    container.dataset.messageId = messageId;
    container.dataset.messageKind = kind;
    const isError = options.variant === "error";
    if (isError) {
        container.classList.add("message-error");
    }
    let messageRole = "group";
    if (isError) {
        messageRole = "alert";
    } else if (kind === "system") {
        messageRole = "status";
    }
    container.setAttribute("role", messageRole);
    container.setAttribute("aria-label", resolveMessageAriaLabel(kind, label));
    if (kind === "system") {
        container.setAttribute("aria-live", isError ? "assertive" : "polite");
    }

    const body = document.createElement("div");
    body.className = "message-text";
    renderMessageBody(body, kind, content, options);

    container.appendChild(body);
    syncMessageMeta(container, label, options);
    DOM.messages.appendChild(container);
    scheduleMathTypesetting(body);
    scheduleMessagesScrollToBottom();
    return messageId;
}

export function addSystemMessage(text) {
    addMessage("system", text, "Status");
}

export function addErrorMessage(title, error) {
    const detail = errorMessageText(error);
    return addMessage("system", detail, title, {
        variant: "error",
        errorTitle: String(title || "操作失败"),
        errorSummary: summarizeError(detail),
    });
}

export function updateMessage(messageId, content, label, options = {}) {
    const container = DOM.messages.querySelector(`[data-message-id="${messageId}"]`);
    if (!container) {
        return;
    }

    const body = container.querySelector(".message-text");
    const kind = container.dataset.messageKind || "assistant";
    container.setAttribute("aria-label", resolveMessageAriaLabel(kind, label));
    syncMessageMeta(container, label, options);
    if (body) {
        renderMessageBody(body, kind, content, options);
        scheduleMathTypesetting(body);
    }
    scheduleMessagesScrollToBottom();
}

export function clearMessages() {
    clearMathTypesetting(DOM.messages);
    DOM.messages.innerHTML = "";
    messageState.counter = 0;
}

export function scheduleMessagesScrollToBottom() {
    if (!DOM.messages || pendingScrollFrameId) {
        return;
    }

    pendingScrollFrameId = window.requestAnimationFrame(() => {
        pendingScrollFrameId = 0;
        scrollMessagesToBottom();
    });
}

export function removeMessage(messageId) {
    const container = DOM.messages.querySelector(`[data-message-id="${messageId}"]`);
    if (!container) {
        return;
    }
    clearMathTypesetting(container);
    container.remove();
}

export function initializeMessageInteractions() {
    if (DOM.messages) {
        DOM.messages.addEventListener("click", handleMessageAreaClick);
    }
    if (DOM.messageImageDialogClose) {
        DOM.messageImageDialogClose.addEventListener("click", closeMessageImagePreview);
    }
    if (DOM.messageImageDialog) {
        DOM.messageImageDialog.addEventListener("click", handleMessageImageDialogClick);
        DOM.messageImageDialog.addEventListener("close", resetMessageImagePreview);
        DOM.messageImageDialog.addEventListener("cancel", () => {
            resetMessageImagePreview();
        });
    }
}

function syncMessageMeta(container, label, options = {}) {
    const existingMeta = container.querySelector(".message-meta");
    const body = container.querySelector(".message-text");

    if (!options.showMeta) {
        if (existingMeta) {
            existingMeta.remove();
        }
        return;
    }

    const meta = existingMeta || document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = String(label || "");

    if (!existingMeta) {
        if (body) {
            container.insertBefore(meta, body);
        } else {
            container.appendChild(meta);
        }
    }
}

function scrollMessagesToBottom() {
    if (!DOM.messages) {
        return;
    }

    DOM.messages.scrollTop = DOM.messages.scrollHeight;
}

function resolveMessageAriaLabel(kind, label) {
    const customLabel = String(label || "").trim();
    if (customLabel) {
        return customLabel;
    }

    if (kind === "user") {
        return "Your message";
    }
    if (kind === "assistant") {
        return "Echo reply";
    }
    if (kind === "system") {
        return "Status";
    }
    return "Message";
}

function renderMessageBody(element, kind, content, options = {}) {
    clearMathTypesetting(element);
    if (options.variant === "error") {
        renderErrorBody(element, content, options);
        return;
    }
    const normalizedContent = normalizeMessageContent(content);
    if (Array.isArray(normalizedContent)) {
        renderStructuredBody(element, kind, normalizedContent, options);
        return;
    }

    const renderMode = resolveMessageRenderMode(kind, options);
    if (renderMode === "markdown") {
        renderMarkdownBody(element, normalizedContent);
        return;
    }
    renderPlainTextBody(element, normalizedContent);
}

function renderErrorBody(element, content, options) {
    const detailText = errorMessageText(content);
    const card = document.createElement("div");
    card.className = "message-error-card";

    const icon = document.createElement("span");
    icon.className = "message-error-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "!";

    const main = document.createElement("div");
    main.className = "message-error-main";

    const title = document.createElement("strong");
    title.className = "message-error-title";
    title.textContent = String(options.errorTitle || "操作失败");
    main.appendChild(title);

    const summary = document.createElement("p");
    summary.className = "message-error-summary";
    summary.textContent = String(options.errorSummary || summarizeError(detailText));
    main.appendChild(summary);

    if (detailText) {
        const detail = document.createElement("pre");
        detail.className = "message-error-detail";
        detail.textContent = detailText;
        main.appendChild(detail);
    }

    card.append(icon, main);
    element.className = "message-text message-text-error";
    element.replaceChildren(card);
}

function errorMessageText(error) {
    if (error instanceof Error) {
        return String(error.message || error.name || "未知错误").trim();
    }
    return String(error || "未知错误").trim();
}

function summarizeError(detail) {
    const normalized = String(detail || "").toLowerCase();
    if (normalized.includes("status=400") || normalized.includes("invalid_request")) {
        return "请求参数格式不受当前模型服务支持，请检查模型能力或附件格式。";
    }
    if (normalized.includes("status=401") || normalized.includes("unauthorized")) {
        return "模型服务认证失败，请检查 API Key。";
    }
    if (normalized.includes("status=403") || normalized.includes("permission")) {
        return "模型服务拒绝了请求，请检查账号权限。";
    }
    if (normalized.includes("status=429") || normalized.includes("rate limit")) {
        return "模型服务请求过于频繁，请稍后重试。";
    }
    if (normalized.includes("timed out") || normalized.includes("timeout")) {
        return "模型服务响应超时，请稍后重试。";
    }
    if (
        normalized.includes("network error")
        || normalized.includes("connection refused")
        || normalized.includes("failed to fetch")
    ) {
        return "无法连接模型服务，请检查服务地址和网络连接。";
    }
    return "本次请求未能完成，展开下方详情可查看具体原因。";
}

function resolveMessageRenderMode(kind, options) {
    if (options.renderMode === "markdown") {
        return "markdown";
    }
    if (options.renderMode === "plain") {
        return "plain";
    }
    return kind === "assistant" ? "markdown" : "plain";
}

function renderPlainTextBody(element, text) {
    element.className = "message-text message-text-plain";
    element.textContent = String(text || "");
}

function renderMarkdownBody(element, text) {
    element.className = "message-text message-text-markdown";
    element.replaceChildren(buildMarkdownFragment(String(text || "")));
}

function renderStructuredBody(element, kind, contentBlocks, options) {
    const renderMode = resolveMessageRenderMode(kind, options);
    const fragment = document.createDocumentFragment();

    contentBlocks.forEach((block) => {
        const blockType = String(block.type || "").trim();
        if (blockType === TEXT_CONTENT_BLOCK_TYPE) {
            fragment.appendChild(
                buildTextBlock(
                    String(block.text || ""),
                    renderMode,
                ),
            );
            return;
        }

        if (blockType === IMAGE_URL_CONTENT_BLOCK_TYPE) {
            const imageUrl = String(block.image_url?.url || "").trim();
            const previewUrl = String(block.image_url?.preview_url || "").trim();
            if (imageUrl) {
                fragment.appendChild(buildImageBlock(previewUrl || imageUrl));
            }
            return;
        }

        if (blockType === FILE_ATTACHMENT_CONTENT_BLOCK_TYPE) {
            fragment.appendChild(buildFileAttachmentBlock(block.file_attachment));
            return;
        }

        if (blockType) {
            fragment.appendChild(buildTextBlock(`[${blockType}]`, "plain"));
        }
    });

    element.className = "message-text message-text-structured";
    if (!fragment.childNodes.length) {
        element.textContent = "";
        return;
    }
    element.replaceChildren(fragment);
}

function buildTextBlock(text, renderMode) {
    const block = document.createElement("div");
    block.className = "message-block message-block-text";
    if (renderMode === "markdown") {
        block.classList.add("message-text-markdown");
        block.replaceChildren(buildMarkdownFragment(String(text || "")));
        return block;
    }

    block.classList.add("message-text-plain");
    block.textContent = String(text || "");
    return block;
}

function buildImageBlock(imageUrl) {
    const block = document.createElement("div");
    block.className = "message-block message-block-image";

    const previewButton = document.createElement("button");
    previewButton.type = "button";
    previewButton.className = "message-image-link";
    previewButton.dataset.imagePreview = "true";
    previewButton.dataset.imageUrl = imageUrl;
    previewButton.title = "点击预览图片";
    previewButton.setAttribute("aria-label", "预览图片");

    const image = document.createElement("img");
    image.className = "message-image";
    image.src = imageUrl;
    image.alt = "Attached image";
    image.loading = "lazy";

    previewButton.appendChild(image);
    block.appendChild(previewButton);
    return block;
}

function buildFileAttachmentBlock(fileAttachment) {
    const attachment = fileAttachment && typeof fileAttachment === "object"
        ? fileAttachment
        : {};
    const fileName = String(attachment.name || "").trim() || "file";
    const downloadUrl = String(attachment.download_url || "").trim();
    const sizeBytes = Number(attachment.size_bytes || 0);

    const block = document.createElement("div");
    block.className = "message-block message-block-file";

    const card = downloadUrl
        ? document.createElement("a")
        : document.createElement("div");
    card.className = "message-file-card";

    if (downloadUrl) {
        card.href = downloadUrl;
        card.target = "_blank";
        card.rel = "noreferrer";
        card.download = fileName;
    }

    const body = document.createElement("div");
    body.className = "message-file-body";

    const name = document.createElement("div");
    name.className = "message-file-name";
    name.textContent = fileName;
    body.appendChild(name);

    const meta = document.createElement("div");
    meta.className = "message-file-meta";
    meta.textContent = buildFileAttachmentMeta(downloadUrl, sizeBytes);
    if (meta.textContent) {
        body.appendChild(meta);
    }

    card.appendChild(body);
    block.appendChild(card);
    return block;
}

function buildFileAttachmentMeta(downloadUrl, sizeBytes) {
    const parts = [];
    if (downloadUrl) {
        parts.push("点击下载");
    } else {
        parts.push("已上传");
    }

    const sizeText = formatFileSize(sizeBytes);
    if (sizeText) {
        parts.push(sizeText);
    }
    return parts.join(" · ");
}

function formatFileSize(sizeBytes) {
    const size = Number(sizeBytes || 0);
    if (!Number.isFinite(size) || size <= 0) {
        return "";
    }
    if (size < 1024) {
        return `${size} B`;
    }
    if (size < 1024 * 1024) {
        return `${(size / 1024).toFixed(1).replace(/\\.0$/, "")} KB`;
    }
    return `${(size / (1024 * 1024)).toFixed(1).replace(/\\.0$/, "")} MB`;
}

function handleMessageAreaClick(event) {
    const previewTrigger = event.target.closest(".message-image-link[data-image-preview='true']");
    if (!previewTrigger || !DOM.messageImageDialog) {
        return;
    }

    const imageUrl = String(previewTrigger.dataset.imageUrl || "").trim();
    if (!imageUrl) {
        return;
    }

    openMessageImagePreview(imageUrl);
}

function openMessageImagePreview(imageUrl) {
    if (!DOM.messageImageDialog || !DOM.messageImageDialogImage) {
        return;
    }

    DOM.messageImageDialogImage.src = imageUrl;

    if (!DOM.messageImageDialog.open) {
        DOM.messageImageDialog.showModal();
    }
}

function closeMessageImagePreview() {
    if (DOM.messageImageDialog?.open) {
        DOM.messageImageDialog.close();
    }
}

function handleMessageImageDialogClick(event) {
    if (event.target === DOM.messageImageDialog) {
        closeMessageImagePreview();
    }
}

function resetMessageImagePreview() {
    if (DOM.messageImageDialogImage) {
        DOM.messageImageDialogImage.removeAttribute("src");
    }
}
