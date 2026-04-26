export function resolveDefaultLive2DTransform(options) {
    const stageWidth = Number(options?.stageWidth) || 0;
    const stageHeight = Number(options?.stageHeight) || 0;
    const baseWidth = Math.max(Number(options?.baseWidth) || 0, 1);
    const baseHeight = Math.max(Number(options?.baseHeight) || 0, 1);
    const desktopTransparentStage = Boolean(options?.desktopTransparentStage);

    const widthRatio = stageWidth / baseWidth;
    const heightRatio = stageHeight / baseHeight;

    if (desktopTransparentStage) {
        const nextScale = Math.max(widthRatio * 0.96, heightRatio * 1.18);
        const scaledHeight = baseHeight * nextScale;
        const topMargin = resolveDesktopTopMargin(stageHeight);
        return {
            x: stageWidth * 0.5,
            y: topMargin + scaledHeight * 0.5,
            scale: roundToTransform(nextScale),
        };
    }

    return {
        x: stageWidth * 0.5,
        y: stageHeight * 0.62,
        scale: roundToTransform(Math.min(widthRatio, heightRatio) * 0.82),
    };
}

function roundToTransform(value) {
    return Math.round(value * 10000) / 10000;
}

function resolveDesktopTopMargin(stageHeight) {
    const height = Math.max(Number(stageHeight) || 0, 1);
    return Math.max(height * 0.06, 12);
}
