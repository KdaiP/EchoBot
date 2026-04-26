import test from "node:test";
import assert from "node:assert/strict";

const documentStub = {
    getElementById() {
        return null;
    },
    createElement() {
        throw new Error("Unexpected createElement call");
    },
};

globalThis.document = documentStub;
globalThis.window = {
    localStorage: {
        getItem() {
            return null;
        },
        setItem() {},
        removeItem() {},
    },
};

function createStageElement(flag) {
    return {
        dataset: {
            desktopTransparentStage: flag,
        },
        offsetWidth: 420,
        offsetHeight: 620,
        style: {
            setProperty() {},
            removeProperty() {},
        },
        classList: {
            add() {},
            remove() {},
        },
    };
}

test("desktop transparent stage flag is enabled only for explicit desktop stage", async () => {
    const { isDesktopTransparentStageEnabled } = await import(
        "../echobot/app/web/features/live2d/desktop-stage-mode.js"
    );

    assert.equal(isDesktopTransparentStageEnabled(createStageElement("true")), true);
    assert.equal(isDesktopTransparentStageEnabled(createStageElement("false")), false);
    assert.equal(isDesktopTransparentStageEnabled(null), false);
});

test("desktop transparent stage keeps pixi background sprite hidden when no custom background is selected", async () => {
    const { DOM } = await import("../echobot/app/web/core/dom.js");
    const { live2dState } = await import("../echobot/app/web/core/store.js");
    const { createStageBackgroundController } = await import(
        "../echobot/app/web/features/live2d/backgrounds.js"
    );

    const originalCreateElement = document.createElement;
    const originalWindow = globalThis.window;
    const originalStageElement = DOM.stageElement;
    const originalStageBackgroundImage = DOM.stageBackgroundImage;
    const originalPixiApp = live2dState.pixiApp;
    const originalBackgroundLayer = live2dState.live2dBackgroundLayer;
    const originalBackgroundSprite = live2dState.stageBackgroundSprite;
    const originalBackgroundLoadToken = live2dState.stageBackgroundLoadToken;
    const originalNaturalSize = live2dState.currentBackgroundImageNaturalSize;
    const originalStageEffects = live2dState.stageEffects;

    const stageElement = createStageElement("true");
    const stageBackgroundImage = {
        hidden: false,
        style: {
            backgroundImage: "",
        },
    };

    const whiteTexture = { id: "white" };
    document.createElement = function createElement() {
        throw new Error("Transparent desktop stage should not create a default background canvas");
    };
    globalThis.window = {
        ...originalWindow,
        PIXI: {
            Texture: {
                WHITE: whiteTexture,
                from() {
                    throw new Error("Transparent desktop stage should not build a default background texture");
                },
            },
            Sprite: class {
                constructor(texture) {
                    this.texture = texture;
                    this.visible = true;
                    this.alpha = 1;
                    this.zIndex = 0;
                    this.anchor = {
                        set() {},
                    };
                }
            },
        },
    };

    DOM.stageElement = stageElement;
    DOM.stageBackgroundImage = stageBackgroundImage;
    live2dState.pixiApp = {};
    live2dState.live2dBackgroundLayer = {
        addChild() {},
    };
    live2dState.stageBackgroundSprite = null;
    live2dState.stageBackgroundLoadToken = 0;
    live2dState.currentBackgroundImageNaturalSize = {
        w: 10,
        h: 10,
    };
    live2dState.stageEffects = null;

    try {
        const controller = createStageBackgroundController({
            clamp(value, min, max) {
                return Math.min(Math.max(value, min), max);
            },
            roundTo(value) {
                return value;
            },
            responseToError(error) {
                return error;
            },
            setRunStatus() {},
            applyStageEffectsToRuntime() {},
        });

        await controller.syncPixiStageBackground(null, {
            positionX: 50,
            positionY: 50,
            scale: 100,
        });

        assert.equal(live2dState.stageBackgroundSprite.texture, whiteTexture);
        assert.equal(live2dState.stageBackgroundSprite.visible, false);
        assert.equal(live2dState.stageBackgroundSprite.alpha, 0);
        assert.equal(live2dState.currentBackgroundImageNaturalSize, null);
    } finally {
        document.createElement = originalCreateElement;
        globalThis.window = originalWindow;
        DOM.stageElement = originalStageElement;
        DOM.stageBackgroundImage = originalStageBackgroundImage;
        live2dState.pixiApp = originalPixiApp;
        live2dState.live2dBackgroundLayer = originalBackgroundLayer;
        live2dState.stageBackgroundSprite = originalBackgroundSprite;
        live2dState.stageBackgroundLoadToken = originalBackgroundLoadToken;
        live2dState.currentBackgroundImageNaturalSize = originalNaturalSize;
        live2dState.stageEffects = originalStageEffects;
    }
});

