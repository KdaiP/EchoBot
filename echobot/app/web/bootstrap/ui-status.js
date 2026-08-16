import { DOM } from "../core/dom.js";
import { chatState, sessionState } from "../core/store.js";
import { scheduleMessagesScrollToBottom } from "../modules/messages.js";

export function createUiStatusController() {
    const features = {
        asr: null,
        chat: null,
        roles: null,
        sessions: null,
    };

    function bindFeatures(nextFeatures) {
        Object.assign(features, nextFeatures || {});
    }

    function setChatBusy(isBusy) {
        chatState.chatBusy = isBusy;
        if (DOM.sendButton) {
            DOM.sendButton.disabled = isBusy;
        }
        if (DOM.composerFileButton) {
            DOM.composerFileButton.disabled = isBusy || Boolean(chatState.activeAgentRunId);
        }
        if (DOM.composerFileInput) {
            DOM.composerFileInput.disabled = isBusy || Boolean(chatState.activeAgentRunId);
        }
        if (DOM.composerImageButton) {
            DOM.composerImageButton.disabled = isBusy || Boolean(chatState.activeAgentRunId);
        }
        if (DOM.composerImageInput) {
            DOM.composerImageInput.disabled = isBusy || Boolean(chatState.activeAgentRunId);
        }
        if (DOM.sessionCreateButton) {
            DOM.sessionCreateButton.disabled = isBusy || sessionState.sessionLoading;
        }
        if (DOM.sessionRefreshButton) {
            DOM.sessionRefreshButton.disabled = isBusy || sessionState.sessionLoading;
        }
        if (DOM.routeModeSelect) {
            DOM.routeModeSelect.disabled = (
                isBusy
                || sessionState.sessionLoading
                || Boolean(chatState.activeAgentRunId)
            );
        }

        features.sessions?.renderSessionList(sessionState.sessions);
        features.roles?.updateRoleActionState();
        features.asr?.updateVoiceInputControls();
        updateAgentRunState();
        features.chat?.refreshComposerAttachments();
    }

    function setActiveAgentRun(runId) {
        chatState.activeAgentRunId = String(runId || "").trim();
        updateAgentRunState();
    }

    function setConnectionState(kind, text) {
        if (!DOM.connectionBadge) {
            return;
        }

        DOM.connectionBadge.className = `status-badge status-${kind}`;
        DOM.connectionBadge.textContent = text;
    }

    function setRunStatus(text) {
        if (DOM.runStatus) {
            DOM.runStatus.textContent = text;
        }
    }

    function updateAgentRunState() {
        const agentRunActive = Boolean(chatState.activeAgentRunId);

        if (DOM.promptInput) {
            DOM.promptInput.disabled = agentRunActive;
        }
        if (DOM.composerFileButton) {
            DOM.composerFileButton.disabled = agentRunActive || chatState.chatBusy;
        }
        if (DOM.composerFileInput) {
            DOM.composerFileInput.disabled = agentRunActive || chatState.chatBusy;
        }
        if (DOM.composerImageButton) {
            DOM.composerImageButton.disabled = agentRunActive || chatState.chatBusy;
        }
        if (DOM.composerImageInput) {
            DOM.composerImageInput.disabled = agentRunActive || chatState.chatBusy;
        }
        if (DOM.composerStatusBanner) {
            DOM.composerStatusBanner.hidden = !agentRunActive;
        }
        if (DOM.stopAgentButton) {
            DOM.stopAgentButton.disabled = !agentRunActive;
            DOM.stopAgentButton.classList.toggle("is-active", agentRunActive);
        }
        if (DOM.routeModeSelect) {
            DOM.routeModeSelect.disabled = (
                agentRunActive
                || chatState.chatBusy
                || sessionState.sessionLoading
            );
        }

        scheduleMessagesScrollToBottom();
        features.chat?.refreshComposerAttachments();
    }

    return {
        bindFeatures,
        setActiveAgentRun,
        setChatBusy,
        setConnectionState,
        setRunStatus,
    };
}
