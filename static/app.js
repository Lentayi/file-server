(function () {
    function qs(selector, root) {
        return (root || document).querySelector(selector);
    }

    function qsa(selector, root) {
        return Array.from((root || document).querySelectorAll(selector));
    }

    function setTheme(theme) {
        document.body.dataset.theme = theme;
        localStorage.setItem("file-server-login-theme", theme);
        qsa("[data-theme-choice]").forEach((button) => {
            button.classList.toggle("active", button.dataset.themeChoice === theme);
        });
    }

    qsa("[data-theme-choice]").forEach((button) => {
        button.addEventListener("click", () => setTheme(button.dataset.themeChoice || "dark"));
    });

    const savedTheme = localStorage.getItem("file-server-login-theme");
    if (document.body.dataset.loginTheme === "true" && savedTheme) {
        setTheme(savedTheme);
    }

    const logoutToggle = qs("[data-logout-toggle]");
    const logoutMenu = qs("[data-logout-menu]");
    const logoutCancel = qs("[data-logout-cancel]");

    function closeLogoutMenu() {
        if (!logoutToggle || !logoutMenu) {
            return;
        }

        logoutMenu.hidden = true;
        logoutToggle.setAttribute("aria-expanded", "false");
    }

    if (logoutToggle && logoutMenu) {
        logoutToggle.addEventListener("click", (event) => {
            event.preventDefault();
            const shouldOpen = logoutMenu.hidden;
            logoutMenu.hidden = !shouldOpen;
            logoutToggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
        });

        if (logoutCancel) {
            logoutCancel.addEventListener("click", closeLogoutMenu);
        }

        document.addEventListener("click", (event) => {
            if (!logoutMenu.hidden && !event.target.closest(".logout-menu-wrap")) {
                closeLogoutMenu();
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                closeLogoutMenu();
            }
        });
    }

    qsa("[data-select-form]").forEach((form) => {
        const selectAll = qs("[data-select-all]", form);
        const checkboxes = qsa(".entry-checkbox", form);
        const counter = document.getElementById(form.dataset.counter || "");

        function updateCounter() {
            const selected = checkboxes.filter((checkbox) => checkbox.checked).length;
            if (counter) {
                counter.textContent = `Выбрано: ${selected}`;
            }
            if (selectAll) {
                selectAll.checked = selected > 0 && selected === checkboxes.length;
            }
        }

        if (selectAll) {
            selectAll.addEventListener("change", () => {
                checkboxes.forEach((checkbox) => {
                    checkbox.checked = selectAll.checked;
                });
                updateCounter();
            });
        }

        checkboxes.forEach((checkbox) => checkbox.addEventListener("change", updateCounter));
        updateCounter();
    });

    qsa("[data-avatar-upload]").forEach((form) => {
        const input = qs("[data-upload-input]", form);
        const progress = qs(".upload-progress", form);
        const status = qs(".upload-progress-status", form);
        const percent = qs(".upload-progress-percent", form);
        const fill = qs(".upload-progress-fill", form);
        const meta = qs(".upload-progress-meta", form);

        if (!input || !progress || !status || !percent || !fill || !meta) {
            return;
        }

        input.addEventListener("change", () => {
            const file = input.files && input.files[0];
            if (!file) {
                progress.hidden = true;
                return;
            }
            progress.hidden = false;
            status.textContent = "Файл готов";
            percent.textContent = "100%";
            fill.style.width = "100%";
            meta.textContent = file.name;
        });
    });

    const targetId = new URL(window.location.href).searchParams.get("return_to");
    if (targetId) {
        const target = document.getElementById(targetId);
        if (target) {
            requestAnimationFrame(() => target.scrollIntoView({ behavior: "smooth", block: "center" }));
        }
    }

    const ramBufferStatus = qs("[data-ram-buffer-status]");

    function formatBytes(bytes) {
        const value = Number(bytes || 0);
        const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
        let size = value;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex += 1;
        }
        const digits = size >= 10 || unitIndex === 0 ? 0 : 1;
        return `${size.toFixed(digits)} ${units[unitIndex]}`;
    }

    function formatDuration(seconds) {
        const total = Math.max(0, Math.round(Number(seconds || 0)));
        if (!total) {
            return "Очередь пуста";
        }
        const minutes = Math.floor(total / 60);
        const rest = total % 60;
        if (minutes <= 0) {
            return `${rest} сек до диска`;
        }
        return `${minutes} мин ${rest} сек до диска`;
    }

    if (ramBufferStatus) {
        const fill = qs("[data-ram-buffer-fill]", ramBufferStatus);
        const used = qs("[data-ram-buffer-used]", ramBufferStatus);
        const speed = qs("[data-ram-buffer-speed]", ramBufferStatus);
        const eta = qs("[data-ram-buffer-eta]", ramBufferStatus);
        const statusUrl = ramBufferStatus.dataset.statusUrl;

        function renderRamBuffer(status) {
            const percent = Math.max(0, Math.min(100, Number(status.percent || 0)));
            if (fill) {
                fill.style.width = `${percent}%`;
            }
            if (used) {
                used.textContent = `${formatBytes(status.used_bytes)} занято`;
            }
            if (speed) {
                speed.textContent = `${formatBytes(status.speed_bytes_per_sec)}/с на диск`;
            }
            if (eta) {
                eta.textContent = status.flushing ? formatDuration(status.eta_seconds) : "Очередь пуста";
            }
            ramBufferStatus.hidden = status.enabled === false;
        }

        async function refreshRamBuffer() {
            if (!statusUrl) {
                return;
            }
            try {
                const response = await fetch(statusUrl, { headers: { Accept: "application/json" } });
                if (!response.ok) {
                    return;
                }
                renderRamBuffer(await response.json());
            } catch (error) {
                // The next interval will retry the lightweight status request.
            }
        }

        refreshRamBuffer();
        window.setInterval(refreshRamBuffer, 1000);
    }
})();
