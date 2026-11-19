document.addEventListener("DOMContentLoaded", () => {
    const color = document.body.dataset.appColor || "";
    if (window.CSS && CSS.supports("color", color)) {
        document.documentElement.style.setProperty("--osw-accent", color);
    }
});
