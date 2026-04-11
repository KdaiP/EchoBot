import test from "node:test";
import assert from "node:assert/strict";

function createDisabledStageEffects() {
    return {
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
    };
}

test("initializePixiApplication starts desktop stage filter from a neutral disabled state", async () => {
    const originalDocument = globalThis.document;
    const originalWindow = globalThis.window;

    const canvasElement = {};
    const stageElement = {
        dataset: {
            desktopTransparentStage: "true",
        },
        style: {
            setProperty() {},
            removeProperty() {},
        },
        classList: {
            add() {},
            remove() {},
        },
    };

    globalThis.document = {
        getElementById(id) {
            if (id === "live2d-canvas") {
                return canvasElement;
            }
            return null;
        },
        createElement(tagName) {
            if (tagName !== "canvas") {
                throw new Error(`Unexpected element: ${tagName}`);
            }

            return {
                width: 0,
                height: 0,
                getContext() {
                    return {
                        createRadialGradient() {
                            return {
                                addColorStop() {},
                            };
                        },
                        fillRect() {},
                        set fillStyle(_value) {},
                    };
                },
            };
        },
    };

    globalThis.window = {
        localStorage: {
            getItem() {
                return null;
            },
            setItem() {},
            removeItem() {},
        },
        PIXI: {
            Application: class {
                constructor(options) {
                    this.options = options;
                    this.stage = {
                        interactive: false,
                        hitArea: null,
                        addChild() {},
                    };
                    this.screen = {
                        width: 420,
                        height: 620,
                    };
                    this.ticker = {
                        deltaMS: 16,
                        add() {},
                    };
                }
            },
            Container: class {
                constructor() {
                    this.filters = null;
                    this.filterArea = null;
                    this.interactiveChildren = true;
                }

                addChild() {}
            },
            Sprite: class {
                constructor(texture) {
                    this.texture = texture;
                    this.anchor = {
                        set() {},
                    };
                    this.scale = {
                        set() {},
                    };
                    this.visible = true;
                    this.alpha = 1;
                    this.rotation = 0;
                    this.tint = 0;
                    this.interactive = false;
                    this.blendMode = null;
                    this.stageParticle = null;
                }
            },
            Filter: class {
                constructor(_vertex, _fragment, uniforms) {
                    this.enabled = true;
                    this.uniforms = {
                        ...uniforms,
                    };
                }
            },
            Texture: {
                WHITE: { id: "white" },
            },
            filters: {
                BlurFilter: class {
                    constructor() {
                        this.blur = 0;
                    }
                },
            },
            BLEND_MODES: {
                SCREEN: "screen",
            },
            live2d: {
                Live2DModel: class {},
            },
        },
    };

    const { DOM } = await import("../echobot/app/web/core/dom.js");
    const { live2dState } = await import("../echobot/app/web/core/store.js");
    const { createLive2DSceneController } = await import(
        "../echobot/app/web/features/live2d/scene.js"
    );

    const originalStageElement = DOM.stageElement;
    const originalPixiApp = live2dState.pixiApp;
    const originalStage = live2dState.live2dStage;
    const originalScene = live2dState.live2dScene;
    const originalBackgroundLayer = live2dState.live2dBackgroundLayer;
    const originalParticleLayer = live2dState.live2dParticleLayer;
    const originalCharacterLayer = live2dState.live2dCharacterLayer;
    const originalPostFilter = live2dState.stagePostFilter;
    const originalBlurFilter = live2dState.stageBackgroundBlurFilter;
    const originalStageEffects = live2dState.stageEffects;
    const originalParticleTextures = live2dState.stageParticleTextures;
    const originalParticleSprites = live2dState.stageParticleSprites;
    const originalAtmosphereTick = live2dState.stageAtmosphereTick;
    const originalResizeObserver = live2dState.stageResizeObserver;

    DOM.stageElement = stageElement;
    live2dState.stageEffects = createDisabledStageEffects();
    live2dState.stageParticleTextures = { id: "particle-texture" };
    live2dState.stageParticleSprites = [];
    live2dState.stageAtmosphereTick = null;
    live2dState.stageResizeObserver = null;

    let filterSnapshot = null;

    try {
        const controller = createLive2DSceneController({
            clamp(value, min, max) {
                return Math.min(Math.max(value, min), max);
            },
            roundTo(value) {
                return value;
            },
            applyStageEffectsSettings() {
                filterSnapshot = {
                    enabled: live2dState.stagePostFilter?.enabled,
                    uniforms: {
                        ...live2dState.stagePostFilter?.uniforms,
                    },
                };
            },
            applyStageBackgroundTransform() {},
            currentStageBackgroundOption() {
                return null;
            },
            refreshLive2DFocusFromLastPointer() {},
            syncPixiStageBackground() {},
        });

        controller.initializePixiApplication();

        assert.deepEqual(filterSnapshot, {
            enabled: false,
            uniforms: {
                uLightPos: [0, 0],
                uAmbientColor: [1, 1, 1],
                uHighlightColor: [0, 0, 0],
                uGlowStrength: 0,
                uGrainStrength: 0,
                uVignetteStrength: 0,
                uPulse: filterSnapshot.uniforms.uPulse,
                uTime: filterSnapshot.uniforms.uTime,
            },
        });
    } finally {
        DOM.stageElement = originalStageElement;
        live2dState.pixiApp = originalPixiApp;
        live2dState.live2dStage = originalStage;
        live2dState.live2dScene = originalScene;
        live2dState.live2dBackgroundLayer = originalBackgroundLayer;
        live2dState.live2dParticleLayer = originalParticleLayer;
        live2dState.live2dCharacterLayer = originalCharacterLayer;
        live2dState.stagePostFilter = originalPostFilter;
        live2dState.stageBackgroundBlurFilter = originalBlurFilter;
        live2dState.stageEffects = originalStageEffects;
        live2dState.stageParticleTextures = originalParticleTextures;
        live2dState.stageParticleSprites = originalParticleSprites;
        live2dState.stageAtmosphereTick = originalAtmosphereTick;
        live2dState.stageResizeObserver = originalResizeObserver;
        globalThis.document = originalDocument;
        globalThis.window = originalWindow;
    }
});
