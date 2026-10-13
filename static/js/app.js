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

/* Confirm dialog, busy buttons, drop zone. */
(function () {
    "use strict";

    var pendingForm = null;

    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement)) { return; }
        var modalEl = document.getElementById("confirm-modal");
        if (form.dataset.confirm && !form.dataset.confirmed && modalEl && window.bootstrap) {
            event.preventDefault();
            pendingForm = form;
            document.getElementById("confirm-modal-text").textContent = form.dataset.confirm;
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
            return;
        }
        if (form.dataset.busy) {
            if (form.dataset.busyActive) { event.preventDefault(); return; }
            form.dataset.busyActive = "1";
            var btn = form.querySelector('[type="submit"]');
            if (btn) {
                // Disable after the browser has collected the submitter's value.
                setTimeout(function () {
                    btn.disabled = true;
                    btn.textContent = "";
                    var spinner = document.createElement("span");
                    spinner.className = "spinner-border spinner-border-sm me-2";
                    spinner.setAttribute("aria-hidden", "true");
                    btn.appendChild(spinner);
                    btn.appendChild(document.createTextNode(form.dataset.busy));
                }, 0);
            }
        }
    });

    document.addEventListener("click", function (event) {
        if (!event.target.closest("#confirm-modal-ok") || !pendingForm) { return; }
        var form = pendingForm;
        pendingForm = null;
        form.dataset.confirmed = "1";
        window.bootstrap.Modal.getInstance(document.getElementById("confirm-modal")).hide();
        if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
    });

    document.addEventListener("DOMContentLoaded", function () {
        var target = document.querySelector("[data-autofocus]");
        if (target) { target.focus(); }

        document.querySelectorAll(".drop-zone").forEach(function (zone) {
            var input = zone.querySelector('input[type="file"]');
            if (!input) { return; }
            var label = zone.querySelector(".drop-zone-name");
            if (!label) {
                label = document.createElement("span");
                label.className = "drop-zone-name";
                zone.appendChild(label);
            }
            function showName() {
                label.textContent = input.files && input.files.length ? input.files[0].name : "";
            }
            ["dragenter", "dragover"].forEach(function (name) {
                zone.addEventListener(name, function (e) { e.preventDefault(); zone.classList.add("is-dragover"); });
            });
            ["dragleave", "drop"].forEach(function (name) {
                zone.addEventListener(name, function () { zone.classList.remove("is-dragover"); });
            });
            zone.addEventListener("drop", function (e) {
                e.preventDefault();
                if (e.dataTransfer && e.dataTransfer.files.length) {
                    input.files = e.dataTransfer.files;
                    showName();
                }
            });
            input.addEventListener("change", showName);
        });
    });
})();
