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
})();
