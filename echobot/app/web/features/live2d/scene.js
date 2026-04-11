import { DOM } from "../../core/dom.js";
import { live2dState } from "../../core/store.js";
import {
    ATMOSPHERE_FILTER_FRAGMENT,
    DEFAULT_STAGE_BACKGROUND_TRANSFORM,
    DEFAULT_STAGE_EFFECT_SETTINGS,
    DEFAULT_STAGE_LIGHT_POSITION,
    DEFAULT_STAGE_RIM_LIGHT_POSITION,
    STAGE_PARTICLE_COUNT,
} from "./constants.js";
import { buildPixiApplicationOptions } from "./application-options.js";

export function createLive2DSceneController(deps) {
    const {
        clamp,
        roundTo,
        applyStageEffectsSettings,
        applyStageBackgroundTransform,
        currentStageBackgroundOption,
        refreshLive2DFocusFromLastPointer,
        syncPixiStageBackground,
    } = deps;

    function updateSceneFilterBounds() {
        if (!live2dState.pixiApp || !live2dState.live2dStage) {
            return;
        }

        live2dState.live2dStage.hitArea = live2dState.pixiApp.screen;
        if (live2dState.live2dScene) {
            live2dState.live2dScene.filterArea = live2dState.pixiApp.screen;
        }
        if (live2dState.live2dBackgroundLayer) {
            live2dState.live2dBackgroundLayer.filterArea = live2dState.pixiApp.screen;
        }
    }

    function createStagePostFilter() {
        const filter = new window.PIXI.Filter(undefined, ATMOSPHERE_FILTER_FRAGMENT, {
            uLightPos: [DEFAULT_STAGE_LIGHT_POSITION.x, DEFAULT_STAGE_LIGHT_POSITION.y],
            uAmbientColor: [1, 1, 1],
            uHighlightColor: [0, 0, 0],
            uGlowStrength: 0,
            uGrainStrength: 0,
            uVignetteStrength: 0,
            uPulse: 1,
            uTime: 0,
        });
        filter.enabled = false;
        return filter;
    }

    function randomBetween(min, max) {
        return min + Math.random() * (max - min);
    }

    function createSoftParticleTexture(size, colorStops) {
        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const context = canvas.getContext("2d");
        if (!context) {
            return window.PIXI.Texture.WHITE;
        }

        const gradient = context.createRadialGradient(
            size * 0.5,
            size * 0.5,
            0,
            size * 0.5,
            size * 0.5,
            size * 0.5,
        );
        colorStops.forEach(([offset, color]) => {
            gradient.addColorStop(offset, color);
        });

        context.fillStyle = gradient;
        context.fillRect(0, 0, size, size);
        return window.PIXI.Texture.from(canvas);
    }

    function ensureStageParticleTextures() {
        if (live2dState.stageParticleTextures) {
            return live2dState.stageParticleTextures;
        }

        live2dState.stageParticleTextures = createSoftParticleTexture(80, [
            [0, "rgba(255,255,255,0.98)"],
            [0.16, "rgba(255,251,246,0.68)"],
            [0.38, "rgba(255,244,232,0.22)"],
            [1, "rgba(255,255,255,0)"],
        ]);
        return live2dState.stageParticleTextures;
    }

    function resetStageParticleSprite(sprite, stageWidth, stageHeight, spawnEdge = "random") {
        if (!sprite || !sprite.stageParticle) {
            return;
        }

        const particle = sprite.stageParticle;
        const margin = 110;
        const width = Math.max(stageWidth, 1);
        const height = Math.max(stageHeight, 1);

        particle.margin = margin;
        particle.baseAlpha = randomBetween(0.2, 0.42);
        particle.baseScale = randomBetween(0.12, 0.3);
        particle.driftX = randomBetween(-12, 12);
        particle.driftY = randomBetween(-20, -8);
        particle.wobbleAmplitudeX = randomBetween(10, 26);
        particle.wobbleAmplitudeY = randomBetween(6, 14);
        particle.wobbleSpeed = randomBetween(0.35, 0.95);
        particle.pulseSpeed = randomBetween(0.55, 1.35);
        particle.wobblePhase = randomBetween(0, Math.PI * 2);
        particle.pulsePhase = randomBetween(0, Math.PI * 2);
        particle.rotationSpeed = randomBetween(-0.08, 0.08);
        particle.baseX = randomBetween(-margin, width + margin);
        particle.baseY = spawnEdge === "bottom"
            ? height + randomBetween(0, margin)
            : randomBetween(-margin, height + margin);

        sprite.scale.set(particle.baseScale);
        sprite.rotation = randomBetween(0, Math.PI * 2);
        sprite.alpha = 0;
        sprite.visible = false;
        sprite.tint = Math.random() < 0.5 ? 0xfffdf8 : 0xf5efe7;
    }

    function ensureStageParticleLayer() {
        if (live2dState.live2dParticleLayer) {
            return live2dState.live2dParticleLayer;
        }

        const texture = ensureStageParticleTextures();
        const layer = new window.PIXI.Container();
        layer.interactiveChildren = false;
        live2dState.live2dParticleLayer = layer;
        live2dState.stageParticleSprites = [];

        for (let index = 0; index < STAGE_PARTICLE_COUNT; index += 1) {
            const sprite = new window.PIXI.Sprite(texture);
            sprite.anchor.set(0.5);
            sprite.interactive = false;
            sprite.blendMode = window.PIXI.BLEND_MODES.SCREEN;
            sprite.stageParticle = {
                margin: 0,
                baseAlpha: 0,
                baseScale: 0,
                baseX: 0,
                baseY: 0,
                driftX: 0,
                driftY: 0,
                wobbleAmplitudeX: 0,
                wobbleAmplitudeY: 0,
                wobbleSpeed: 0,
                pulseSpeed: 0,
                wobblePhase: 0,
                pulsePhase: 0,
                rotationSpeed: 0,
            };
            resetStageParticleSprite(sprite, 1, 1);
            live2dState.stageParticleSprites.push(sprite);
            layer.addChild(sprite);
        }

        return layer;
    }

    function resetAllStageParticles(stageWidth, stageHeight) {
        if (!Array.isArray(live2dState.stageParticleSprites)) {
            return;
        }

        live2dState.stageParticleSprites.forEach((sprite) => {
            if (!sprite || !sprite.stageParticle) {
                return;
            }
            resetStageParticleSprite(sprite, stageWidth, stageHeight, "random");
        });
    }

    function resolveStageParticleTargets(settings) {
        const density = clamp(settings.particleDensity / 100, 0, 1);
        return {
            density: density,
            count: density <= 0
                ? 0
                : Math.max(1, Math.round(STAGE_PARTICLE_COUNT * Math.pow(density, 0.72))),
        };
    }

    function updateStageParticleLayer(now, deltaSeconds) {
        if (
            !live2dState.pixiApp
            || !live2dState.live2dParticleLayer
            || !Array.isArray(live2dState.stageParticleSprites)
            || live2dState.stageParticleSprites.length === 0
        ) {
            return;
        }

        const settings = live2dState.stageEffects || DEFAULT_STAGE_EFFECT_SETTINGS;
        const particlesEnabled = settings.enabled && settings.particlesEnabled;
        live2dState.live2dParticleLayer.visible = particlesEnabled;
        if (!particlesEnabled) {
            return;
        }

        const { density, count } = resolveStageParticleTargets(settings);
        const stageWidth = Math.max(live2dState.pixiApp.screen.width, 1);
        const stageHeight = Math.max(live2dState.pixiApp.screen.height, 1);
        const lightPosX = live2dState.stageLightCurrentX * stageWidth;
        const lightPosY = live2dState.stageLightCurrentY * stageHeight;
        const speedMultiplier = clamp(settings.particleSpeed / 100, 0, 3);
        const sizeMultiplier = clamp(settings.particleSize / 100, 0.4, 2.8);
        const opacityMultiplier = clamp(settings.particleOpacity / 100, 0, 1.8);
        const densityAlpha = clamp(0.72 + density * 1.18, 0.48, 1.9);
        const lightBoost = settings.lightEnabled
            ? clamp(0.96 + settings.glowStrength / 160, 0.96, 1.6)
            : 1;
        const motionSpeed = Math.max(speedMultiplier, 0.05);
        let visibleCount = 0;

        live2dState.stageParticleSprites.forEach((sprite) => {
            const particle = sprite.stageParticle;
            if (!particle) {
                return;
            }

            const isVisible = visibleCount < count;
            visibleCount += 1;
            sprite.visible = isVisible;
            if (!isVisible) {
                sprite.alpha = 0;
                return;
            }

            particle.baseX += particle.driftX * deltaSeconds * speedMultiplier;
            particle.baseY += particle.driftY * deltaSeconds * speedMultiplier;

            const wobbleX = Math.sin(now * particle.wobbleSpeed * motionSpeed + particle.wobblePhase)
                * particle.wobbleAmplitudeX;
            const wobbleY = Math.cos(
                now * particle.wobbleSpeed * 0.72 * motionSpeed + particle.wobblePhase,
            ) * particle.wobbleAmplitudeY;
            sprite.x = particle.baseX + wobbleX;
            sprite.y = particle.baseY + wobbleY;
            sprite.rotation += particle.rotationSpeed * deltaSeconds * motionSpeed;

            const lightDistance = Math.hypot(sprite.x - lightPosX, sprite.y - lightPosY);
            const lightRadius = Math.max(stageWidth, stageHeight) * 0.86;
            const lightFactor = clamp(
                1 - lightDistance / Math.max(lightRadius, 1),
                0.78,
                1,
            );
            const pulse = 0.92 + Math.sin(
                now * particle.pulseSpeed * motionSpeed + particle.pulsePhase,
            ) * 0.08;
            sprite.alpha = clamp(
                particle.baseAlpha
                * densityAlpha
                * opacityMultiplier
                * lightBoost
                * lightFactor
                * pulse,
                0,
                1,
            );

            const scalePulse = 0.96 + Math.sin(
                now * particle.pulseSpeed * motionSpeed + particle.pulsePhase,
            ) * 0.04;
            sprite.scale.set(particle.baseScale * sizeMultiplier * scalePulse);

            if (
                sprite.y < -particle.margin
                || sprite.x < -particle.margin * 1.5
                || sprite.x > stageWidth + particle.margin * 1.5
            ) {
                resetStageParticleSprite(sprite, stageWidth, stageHeight, "bottom");
            }
        });
    }

    function applyStageLightingVars(lightX, lightY, pulse) {
        if (!DOM.stageElement) {
            return;
        }

        const rimX = clamp(
            lightX + (DEFAULT_STAGE_RIM_LIGHT_POSITION.x - DEFAULT_STAGE_LIGHT_POSITION.x),
            0.12,
            0.9,
        );
        const rimY = clamp(
            lightY + (DEFAULT_STAGE_RIM_LIGHT_POSITION.y - DEFAULT_STAGE_LIGHT_POSITION.y),
            0.16,
            0.82,
        );
        DOM.stageElement.style.setProperty("--stage-light-x", `${roundTo(lightX * 100, 1)}%`);
        DOM.stageElement.style.setProperty("--stage-light-y", `${roundTo(lightY * 100, 1)}%`);
        DOM.stageElement.style.setProperty("--stage-light-rim-x", `${roundTo(rimX * 100, 1)}%`);
        DOM.stageElement.style.setProperty("--stage-light-rim-y", `${roundTo(rimY * 100, 1)}%`);
        DOM.stageElement.style.setProperty("--stage-pulse", String(roundTo(pulse, 3)));
    }

    function updateStageAtmosphereFrame() {
        if (!live2dState.pixiApp) {
            return;
        }

        const effects = live2dState.stageEffects || DEFAULT_STAGE_EFFECT_SETTINGS;
        const lightEnabled = effects.enabled && effects.lightEnabled;
        const now = performance.now() / 1000;
        const deltaSeconds = clamp(
            live2dState.pixiApp.ticker && Number.isFinite(live2dState.pixiApp.ticker.deltaMS)
                ? live2dState.pixiApp.ticker.deltaMS / 1000
                : 1 / 60,
            1 / 120,
            0.05,
        );
        const manualLightX = effects.lightX / 100;
        const manualLightY = effects.lightY / 100;
        const baseLightX = effects.lightFloatEnabled && lightEnabled
            ? manualLightX + Math.sin(now * 0.37) * 0.028
            : manualLightX;
        const baseLightY = effects.lightFloatEnabled && lightEnabled
            ? manualLightY + Math.cos(now * 0.29) * 0.018
            : manualLightY;
        const targetX = clamp(baseLightX, 0, 1);
        const targetY = clamp(baseLightY, 0, 1);

        live2dState.stageLightCurrentX += (targetX - live2dState.stageLightCurrentX) * 0.08;
        live2dState.stageLightCurrentY += (targetY - live2dState.stageLightCurrentY) * 0.08;

        const pulse = lightEnabled
            ? 0.96 + Math.sin(now * 1.7) * 0.04
            : 0.9;
        applyStageLightingVars(
            live2dState.stageLightCurrentX,
            live2dState.stageLightCurrentY,
            pulse,
        );

        if (live2dState.stagePostFilter) {
            live2dState.stagePostFilter.uniforms.uLightPos = [
                live2dState.stageLightCurrentX,
                live2dState.stageLightCurrentY,
            ];
            live2dState.stagePostFilter.uniforms.uPulse = pulse;
            live2dState.stagePostFilter.uniforms.uTime = now;
        }

        updateStageParticleLayer(now, deltaSeconds);
        updateSceneFilterBounds();
    }

    function installStageAtmosphereTicker() {
        if (!live2dState.pixiApp || live2dState.stageAtmosphereTick) {
            return;
        }

        live2dState.stageAtmosphereTick = () => {
            updateStageAtmosphereFrame();
        };
        live2dState.pixiApp.ticker.add(live2dState.stageAtmosphereTick);
        updateStageAtmosphereFrame();
    }

    function ensureStageResizeObserver() {
        if (!window.ResizeObserver || live2dState.stageResizeObserver || !DOM.stageElement) {
            return;
        }

        live2dState.stageResizeObserver = new window.ResizeObserver(() => {
            window.requestAnimationFrame(() => {
                updateSceneFilterBounds();
                if (live2dState.pixiApp) {
                    resetAllStageParticles(
                        Math.max(live2dState.pixiApp.screen.width, 1),
                        Math.max(live2dState.pixiApp.screen.height, 1),
                    );
                }
                if (live2dState.currentStageBackgroundTransform) {
                    applyStageBackgroundTransform(live2dState.currentStageBackgroundTransform);
                }
                refreshLive2DFocusFromLastPointer();
            });
        });
        live2dState.stageResizeObserver.observe(DOM.stageElement);
    }

    function createStageScene() {
        live2dState.live2dScene = new window.PIXI.Container();
        live2dState.live2dBackgroundLayer = new window.PIXI.Container();
        live2dState.live2dParticleLayer = ensureStageParticleLayer();
        live2dState.live2dCharacterLayer = new window.PIXI.Container();
        live2dState.stageLightCurrentX = DEFAULT_STAGE_LIGHT_POSITION.x;
        live2dState.stageLightCurrentY = DEFAULT_STAGE_LIGHT_POSITION.y;

        live2dState.stageBackgroundBlurFilter = new window.PIXI.filters.BlurFilter();
        live2dState.stageBackgroundBlurFilter.blur = 0;
        live2dState.live2dBackgroundLayer.filters = [live2dState.stageBackgroundBlurFilter];

        live2dState.stagePostFilter = createStagePostFilter();
        live2dState.live2dScene.filters = [live2dState.stagePostFilter];

        live2dState.live2dScene.addChild(live2dState.live2dBackgroundLayer);
        live2dState.live2dScene.addChild(live2dState.live2dParticleLayer);
        live2dState.live2dScene.addChild(live2dState.live2dCharacterLayer);
        live2dState.live2dStage.addChild(live2dState.live2dScene);

        if (live2dState.pixiApp) {
            resetAllStageParticles(
                Math.max(live2dState.pixiApp.screen.width, 1),
                Math.max(live2dState.pixiApp.screen.height, 1),
            );
        }

        ensureStageResizeObserver();
        updateSceneFilterBounds();
        applyStageEffectsSettings(
            live2dState.stageEffects || DEFAULT_STAGE_EFFECT_SETTINGS,
            { persist: false },
        );

        const activeBackground = currentStageBackgroundOption();
        const activeTransform = live2dState.currentStageBackgroundTransform
            || DEFAULT_STAGE_BACKGROUND_TRANSFORM;
        void syncPixiStageBackground(activeBackground, activeTransform);
        installStageAtmosphereTicker();
    }

    function initializePixiApplication() {
        if (!window.PIXI) {
            throw new Error("Failed to load PIXI");
        }

        if (!window.PIXI.live2d || !window.PIXI.live2d.Live2DModel) {
            throw new Error("Failed to load pixi-live2d-display");
        }

        live2dState.pixiApp = new window.PIXI.Application(
            buildPixiApplicationOptions({
                view: document.getElementById("live2d-canvas"),
                resizeTo: DOM.stageElement,
            }),
        );
        live2dState.live2dStage = live2dState.pixiApp.stage;
        live2dState.live2dStage.interactive = true;
        live2dState.live2dStage.hitArea = live2dState.pixiApp.screen;
        createStageScene();
    }

    return {
        applyStageLightingVars,
        initializePixiApplication,
        updateStageAtmosphereFrame,
    };
}
