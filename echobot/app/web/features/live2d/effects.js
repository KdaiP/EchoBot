import { DOM } from "../../core/dom.js";
import { live2dState } from "../../core/store.js";
import { readJson, writeJson } from "../../core/storage.js";
import {
    DEFAULT_STAGE_EFFECT_SETTINGS,
    STAGE_EFFECTS_STORAGE_KEY,
} from "./constants.js";
import { isDesktopTransparentStageEnabled } from "./desktop-stage-mode.js";

export function createStageEffectsController(deps) {
    const {
        clamp,
        roundTo,
        setRunStatus,
        applyStageLightingVars,
        updateStageAtmosphereFrame,
    } = deps;

    function normalizeStageEffectsSettings(settings) {
        const input = settings || {};
        const backgroundBlur = Number.parseFloat(String(input.backgroundBlur));
        const lightX = Number.parseFloat(String(input.lightX));
        const lightY = Number.parseFloat(String(input.lightY));
        const glowStrength = Number.parseFloat(String(input.glowStrength));
        const vignetteStrength = Number.parseFloat(String(input.vignetteStrength));
        const grainStrength = Number.parseFloat(String(input.grainStrength));
        const particleDensity = Number.parseFloat(String(input.particleDensity));
        const particleOpacity = Number.parseFloat(String(input.particleOpacity));
        const particleSize = Number.parseFloat(String(input.particleSize));
        const particleSpeed = Number.parseFloat(String(input.particleSpeed));
        const hue = Number.parseFloat(String(input.hue));
        const saturation = Number.parseFloat(String(input.saturation));
        const contrast = Number.parseFloat(String(input.contrast));

        return {
            enabled: input.enabled !== false,
            backgroundBlurEnabled: input.backgroundBlurEnabled !== false,
            backgroundBlur: roundTo(
                clamp(
                    Number.isFinite(backgroundBlur)
                        ? backgroundBlur
                        : DEFAULT_STAGE_EFFECT_SETTINGS.backgroundBlur,
                    0,
                    16,
                ),
                1,
            ),
            lightEnabled: input.lightEnabled !== false,
            lightFloatEnabled: input.lightFloatEnabled !== false,
            particlesEnabled: input.particlesEnabled !== false,
            particleDensity: roundTo(
                clamp(
                    Number.isFinite(particleDensity)
                        ? particleDensity
                        : DEFAULT_STAGE_EFFECT_SETTINGS.particleDensity,
                    0,
                    100,
                ),
                0,
            ),
            particleOpacity: roundTo(
                clamp(
                    Number.isFinite(particleOpacity)
                        ? particleOpacity
                        : DEFAULT_STAGE_EFFECT_SETTINGS.particleOpacity,
                    0,
                    160,
                ),
                0,
            ),
            particleSize: roundTo(
                clamp(
                    Number.isFinite(particleSize)
                        ? particleSize
                        : DEFAULT_STAGE_EFFECT_SETTINGS.particleSize,
                    40,
                    240,
                ),
                0,
            ),
            particleSpeed: roundTo(
                clamp(
                    Number.isFinite(particleSpeed)
                        ? particleSpeed
                        : DEFAULT_STAGE_EFFECT_SETTINGS.particleSpeed,
                    0,
                    260,
                ),
                0,
            ),
            lightX: roundTo(
                clamp(
                    Number.isFinite(lightX)
                        ? lightX
                        : DEFAULT_STAGE_EFFECT_SETTINGS.lightX,
                    0,
                    100,
                ),
                0,
            ),
            lightY: roundTo(
                clamp(
                    Number.isFinite(lightY)
                        ? lightY
                        : DEFAULT_STAGE_EFFECT_SETTINGS.lightY,
                    0,
                    100,
                ),
                0,
            ),
            glowStrength: roundTo(
                clamp(
                    Number.isFinite(glowStrength)
                        ? glowStrength
                        : DEFAULT_STAGE_EFFECT_SETTINGS.glowStrength,
                    0,
                    160,
                ),
                0,
            ),
            vignetteStrength: roundTo(
                clamp(
                    Number.isFinite(vignetteStrength)
                        ? vignetteStrength
                        : DEFAULT_STAGE_EFFECT_SETTINGS.vignetteStrength,
                    0,
                    60,
                ),
                0,
            ),
            grainStrength: roundTo(
                clamp(
                    Number.isFinite(grainStrength)
                        ? grainStrength
                        : DEFAULT_STAGE_EFFECT_SETTINGS.grainStrength,
                    0,
                    40,
                ),
                0,
            ),
            hue: roundTo(
                clamp(
                    Number.isFinite(hue)
                        ? hue
                        : DEFAULT_STAGE_EFFECT_SETTINGS.hue,
                    -180,
                    180,
                ),
                0,
            ),
            saturation: roundTo(
                clamp(
                    Number.isFinite(saturation)
                        ? saturation
                        : DEFAULT_STAGE_EFFECT_SETTINGS.saturation,
                    0,
                    200,
                ),
                0,
            ),
            contrast: roundTo(
                clamp(
                    Number.isFinite(contrast)
                        ? contrast
                        : DEFAULT_STAGE_EFFECT_SETTINGS.contrast,
                    0,
                    200,
                ),
                0,
            ),
        };
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

    function updateStageEffectsValueLabels(settings) {
        if (DOM.stageEffectsBackgroundBlurValue) {
            DOM.stageEffectsBackgroundBlurValue.textContent = String(settings.backgroundBlur);
        }
        if (DOM.stageEffectsLightXValue) {
            DOM.stageEffectsLightXValue.textContent = `${settings.lightX}%`;
        }
        if (DOM.stageEffectsLightYValue) {
            DOM.stageEffectsLightYValue.textContent = `${settings.lightY}%`;
        }
        if (DOM.stageEffectsGlowValue) {
            DOM.stageEffectsGlowValue.textContent = `${settings.glowStrength}%`;
        }
        if (DOM.stageEffectsVignetteValue) {
            DOM.stageEffectsVignetteValue.textContent = `${settings.vignetteStrength}%`;
        }
        if (DOM.stageEffectsGrainValue) {
            DOM.stageEffectsGrainValue.textContent = `${settings.grainStrength}%`;
        }
        if (DOM.stageEffectsParticleDensityValue) {
            DOM.stageEffectsParticleDensityValue.textContent = `${settings.particleDensity}%`;
        }
        if (DOM.stageEffectsParticleOpacityValue) {
            DOM.stageEffectsParticleOpacityValue.textContent = `${settings.particleOpacity}%`;
        }
        if (DOM.stageEffectsParticleSizeValue) {
            DOM.stageEffectsParticleSizeValue.textContent = `${settings.particleSize}%`;
        }
        if (DOM.stageEffectsParticleSpeedValue) {
            DOM.stageEffectsParticleSpeedValue.textContent = `${settings.particleSpeed}%`;
        }
        if (DOM.stageEffectsHueValue) {
            DOM.stageEffectsHueValue.textContent = `${settings.hue}\u00B0`;
        }
        if (DOM.stageEffectsSaturationValue) {
            DOM.stageEffectsSaturationValue.textContent = `${settings.saturation}%`;
        }
        if (DOM.stageEffectsContrastValue) {
            DOM.stageEffectsContrastValue.textContent = `${settings.contrast}%`;
        }
    }

    function syncStageEffectsInputs(settings) {
        if (DOM.stageEffectsEnabledCheckbox) {
            DOM.stageEffectsEnabledCheckbox.checked = settings.enabled;
        }
        if (DOM.stageEffectsBackgroundBlurCheckbox) {
            DOM.stageEffectsBackgroundBlurCheckbox.checked = settings.backgroundBlurEnabled;
        }
        if (DOM.stageEffectsLightEnabledCheckbox) {
            DOM.stageEffectsLightEnabledCheckbox.checked = settings.lightEnabled;
        }
        if (DOM.stageEffectsLightFloatCheckbox) {
            DOM.stageEffectsLightFloatCheckbox.checked = settings.lightFloatEnabled;
        }
        if (DOM.stageEffectsParticlesEnabledCheckbox) {
            DOM.stageEffectsParticlesEnabledCheckbox.checked = settings.particlesEnabled;
        }
        if (DOM.stageEffectsBackgroundBlurInput) {
            DOM.stageEffectsBackgroundBlurInput.value = String(settings.backgroundBlur);
        }
        if (DOM.stageEffectsLightXInput) {
            DOM.stageEffectsLightXInput.value = String(settings.lightX);
        }
        if (DOM.stageEffectsLightYInput) {
            DOM.stageEffectsLightYInput.value = String(settings.lightY);
        }
        if (DOM.stageEffectsGlowInput) {
            DOM.stageEffectsGlowInput.value = String(settings.glowStrength);
        }
        if (DOM.stageEffectsVignetteInput) {
            DOM.stageEffectsVignetteInput.value = String(settings.vignetteStrength);
        }
        if (DOM.stageEffectsGrainInput) {
            DOM.stageEffectsGrainInput.value = String(settings.grainStrength);
        }
        if (DOM.stageEffectsParticleDensityInput) {
            DOM.stageEffectsParticleDensityInput.value = String(settings.particleDensity);
        }
        if (DOM.stageEffectsParticleOpacityInput) {
            DOM.stageEffectsParticleOpacityInput.value = String(settings.particleOpacity);
        }
        if (DOM.stageEffectsParticleSizeInput) {
            DOM.stageEffectsParticleSizeInput.value = String(settings.particleSize);
        }
        if (DOM.stageEffectsParticleSpeedInput) {
            DOM.stageEffectsParticleSpeedInput.value = String(settings.particleSpeed);
        }
        if (DOM.stageEffectsHueInput) {
            DOM.stageEffectsHueInput.value = String(settings.hue);
        }
        if (DOM.stageEffectsSaturationInput) {
            DOM.stageEffectsSaturationInput.value = String(settings.saturation);
        }
        if (DOM.stageEffectsContrastInput) {
            DOM.stageEffectsContrastInput.value = String(settings.contrast);
        }

        updateStageEffectsValueLabels(settings);
    }

    function updateStageEffectsControls(settings) {
        const controlsLocked = !settings.enabled;
        const lightControlsLocked = controlsLocked || !settings.lightEnabled;
        const blurControlsLocked = controlsLocked || !settings.backgroundBlurEnabled;
        const particleControlsLocked = controlsLocked || !settings.particlesEnabled;

        if (DOM.stageEffectsBackgroundBlurCheckbox) {
            DOM.stageEffectsBackgroundBlurCheckbox.disabled = controlsLocked;
        }
        if (DOM.stageEffectsBackgroundBlurInput) {
            DOM.stageEffectsBackgroundBlurInput.disabled = blurControlsLocked;
        }
        if (DOM.stageEffectsLightEnabledCheckbox) {
            DOM.stageEffectsLightEnabledCheckbox.disabled = controlsLocked;
        }
        if (DOM.stageEffectsLightFloatCheckbox) {
            DOM.stageEffectsLightFloatCheckbox.disabled = lightControlsLocked;
        }
        if (DOM.stageEffectsParticlesEnabledCheckbox) {
            DOM.stageEffectsParticlesEnabledCheckbox.disabled = controlsLocked;
        }
        if (DOM.stageEffectsLightXInput) {
            DOM.stageEffectsLightXInput.disabled = lightControlsLocked;
        }
        if (DOM.stageEffectsLightYInput) {
            DOM.stageEffectsLightYInput.disabled = lightControlsLocked;
        }
        if (DOM.stageEffectsGlowInput) {
            DOM.stageEffectsGlowInput.disabled = lightControlsLocked;
        }
        if (DOM.stageEffectsVignetteInput) {
            DOM.stageEffectsVignetteInput.disabled = controlsLocked;
        }
        if (DOM.stageEffectsGrainInput) {
            DOM.stageEffectsGrainInput.disabled = controlsLocked;
        }
        if (DOM.stageEffectsParticleDensityInput) {
            DOM.stageEffectsParticleDensityInput.disabled = particleControlsLocked;
        }
        if (DOM.stageEffectsParticleOpacityInput) {
            DOM.stageEffectsParticleOpacityInput.disabled = particleControlsLocked;
        }
        if (DOM.stageEffectsParticleSizeInput) {
            DOM.stageEffectsParticleSizeInput.disabled = particleControlsLocked;
        }
        if (DOM.stageEffectsParticleSpeedInput) {
            DOM.stageEffectsParticleSpeedInput.disabled = particleControlsLocked;
        }
        if (DOM.stageEffectsHueInput) {
            DOM.stageEffectsHueInput.disabled = controlsLocked;
        }
        if (DOM.stageEffectsSaturationInput) {
            DOM.stageEffectsSaturationInput.disabled = controlsLocked;
        }
        if (DOM.stageEffectsContrastInput) {
            DOM.stageEffectsContrastInput.disabled = controlsLocked;
        }
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
        const particlesEnabled = effectsEnabled && settings.particlesEnabled;
        const baseLightX = settings.lightX / 100;
        const baseLightY = settings.lightY / 100;
        const transparentDesktopStage = isDesktopTransparentStageEnabled(DOM.stageElement);

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
            DOM.stageGradient.style.opacity = effectsEnabled
                ? "1"
                : (transparentDesktopStage ? "0" : "0.35");
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

        if (options.persist !== false) {
            persistStageEffectsSettings(settings);
        }

        syncStageEffectsInputs(settings);
        renderStageEffectsDetail(settings);
        updateStageEffectsControls(settings);
        applyStageEffectsToRuntime(settings);
    }

    function readStageEffectsSettingsFromInputs() {
        return normalizeStageEffectsSettings({
            enabled: DOM.stageEffectsEnabledCheckbox
                ? DOM.stageEffectsEnabledCheckbox.checked
                : DEFAULT_STAGE_EFFECT_SETTINGS.enabled,
            backgroundBlurEnabled: DOM.stageEffectsBackgroundBlurCheckbox
                ? DOM.stageEffectsBackgroundBlurCheckbox.checked
                : DEFAULT_STAGE_EFFECT_SETTINGS.backgroundBlurEnabled,
            backgroundBlur: DOM.stageEffectsBackgroundBlurInput
                ? DOM.stageEffectsBackgroundBlurInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.backgroundBlur,
            lightEnabled: DOM.stageEffectsLightEnabledCheckbox
                ? DOM.stageEffectsLightEnabledCheckbox.checked
                : DEFAULT_STAGE_EFFECT_SETTINGS.lightEnabled,
            lightFloatEnabled: DOM.stageEffectsLightFloatCheckbox
                ? DOM.stageEffectsLightFloatCheckbox.checked
                : DEFAULT_STAGE_EFFECT_SETTINGS.lightFloatEnabled,
            particlesEnabled: DOM.stageEffectsParticlesEnabledCheckbox
                ? DOM.stageEffectsParticlesEnabledCheckbox.checked
                : DEFAULT_STAGE_EFFECT_SETTINGS.particlesEnabled,
            lightX: DOM.stageEffectsLightXInput
                ? DOM.stageEffectsLightXInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.lightX,
            lightY: DOM.stageEffectsLightYInput
                ? DOM.stageEffectsLightYInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.lightY,
            glowStrength: DOM.stageEffectsGlowInput
                ? DOM.stageEffectsGlowInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.glowStrength,
            vignetteStrength: DOM.stageEffectsVignetteInput
                ? DOM.stageEffectsVignetteInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.vignetteStrength,
            grainStrength: DOM.stageEffectsGrainInput
                ? DOM.stageEffectsGrainInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.grainStrength,
            particleDensity: DOM.stageEffectsParticleDensityInput
                ? DOM.stageEffectsParticleDensityInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.particleDensity,
            particleOpacity: DOM.stageEffectsParticleOpacityInput
                ? DOM.stageEffectsParticleOpacityInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.particleOpacity,
            particleSize: DOM.stageEffectsParticleSizeInput
                ? DOM.stageEffectsParticleSizeInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.particleSize,
            particleSpeed: DOM.stageEffectsParticleSpeedInput
                ? DOM.stageEffectsParticleSpeedInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.particleSpeed,
            hue: DOM.stageEffectsHueInput
                ? DOM.stageEffectsHueInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.hue,
            saturation: DOM.stageEffectsSaturationInput
                ? DOM.stageEffectsSaturationInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.saturation,
            contrast: DOM.stageEffectsContrastInput
                ? DOM.stageEffectsContrastInput.value
                : DEFAULT_STAGE_EFFECT_SETTINGS.contrast,
        });
    }

    function handleStageEffectsInput() {
        applyStageEffectsSettings(readStageEffectsSettingsFromInputs());
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
