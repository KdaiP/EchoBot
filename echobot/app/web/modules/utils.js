export function formatTimestamp(value) {
    const text = String(value || "").trim();
    if (!text) {
        return "";
    }

    const parsed = new Date(text);
    if (Number.isNaN(parsed.getTime())) {
        return text;
    }
    return parsed.toLocaleString("zh-CN", {
        hour12: false,
    });
}

export function roundTo(value, digits) {
    const power = 10 ** digits;
    return Math.round(value * power) / power;
}

export function smoothValue(previous, next, strength) {
    return previous + (next - previous) * strength;
}

export function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
}

export function delay(milliseconds) {
    return new Promise((resolve) => {
        window.setTimeout(resolve, milliseconds);
    });
}
