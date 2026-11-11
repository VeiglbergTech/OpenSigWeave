const csrfToken = document.body.dataset.csrfToken || "";
const logoutButton = document.getElementById("logout-button");

if (logoutButton) {
    logoutButton.addEventListener("click", async () => {
        try {
            const response = await fetch("/logout", {
                method: "POST",
                headers: { "X-CSRF-Token": csrfToken },
            });
            if (!response.ok) return;
            const result = await response.json();
            if (result.logout_url) window.location.assign(result.logout_url);
        } catch {
            window.location.assign("/login");
        }
    });
}
