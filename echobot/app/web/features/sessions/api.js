export function createSessionsApi(deps) {
    const { requestJson } = deps;

    async function requestSessionSummaries() {
        const payload = await requestJson("/api/sessions");
        return Array.isArray(payload) ? payload : [];
    }

    async function requestSessionDetail(sessionId) {
        return await requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`);
    }

    async function switchCurrentSession(sessionId) {
        return await requestJson("/api/sessions/current", {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ session_id: sessionId }),
        });
    }

    async function createSession(title) {
        return await requestJson("/api/sessions", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(title ? { title: title } : {}),
        });
    }

    async function updateSessionRole(sessionId, roleName) {
        return await requestJson(
            `/api/sessions/${encodeURIComponent(sessionId)}/role`,
            {
                method: "PUT",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ role_name: roleName }),
            },
        );
    }

    async function updateSessionRouteMode(sessionId, routeMode) {
        return await requestJson(
            `/api/sessions/${encodeURIComponent(sessionId)}/route-mode`,
            {
                method: "PUT",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ route_mode: routeMode }),
            },
        );
    }

    async function renameSession(sessionId, title) {
        return await requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`, {
            method: "PATCH",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ title: title }),
        });
    }

    async function deleteSession(sessionId) {
        return await requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`, {
            method: "DELETE",
        });
    }

    return {
        createSession: createSession,
        deleteSession: deleteSession,
        renameSession: renameSession,
        requestSessionDetail: requestSessionDetail,
        requestSessionSummaries: requestSessionSummaries,
        switchCurrentSession: switchCurrentSession,
        updateSessionRole: updateSessionRole,
        updateSessionRouteMode: updateSessionRouteMode,
    };
}
