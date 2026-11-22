(function () {
    "use strict";
    const storageKey = "opensigweave.theme.v1";
    const root = document.documentElement;
    let theme = "dark";
    let brandAssetVersion = "";
    try {
        if (window.localStorage.getItem(storageKey) === "light") theme = "light";
    } catch (_error) { /* Blocked storage: fall back to dark. */ }
    function applyTheme(value, persist) {
        theme = value === "light" ? "light" : "dark";
        root.dataset.theme = theme;
        root.classList.toggle("dark", theme === "dark");
        if (persist) {
            try { window.localStorage.setItem(storageKey, theme); }
            catch (_error) { /* Keep the current appearance without storage. */ }
        }
        const button = document.getElementById("theme-toggle");
        if (button) {
            const target = theme === "dark" ? "light" : "dark";
            button.textContent = target === "light" ? "Light mode" : "Dark mode";
            button.setAttribute("aria-label", "Switch to " + target + " mode");
            button.setAttribute("aria-pressed", String(theme === "light"));
        }
        const selectedLogo = (theme === "dark" ? "/branding/logo-dark.png" : "/branding/logo.png")
            + (brandAssetVersion ? "?v=" + encodeURIComponent(brandAssetVersion) : "");
        document.querySelectorAll("[data-osw-brand-logo]").forEach(logo => {
            if (logo.getAttribute("src") !== selectedLogo) logo.setAttribute("src", selectedLogo);
        });
        window.dispatchEvent(new CustomEvent("opensigweave-theme-change", { detail: { theme } }));
    }
    applyTheme(theme, false);
    window.addEventListener("opensigweave-branding-change", event => {
        brandAssetVersion = String(event.detail?.version || Date.now());
        applyTheme(theme, false);
    });
    document.addEventListener("DOMContentLoaded", () => {
        applyTheme(theme, false);
        const button = document.getElementById("theme-toggle");
        if (button) button.addEventListener("click", () =>
            applyTheme(theme === "dark" ? "light" : "dark", true));
    });
})();
