import { DOM } from "../../core/dom.js";
import { live2dState } from "../../core/store.js";
import { readJson, writeJson } from "../../core/storage.js";
import {
    DEFAULT_STAGE_EFFECT_SETTINGS,
    STAGE_EFFECTS_STORAGE_KEY,
} from "./constants.js";

const STAGE_EFFECTS_SAVE_DELAY_MS = 180;

const STAGE_EFFECT_TOGGLE_FIELDS = Object.freeze([
    {
        key: "enabled",
        domKey: "stageEffectsEnabledCheckbox",
        lockGroup: "",
    },
    {
        key: "backgroundBlurEnabled",
        domKey: "stageEffectsBackgroundBlurCheckbox",
        lockGroup: "all",
    },
    {
        key: "lightEnabled",
        domKey: "stageEffectsLightEnabledCheckbox",
        lockGroup: "all",
    },
    {
        key: "lightFloatEnabled",
        domKey: "stageEffectsLightFloatCheckbox",
        lockGroup: "light",
    },
    {
        key: "particlesEnabled",
        domKey: "stageEffectsParticlesEnabledCheckbox",
        lockGroup: "all",
    },
]);

const STAGE_EFFECT_NUMBER_FIELDS = Object.freeze([
    {
        key: "backgroundBlur",
        domKey: "stageEffectsBackgroundBlurInput",
        valueDomKey: "stageEffectsBackgroundBlurValue",
        min: 0,
        max: 16,
        decimals: 1,
        unit: "",
        lockGroup: "blur",
    },
    {
        key: "lightX",
        domKey: "stageEffectsLightXInput",
        valueDomKey: "stageEffectsLightXValue",
        min: 0,
        max: 100,
        decimals: 0,
        unit: "%",
        lockGroup: "light",
    },
    {
        key: "lightY",
        domKey: "stageEffectsLightYInput",
        valueDomKey: "stageEffectsLightYValue",
        min: 0,
        max: 100,
        decimals: 0,
        unit: "%",
        lockGroup: "light",
    },
    {
        key: "glowStrength",
        domKey: "stageEffectsGlowInput",
        valueDomKey: "stageEffectsGlowValue",
        min: 0,
        max: 160,
        decimals: 0,
        unit: "%",
        lockGroup: "light",
    },
    {
        key: "vignetteStrength",
        domKey: "stageEffectsVignetteInput",
        valueDomKey: "stageEffectsVignetteValue",
        min: 0,
        max: 60,
        decimals: 0,
        unit: "%",
        lockGroup: "all",
    },
    {
        key: "grainStrength",
        domKey: "stageEffectsGrainInput",
        valueDomKey: "stageEffectsGrainValue",
        min: 0,
        max: 40,
        decimals: 0,
        unit: "%",
        lockGroup: "all",
    },
    {
        key: "particleDensity",
        domKey: "stageEffectsParticleDensityInput",
        valueDomKey: "stageEffectsParticleDensityValue",
        min: 0,
        max: 100,
        decimals: 0,
        unit: "%",
        lockGroup: "particle",
    },
    {
        key: "particleOpacity",
        domKey: "stageEffectsParticleOpacityInput",
        valueDomKey: "stageEffectsParticleOpacityValue",
        min: 0,
        max: 160,
        decimals: 0,
        unit: "%",
        lockGroup: "particle",
    },
    {
        key: "particleSize",
        domKey: "stageEffectsParticleSizeInput",
        valueDomKey: "stageEffectsParticleSizeValue",
        min: 40,
        max: 240,
        decimals: 0,
        unit: "%",
        lockGroup: "particle",
    },
    {
        key: "particleSpeed",
        domKey: "stageEffectsParticleSpeedInput",
        valueDomKey: "stageEffectsParticleSpeedValue",
        min: 0,
        max: 260,
        decimals: 0,
        unit: "%",
        lockGroup: "particle",
    },
    {
        key: "hue",
        domKey: "stageEffectsHueInput",
        valueDomKey: "stageEffectsHueValue",
        min: -180,
        max: 180,
        decimals: 0,
        unit: "deg",
        lockGroup: "all",
    },
    {
        key: "saturation",
        domKey: "stageEffectsSaturationInput",
        valueDomKey: "stageEffectsSaturationValue",
        min: 0,
        max: 200,
        decimals: 0,
        unit: "%",
        lockGroup: "all",
    },
    {
        key: "contrast",
        domKey: "stageEffectsContrastInput",
        valueDomKey: "stageEffectsContrastValue",
        min: 0,
        max: 200,
        decimals: 0,
        unit: "%",
        lockGroup: "all",
    },
]);

