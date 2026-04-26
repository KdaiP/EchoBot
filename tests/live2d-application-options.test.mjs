import test from "node:test";
import assert from "node:assert/strict";

import {
    buildPixiApplicationOptions,
    resolveRendererResolution,
} from "../echobot/app/web/features/live2d/application-options.js";
import {
    isDesktopTransparentStageEnabled,
} from "../echobot/app/web/features/live2d/desktop-stage-mode.js";

test("resolveRendererResolution uses devicePixelRatio when available", () => {
    assert.equal(resolveRendererResolution({ devicePixelRatio: 2 }), 2);
});

test("resolveRendererResolution falls back to 1 for invalid values", () => {
    assert.equal(resolveRendererResolution({ devicePixelRatio: 0 }), 1);
    assert.equal(resolveRendererResolution({ devicePixelRatio: Number.NaN }), 1);
    assert.equal(resolveRendererResolution({}), 1);
});

test("buildPixiApplicationOptions enables autoDensity for crisp canvas rendering", () => {
    const options = buildPixiApplicationOptions({
        view: "canvas",
        resizeTo: "stage",
        windowObject: { devicePixelRatio: 2.5 },
    });

    assert.deepEqual(options, {
        view: "canvas",
        resizeTo: "stage",
        autoStart: true,
        antialias: true,
        autoDensity: true,
        backgroundAlpha: 0,
        resolution: 2.5,
    });
});

test("desktop transparent stage flag is enabled only for explicit desktop stage", () => {
    assert.equal(
        isDesktopTransparentStageEnabled({
            dataset: {
                desktopTransparentStage: "true",
            },
        }),
        true,
    );
    assert.equal(
        isDesktopTransparentStageEnabled({
            dataset: {
                desktopTransparentStage: "false",
            },
        }),
        false,
    );
    assert.equal(isDesktopTransparentStageEnabled(null), false);
});