test("desktop transparent stage disables residual stage gradient when effects are off", async () => {
    const { DOM } = await import("../echobot/app/web/core/dom.js");
    const { live2dState } = await import("../echobot/app/web/core/store.js");
    const { createStageEffectsController } = await import(
        "../echobot/app/web/features/live2d/effects.js"
    );

    const originalStageElement = DOM.stageElement;
    const originalStageGradient = DOM.stageGradient;
    const originalStageLightBack = DOM.stageLightBack;
    const originalStageLightRim = DOM.stageLightRim;
    const originalStageVignette = DOM.stageVignette;
    const originalStageGrain = DOM.stageGrain;
    const originalParticleLayer = live2dState.live2dParticleLayer;
    const originalPostFilter = live2dState.stagePostFilter;
    const originalBlurFilter = live2dState.stageBackgroundBlurFilter;
    const originalPixiApp = live2dState.pixiApp;
    const originalEffects = live2dState.stageEffects;

    DOM.stageElement = createStageElement("true");
    DOM.stageGradient = {
        style: {
            opacity: "",
        },
    };
    DOM.stageLightBack = {
        style: {
            opacity: "",
        },
    };
    DOM.stageLightRim = {
        style: {
            opacity: "",
        },
    };
    DOM.stageVignette = {
        style: {
            opacity: "",
        },
    };
    DOM.stageGrain = {
        style: {
            opacity: "",
        },
    };
    live2dState.live2dParticleLayer = {
        visible: true,
    };
    live2dState.stagePostFilter = {
        enabled: true,
        uniforms: {
            uGlowStrength: 1,
            uGrainStrength: 1,
            uVignetteStrength: 1,
            uLightPos: [0, 0],
        },
    };
    live2dState.stageBackgroundBlurFilter = {
        blur: 8,
    };
    live2dState.pixiApp = null;

    try {
        const controller = createStageEffectsController({
            clamp(value, min, max) {
                return Math.min(Math.max(value, min), max);
            },
            roundTo(value) {
                return value;
            },
            setRunStatus() {},
            applyStageLightingVars() {},
            updateStageAtmosphereFrame() {},
        });

        controller.applyStageEffectsToRuntime({
            enabled: false,
            backgroundBlurEnabled: false,
            backgroundBlur: 0,
            lightEnabled: false,
            lightFloatEnabled: false,
            particlesEnabled: false,
            particleDensity: 0,
            particleOpacity: 0,
            particleSize: 100,
            particleSpeed: 0,
            lightX: 0,
            lightY: 0,
            glowStrength: 0,
            vignetteStrength: 0,
            grainStrength: 0,
            hue: 0,
            saturation: 100,
            contrast: 100,
        });

        assert.equal(DOM.stageGradient.style.opacity, "0");
        assert.equal(DOM.stageLightBack.style.opacity, "0");
        assert.equal(DOM.stageLightRim.style.opacity, "0");
        assert.equal(DOM.stageVignette.style.opacity, "0");
        assert.equal(DOM.stageGrain.style.opacity, "0");
        assert.equal(live2dState.stagePostFilter.enabled, false);
    } finally {
        DOM.stageElement = originalStageElement;
        DOM.stageGradient = originalStageGradient;
        DOM.stageLightBack = originalStageLightBack;
        DOM.stageLightRim = originalStageLightRim;
        DOM.stageVignette = originalStageVignette;
        DOM.stageGrain = originalStageGrain;
        live2dState.live2dParticleLayer = originalParticleLayer;
        live2dState.stagePostFilter = originalPostFilter;
        live2dState.stageBackgroundBlurFilter = originalBlurFilter;
        live2dState.pixiApp = originalPixiApp;
        live2dState.stageEffects = originalEffects;
    }
});