export function createStageEffectsController(deps) {
    const {
        clamp,
        roundTo,
        setRunStatus,
        applyStageLightingVars,
        updateStageAtmosphereFrame,
    } = deps;

    let effectsSaveTimerId = 0;
    let pendingEffectsSettings = null;

    function normalizeStageEffectsSettings(settings) {
        const input = settings || {};
        const normalized = {};

        STAGE_EFFECT_TOGGLE_FIELDS.forEach((field) => {
            normalized[field.key] = input[field.key] !== false;
        });

        STAGE_EFFECT_NUMBER_FIELDS.forEach((field) => {
            normalized[field.key] = normalizeStageEffectNumber(input, field);
        });

        return normalized;
    }

    function normalizeStageEffectNumber(input, field) {
        const value = Number.parseFloat(String(input[field.key]));
        const fallback = DEFAULT_STAGE_EFFECT_SETTINGS[field.key];
        return roundTo(
            clamp(
                Number.isFinite(value) ? value : fallback,
                field.min,
                field.max,
            ),
            field.decimals,
        );
    }

    function loadSavedStageEffectsSettings() {
        const payload = readJson(STAGE_EFFECTS_STORAGE_KEY);
        if (!payload) {
            return {
                ...DEFAULT_STAGE_EFFECT_SETTINGS,
            };
        }

        try {
            return normalizeStageEffectsSettings(payload);
        } catch (error) {
            console.warn("Failed to read saved stage effects settings", error);
            return {
                ...DEFAULT_STAGE_EFFECT_SETTINGS,
            };
        }
    }

    function persistStageEffectsSettings(settings) {
        writeJson(
            STAGE_EFFECTS_STORAGE_KEY,
            normalizeStageEffectsSettings(settings),
        );
    }

    function scheduleStageEffectsPersist(settings) {
        pendingEffectsSettings = normalizeStageEffectsSettings(settings);

        if (effectsSaveTimerId) {
            window.clearTimeout(effectsSaveTimerId);
            effectsSaveTimerId = 0;
        }

        effectsSaveTimerId = window.setTimeout(() => {
            effectsSaveTimerId = 0;
            flushStageEffectsPersist();
        }, STAGE_EFFECTS_SAVE_DELAY_MS);
    }

    function flushStageEffectsPersist() {
        if (effectsSaveTimerId) {
            window.clearTimeout(effectsSaveTimerId);
            effectsSaveTimerId = 0;
        }

        if (!pendingEffectsSettings) {
            return;
        }

        persistStageEffectsSettings(pendingEffectsSettings);
        pendingEffectsSettings = null;
    }

    function updateStageEffectsValueLabels(settings) {
        STAGE_EFFECT_NUMBER_FIELDS.forEach((field) => {
            const output = DOM[field.valueDomKey];
            if (output) {
                output.textContent = formatStageEffectValue(settings[field.key], field.unit);
            }
        });
    }

    function syncStageEffectsInputs(settings) {
        STAGE_EFFECT_TOGGLE_FIELDS.forEach((field) => {
            const checkbox = DOM[field.domKey];
            if (checkbox) {
                checkbox.checked = settings[field.key];
            }
        });

        STAGE_EFFECT_NUMBER_FIELDS.forEach((field) => {
            const input = DOM[field.domKey];
            if (input) {
                input.value = String(settings[field.key]);
            }
        });

        updateStageEffectsValueLabels(settings);
    }

    function updateStageEffectsControls(settings) {
        const controlsLocked = !settings.enabled;
        const locks = {
            all: controlsLocked,
            blur: controlsLocked || !settings.backgroundBlurEnabled,
            light: controlsLocked || !settings.lightEnabled,
            particle: controlsLocked || !settings.particlesEnabled,
        };

        STAGE_EFFECT_TOGGLE_FIELDS.forEach((field) => {
            const checkbox = DOM[field.domKey];
            if (checkbox && field.lockGroup) {
                checkbox.disabled = Boolean(locks[field.lockGroup]);
            }
        });

        STAGE_EFFECT_NUMBER_FIELDS.forEach((field) => {
            const input = DOM[field.domKey];
            if (input) {
                input.disabled = Boolean(locks[field.lockGroup]);
            }
        });
    }

    function formatStageEffectValue(value, unit) {
        if (unit === "deg") {
            return `${value}\u00B0`;
        }
        return unit ? `${value}${unit}` : String(value);
    }

    function buildStageColorAdjustmentCss(settings) {
        const hasColorAdjustment = (
            settings.hue !== DEFAULT_STAGE_EFFECT_SETTINGS.hue
            || settings.saturation !== DEFAULT_STAGE_EFFECT_SETTINGS.saturation
            || settings.contrast !== DEFAULT_STAGE_EFFECT_SETTINGS.contrast
        );

        if (!settings.enabled || !hasColorAdjustment) {
            return "";
        }

        return [
            `hue-rotate(${settings.hue}deg)`,
            `saturate(${settings.saturation}%)`,
            `contrast(${settings.contrast}%)`,
        ].join(" ");
    }

    function applyStageEffectsToRuntime(settings) {
        const effectsEnabled = settings.enabled;
        const lightEnabled = effectsEnabled && settings.lightEnabled;
        const particlesEnabled = effectsEnabled && settings.particlesEnabled && settings.particleDensity > 0;
        const baseLightX = settings.lightX / 100;
        const baseLightY = settings.lightY / 100;

        if (live2dState.stageBackgroundBlurFilter) {
            live2dState.stageBackgroundBlurFilter.blur = (
                effectsEnabled && settings.backgroundBlurEnabled
            )
                ? settings.backgroundBlur
                : 0;
        }

        if (live2dState.stagePostFilter) {
            live2dState.stagePostFilter.enabled = effectsEnabled;
            live2dState.stagePostFilter.uniforms.uGlowStrength = lightEnabled
                ? settings.glowStrength / 100
                : 0;
            live2dState.stagePostFilter.uniforms.uGrainStrength = effectsEnabled
                ? settings.grainStrength / 16
                : 0;
            live2dState.stagePostFilter.uniforms.uVignetteStrength = effectsEnabled
                ? settings.vignetteStrength / 100
                : 0;
            live2dState.stagePostFilter.uniforms.uLightPos = [baseLightX, baseLightY];
        }

        if (DOM.stageLightBack) {
            DOM.stageLightBack.style.opacity = lightEnabled
                ? String(clamp(0.24 + settings.glowStrength / 145, 0, 0.98))
                : "0";
        }
        if (DOM.stageLightRim) {
            DOM.stageLightRim.style.opacity = lightEnabled
                ? String(clamp(0.14 + settings.glowStrength / 240, 0, 0.82))
                : "0";
        }
        if (DOM.stageVignette) {
            DOM.stageVignette.style.opacity = effectsEnabled
                ? String(clamp(settings.vignetteStrength / 24, 0, 1))
                : "0";
        }
        if (DOM.stageGrain) {
            DOM.stageGrain.style.opacity = effectsEnabled
                ? String(clamp(settings.grainStrength / 100, 0, 0.4))
                : "0";
        }
        if (DOM.stageGradient) {
            DOM.stageGradient.style.opacity = effectsEnabled ? "1" : "0.35";
        }
        if (live2dState.live2dParticleLayer) {
            live2dState.live2dParticleLayer.visible = particlesEnabled;
        }
        if (DOM.stageElement) {
            const colorAdjustment = buildStageColorAdjustmentCss(settings);
            if (colorAdjustment) {
                DOM.stageElement.style.setProperty("--stage-color-adjustment", colorAdjustment);
                DOM.stageElement.classList.add("has-stage-color-adjustment");
            } else {
                DOM.stageElement.style.removeProperty("--stage-color-adjustment");
                DOM.stageElement.classList.remove("has-stage-color-adjustment");
            }
        }

        live2dState.stageLightCurrentX = baseLightX;
        live2dState.stageLightCurrentY = baseLightY;
        applyStageLightingVars(baseLightX, baseLightY, lightEnabled ? 1 : 0.9);

        if (live2dState.pixiApp) {
            updateStageAtmosphereFrame();
        }
    }

    function applyStageEffectsSettings(nextSettings, options = {}) {
        const settings = normalizeStageEffectsSettings(nextSettings);
        live2dState.stageEffects = settings;

        if (options.persist === "defer") {
            scheduleStageEffectsPersist(settings);
        } else if (options.persist !== false) {
            flushStageEffectsPersist();
            persistStageEffectsSettings(settings);
        }

        syncStageEffectsInputs(settings);
        renderStageEffectsDetail(settings);
        updateStageEffectsControls(settings);
        applyStageEffectsToRuntime(settings);
    }

    function readStageEffectsSettingsFromInputs() {
        const payload = {};

        STAGE_EFFECT_TOGGLE_FIELDS.forEach((field) => {
            const checkbox = DOM[field.domKey];
            payload[field.key] = checkbox
                ? checkbox.checked
                : DEFAULT_STAGE_EFFECT_SETTINGS[field.key];
        });

        STAGE_EFFECT_NUMBER_FIELDS.forEach((field) => {
            const input = DOM[field.domKey];
            payload[field.key] = input
                ? input.value
                : DEFAULT_STAGE_EFFECT_SETTINGS[field.key];
        });

        return normalizeStageEffectsSettings(payload);
    }

    function handleStageEffectsInput() {
        applyStageEffectsSettings(readStageEffectsSettingsFromInputs(), {
            persist: "defer",
        });
    }

    function handleStageEffectsReset() {
        applyStageEffectsSettings(DEFAULT_STAGE_EFFECT_SETTINGS);
        setRunStatus("已重置光影参数");
    }

    function renderStageEffectsDetail(settings) {
        if (!DOM.stageEffectsDetail) {
            return;
        }

        if (!settings.enabled) {
            DOM.stageEffectsDetail.textContent = "当前已关闭全部光影效果";
            return;
        }

        const blurText = settings.backgroundBlurEnabled
            ? `模糊 ${settings.backgroundBlur}`
            : "模糊关闭";
        const lightText = settings.lightEnabled
            ? `光位 ${settings.lightX}% / ${settings.lightY}%`
            : "光位关闭";
        const floatText = settings.lightEnabled && settings.lightFloatEnabled
            ? "光位漂移开启"
            : "光位漂移关闭";
        const particleText = settings.particlesEnabled
            ? `粒子 ${settings.particleDensity}% / 透明 ${settings.particleOpacity}% / 尺寸 ${settings.particleSize}% / 速度 ${settings.particleSpeed}%`
            : "粒子关闭";
        const colorText = `色调 ${settings.hue}\u00B0 / 饱和 ${settings.saturation}% / 对比 ${settings.contrast}%`;
        DOM.stageEffectsDetail.textContent = `${blurText} · ${lightText} · ${floatText} · ${particleText} · 光晕 ${settings.glowStrength}% · 暗角 ${settings.vignetteStrength}% · 颗粒 ${settings.grainStrength}% · ${colorText}`;
    }

    return {
        applyStageEffectsSettings,
        applyStageEffectsToRuntime,
        handleStageEffectsInput,
        handleStageEffectsReset,
        loadSavedStageEffectsSettings,
        normalizeStageEffectsSettings,
    };
}
