import {
    chatState,
    roleState,
    sessionState,
} from "../core/store.js";
import { DOM } from "../core/dom.js";

export function createRolesModule(deps) {
    const {
        addMessage,
        requestJson,
        setRunStatus,
    } = deps;

    let sessionHooks = {
        applySessionDetail() {},
    };

    function bindSessionHooks(hooks) {
        sessionHooks = {
            ...sessionHooks,
            ...(hooks || {}),
        };
    }

    async function initializeRolePanel() {
        await refreshRolePanel({ silent: true });
    }

    async function syncRolePanelForCurrentSession() {
        const roleSummaries = Array.isArray(roleState.roles) ? roleState.roles : [];
        const hasCurrentRole = roleSummaries.some(
            (item) => item && item.name === roleState.currentRoleName,
        );

        if (hasCurrentRole) {
            renderRoleSelectOptions();
        } else if (!roleState.roleLoading) {
            await refreshRoleList({ silent: true });
        }
        await refreshCurrentRoleCard({ silent: true });
    }

    async function refreshRolePanel(options = {}) {
        await refreshRoleList(options);
        await refreshCurrentRoleCard(options);
    }

    async function refreshRoleList(options = {}) {
        if (roleState.roleLoading) {
            return;
        }

        setRoleControlsBusy(true, options.silent ? null : "正在加载角色卡…");
        try {
            const payload = await requestJson("/api/roles");
            roleState.roles = Array.isArray(payload) ? payload : [];
            renderRoleSelectOptions();
            if (!options.silent) {
                setRoleStatus("");
            }
        } catch (error) {
            console.error(error);
            renderRoleSelectOptions();
            if (!options.silent) {
                setRoleStatus(error.message || "角色卡加载失败");
                addMessage("system", `角色卡加载失败：${error.message || error}`, "状态");
            }
        } finally {
            setRoleControlsBusy(false);
        }
    }

    async function refreshCurrentRoleCard(options = {}) {
        const roleName = roleState.currentRoleName || "default";
        if (!roleName) {
            roleState.currentRoleCard = null;
            renderCurrentRoleCard();
            return;
        }

        try {
            roleState.currentRoleCard = await requestJson(
                `/api/roles/${encodeURIComponent(roleName)}`,
            );
        } catch (error) {
            console.error(error);
            roleState.currentRoleCard = null;
            if (!options.silent) {
                setRoleStatus(error.message || "角色卡详情加载失败");
                addMessage("system", `角色卡详情加载失败：${error.message || error}`, "状态");
            }
        }

        renderCurrentRoleCard();
    }

    function renderRoleSelectOptions() {
        if (!DOM.roleSelect) {
            return;
        }

        DOM.roleSelect.innerHTML = "";
        const roleSummaries = Array.isArray(roleState.roles) ? roleState.roles : [];
        if (roleSummaries.length === 0) {
            const option = document.createElement("option");
            option.value = "default";
            option.textContent = "default";
            DOM.roleSelect.appendChild(option);
            DOM.roleSelect.disabled = true;
            return;
        }

        const availableNames = new Set(roleSummaries.map((item) => item.name));
        if (!availableNames.has(roleState.currentRoleName)) {
            roleState.currentRoleName = availableNames.has("default")
                ? "default"
                : roleSummaries[0].name;
        }

        roleSummaries.forEach((roleSummary) => {
            const option = document.createElement("option");
            option.value = roleSummary.name;
            option.textContent = buildRoleOptionLabel(roleSummary);
            DOM.roleSelect.appendChild(option);
        });
        DOM.roleSelect.value = roleState.currentRoleName;
        updateRoleActionState();
    }

    function buildRoleOptionLabel(roleSummary) {
        const name = String((roleSummary && roleSummary.name) || "default");
        if (name === "default") {
            return `${name}（默认）`;
        }
        return name;
    }

    function renderCurrentRoleCard() {
        const roleCard = roleState.currentRoleCard;

        if (DOM.rolePromptPreview) {
            DOM.rolePromptPreview.textContent = roleCard && roleCard.prompt
                ? roleCard.prompt
                : "暂无角色卡内容。";
        }

        if (DOM.roleStatus) {
            if (!roleCard) {
                DOM.roleStatus.textContent = "暂无角色卡详情。";
            } else if (!roleCard.editable) {
                DOM.roleStatus.textContent = `当前角色：${roleCard.name}（只读）`;
            } else {
                DOM.roleStatus.textContent = `当前角色：${roleCard.name}`;
            }
        }

        updateRoleActionState();
    }

    function setRoleControlsBusy(isBusy, statusText = null) {
        roleState.roleLoading = isBusy;
        if (typeof statusText === "string") {
            setRoleStatus(statusText);
        }
        updateRoleActionState();
    }

    function setRoleStatus(text) {
        if (!DOM.roleStatus) {
            return;
        }
        DOM.roleStatus.textContent = String(text || "").trim();
    }

    function updateRoleActionState() {
        const roleCard = roleState.currentRoleCard;
        const isBusy = chatState.chatBusy || roleState.roleLoading;
        const editorOpen = roleState.roleEditorMode !== "closed";
        const controlsLocked = isBusy || editorOpen;

        if (DOM.roleSelect) {
            DOM.roleSelect.disabled = controlsLocked || !roleState.roles || roleState.roles.length === 0;
        }
        if (DOM.roleRefreshButton) {
            DOM.roleRefreshButton.disabled = controlsLocked;
        }
        if (DOM.roleNewButton) {
            DOM.roleNewButton.disabled = controlsLocked;
        }
        if (DOM.roleEditButton) {
            DOM.roleEditButton.disabled = controlsLocked || !roleCard || !roleCard.editable;
        }
        if (DOM.roleDeleteButton) {
            DOM.roleDeleteButton.disabled = controlsLocked || !roleCard || !roleCard.deletable;
        }
        if (DOM.roleSaveButton) {
            DOM.roleSaveButton.disabled = isBusy || !editorOpen;
        }
        if (DOM.roleCancelButton) {
            DOM.roleCancelButton.disabled = roleState.roleLoading;
        }
        if (DOM.rolePreview) {
            DOM.rolePreview.hidden = editorOpen;
        }
        if (DOM.roleEditor) {
            DOM.roleEditor.hidden = !editorOpen;
        }
        if (DOM.roleNameInput) {
            DOM.roleNameInput.disabled = roleState.roleLoading || roleState.roleEditorMode !== "create";
            DOM.roleNameInput.readOnly = roleState.roleEditorMode !== "create";
        }
        if (DOM.rolePromptInput) {
            DOM.rolePromptInput.disabled = roleState.roleLoading || !editorOpen;
        }
    }

    async function handleRoleSelectionChange() {
        if (!DOM.roleSelect) {
            return;
        }

        const nextRoleName = String(DOM.roleSelect.value || "").trim();
        if (
            !nextRoleName
            || nextRoleName === roleState.currentRoleName
            || chatState.chatBusy
            || roleState.roleLoading
        ) {
            renderRoleSelectOptions();
            return;
        }

        closeRoleEditor();
        setRoleControlsBusy(true, "正在切换角色卡…");
        try {
            await setCurrentSessionRole(nextRoleName, { silent: true });
            await refreshCurrentRoleCard({ silent: true });
            setRunStatus(`已切换角色卡：${roleState.currentRoleName}`);
            setRoleStatus("");
        } catch (error) {
            console.error(error);
            renderRoleSelectOptions();
            setRoleStatus(error.message || "切换角色卡失败");
            addMessage("system", `切换角色卡失败：${error.message || error}`, "状态");
        } finally {
            setRoleControlsBusy(false);
        }
    }

    async function setCurrentSessionRole(roleName, options = {}) {
        const sessionId = sessionState.currentSessionId;
        const sessionDetail = await requestJson(
            `/api/sessions/${encodeURIComponent(sessionId)}/role`,
            {
                method: "PUT",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ role_name: roleName }),
            },
        );
        sessionHooks.applySessionDetail(sessionDetail);
        if (!options.silent) {
            setRunStatus(`已切换角色卡：${sessionDetail.role_name || roleName}`);
        }
        return sessionDetail;
    }

    function openRoleEditor(mode) {
        if (!DOM.roleEditor || !DOM.roleNameInput || !DOM.rolePromptInput || !DOM.roleEditorTitle) {
            return;
        }

        if (mode === "edit" && (!roleState.currentRoleCard || !roleState.currentRoleCard.editable)) {
            return;
        }

        roleState.roleEditorMode = mode;
        DOM.roleEditor.hidden = false;
        if (mode === "create") {
            DOM.roleEditorTitle.textContent = "新建角色卡";
            DOM.roleNameInput.value = "";
            DOM.rolePromptInput.value = "";
            DOM.roleNameInput.focus();
        } else {
            DOM.roleEditorTitle.textContent = `编辑角色卡：${roleState.currentRoleCard.name}`;
            DOM.roleNameInput.value = roleState.currentRoleCard.name || "";
            DOM.rolePromptInput.value = roleState.currentRoleCard.prompt || "";
            DOM.rolePromptInput.focus();
        }
        updateRoleActionState();
    }

    function closeRoleEditor() {
        roleState.roleEditorMode = "closed";
        if (DOM.roleEditor) {
            DOM.roleEditor.hidden = true;
        }
        if (DOM.roleNameInput) {
            DOM.roleNameInput.value = "";
        }
        if (DOM.rolePromptInput) {
            DOM.rolePromptInput.value = "";
        }
        if (DOM.roleEditorTitle) {
            DOM.roleEditorTitle.textContent = "角色卡编辑";
        }
        updateRoleActionState();
    }

    async function handleEditRoleClick() {
        if (!roleState.currentRoleCard || !roleState.currentRoleCard.editable) {
            return;
        }
        await refreshCurrentRoleCard({ silent: true });
        openRoleEditor("edit");
    }

    async function handleSaveRoleClick() {
        if (
            chatState.chatBusy
            || roleState.roleLoading
            || roleState.roleEditorMode === "closed"
        ) {
            return;
        }

        const roleName = DOM.roleNameInput ? DOM.roleNameInput.value.trim() : "";
        const prompt = DOM.rolePromptInput ? DOM.rolePromptInput.value.trim() : "";
        const isCreateMode = roleState.roleEditorMode === "create";
        let shouldRefreshRoleList = false;
        if (!prompt) {
            setRoleStatus("角色卡内容不能为空。");
            return;
        }
        if (isCreateMode && !roleName) {
            setRoleStatus("角色名不能为空。");
            return;
        }

        setRoleControlsBusy(
            true,
            isCreateMode ? "正在创建角色卡…" : "正在保存角色卡…",
        );
        try {
            let roleDetail;
            if (isCreateMode) {
                roleDetail = await requestJson("/api/roles", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({
                        name: roleName,
                        prompt: prompt,
                    }),
                });
                await setCurrentSessionRole(roleDetail.name, { silent: true });
                shouldRefreshRoleList = true;
            } else {
                roleDetail = await requestJson(
                    `/api/roles/${encodeURIComponent(roleState.currentRoleName)}`,
                    {
                        method: "PUT",
                        headers: {
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify({
                            prompt: prompt,
                        }),
                    },
                );
            }

            roleState.currentRoleName = roleDetail.name || roleState.currentRoleName;
            roleState.currentRoleCard = roleDetail;
            renderCurrentRoleCard();
            closeRoleEditor();
            await refreshCurrentRoleCard({ silent: true });
            setRoleStatus("");
            setRunStatus(
                isCreateMode
                    ? `已创建角色卡：${roleDetail.name}`
                    : `已保存角色卡：${roleDetail.name}`,
            );
        } catch (error) {
            console.error(error);
            setRoleStatus(error.message || "保存角色卡失败");
            addMessage("system", `保存角色卡失败：${error.message || error}`, "状态");
        } finally {
            setRoleControlsBusy(false);
        }

        if (shouldRefreshRoleList) {
            await refreshRoleList({ silent: true });
        }
    }

    async function handleDeleteRoleClick() {
        const roleCard = roleState.currentRoleCard;
        if (
            !roleCard
            || !roleCard.deletable
            || chatState.chatBusy
            || roleState.roleLoading
        ) {
            return;
        }
        if (!window.confirm(`确定删除角色卡“${roleCard.name}”吗？`)) {
            return;
        }

        let shouldRefreshRoleList = false;
        setRoleControlsBusy(true, "正在删除角色卡…");
        try {
            await requestJson(`/api/roles/${encodeURIComponent(roleCard.name)}`, {
                method: "DELETE",
            });
            shouldRefreshRoleList = true;
            closeRoleEditor();
            const sessionDetail = await requestJson("/api/sessions/current");
            sessionHooks.applySessionDetail(sessionDetail);
            await refreshCurrentRoleCard({ silent: true });
            setRoleStatus("");
            setRunStatus(`已删除角色卡：${roleCard.name}`);
        } catch (error) {
            console.error(error);
            setRoleStatus(error.message || "删除角色卡失败");
            addMessage("system", `删除角色卡失败：${error.message || error}`, "状态");
        } finally {
            setRoleControlsBusy(false);
        }

        if (shouldRefreshRoleList) {
            await refreshRoleList({ silent: true });
        }
    }

    return {
        bindSessionHooks: bindSessionHooks,
        initializeRolePanel: initializeRolePanel,
        syncRolePanelForCurrentSession: syncRolePanelForCurrentSession,
        refreshRolePanel: refreshRolePanel,
        handleRoleSelectionChange: handleRoleSelectionChange,
        handleEditRoleClick: handleEditRoleClick,
        handleSaveRoleClick: handleSaveRoleClick,
        handleDeleteRoleClick: handleDeleteRoleClick,
        openRoleEditor: openRoleEditor,
        closeRoleEditor: closeRoleEditor,
        updateRoleActionState: updateRoleActionState,
    };
}
