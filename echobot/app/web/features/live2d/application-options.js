export function resolveRendererResolution(windowObject = window) {
    const ratio = Number(windowObject?.devicePixelRatio);
    if (!Number.isFinite(ratio) || ratio <= 0) {
        return 1;
    }
    return ratio;
}

export function buildPixiApplicationOptions({
    view,
    resizeTo,
    windowObject = window,
}) {
    return {
        view: view,
        resizeTo: resizeTo,
        autoStart: true,
        antialias: true,
        autoDensity: true,
        backgroundAlpha: 0,
        resolution: resolveRendererResolution(windowObject),
    };
}
