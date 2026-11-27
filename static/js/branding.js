document.addEventListener("DOMContentLoaded", () => {
    const root = document.documentElement;
    const applyBrandingColor = color => {
    // Persisted branding is a hex color. Invalid or empty transient values
    // must reset computed accents instead of leaving the previous theme stale.
    const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(color || ""));
    if (!hex) color = "#ff7043";
    const selected = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color);
    root.style.setProperty("--osw-brand-chosen", color);

    // Preserve the saved swatch exactly; accessible action/text shades differ.
    const value = selected[1].length === 3
        ? selected[1].split("").map(part => part + part).join("") : selected[1];
    const rgb = [0, 2, 4].map(index => parseInt(value.slice(index, index + 2), 16));
    const luminance = channels => channels.reduce((sum, channel, index) => {
        const scaled = channel / 255;
        const linear = scaled <= 0.04045 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
        return sum + linear * [0.2126, 0.7152, 0.0722][index];
    }, 0);
    const contrast = (a, b) => {
        const x = luminance(a), y = luminance(b);
        return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
    };
    const toHex = channels => "#" + channels.map(channel =>
        Math.round(channel).toString(16).padStart(2, "0")).join("");
    const shadeFor = (background, direction) => {
        for (let step = 0; step <= 100; step++) {
            const ratio = step / 100;
            const candidate = rgb.map(channel => channel * (1 - ratio) + direction * ratio);
            if (contrast(candidate, background) >= 4.5) return toHex(candidate);
        }
        return toHex([direction, direction, direction]);
    };
    const refresh = () => {
        // All non-button UI colors are fixed theme tokens, never brand-derived.
        const actionColor = shadeFor([255, 255, 255], 0);
        root.style.setProperty("--osw-action-accent", actionColor);
        const action = [1, 3, 5].map(index => parseInt(actionColor.slice(index, index + 2), 16));
        root.style.setProperty("--osw-action-hover", toHex(action.map(channel => channel * 0.84)));
        // Link colors are deliberately independent of the selected button color.
    };
    refresh();
    };
    applyBrandingColor(document.body.dataset.appColor || "");
    window.addEventListener("opensigweave-theme-change", () => {
        applyBrandingColor(document.body.dataset.appColor || "");
    });
    window.addEventListener("opensigweave-branding-change", event => {
        document.body.dataset.appColor = String(event.detail?.app_color || "");
        applyBrandingColor(document.body.dataset.appColor);
    });
});
