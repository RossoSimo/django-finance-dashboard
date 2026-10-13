/* FinanceDash app shell: theme switching and toasts. */
(function () {
    "use strict";
    var KEY = "financedash-theme";
    var root = document.documentElement;

    function systemTheme() {
        return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }

    function setTheme(mode) {
        try {
            if (mode === "system") {
                localStorage.removeItem(KEY);
            } else {
                localStorage.setItem(KEY, mode);
            }
        } catch (e) { /* storage unavailable: theme applies to this page only */ }
        var resolved = mode === "system" ? systemTheme() : mode;
        root.setAttribute("data-bs-theme", resolved);
        document.dispatchEvent(new CustomEvent("fd:themechange", { detail: { mode: mode, theme: resolved } }));
    }

    window.FD = window.FD || {};
    window.FD.setTheme = setTheme;

    function syncToggles() {
        var dark = root.getAttribute("data-bs-theme") === "dark";
        document.querySelectorAll(".js-theme-toggle").forEach(function (btn) {
            var icon = btn.querySelector("i");
            if (icon) { icon.className = dark ? "bi bi-sun" : "bi bi-moon-stars"; }
            btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
        });
    }

    document.addEventListener("click", function (event) {
        var btn = event.target.closest(".js-theme-toggle");
        if (!btn) { return; }
        setTheme(root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark");
    });
    document.addEventListener("fd:themechange", syncToggles);

    document.addEventListener("DOMContentLoaded", function () {
        syncToggles();
        if (window.bootstrap) {
            // Error toasts carry data-bs-autohide="false" and stay until dismissed.
            document.querySelectorAll(".toast").forEach(function (el) {
                window.bootstrap.Toast.getOrCreateInstance(el, {
                    autohide: el.dataset.bsAutohide !== "false",
                    delay: 5000
                }).show();
            });
        }
    });
})();
