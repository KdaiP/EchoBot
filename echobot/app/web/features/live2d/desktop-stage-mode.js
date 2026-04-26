export function isDesktopTransparentStageEnabled(stageElement) {
    return String(stageElement?.dataset?.desktopTransparentStage || "").trim() === "true";
}
