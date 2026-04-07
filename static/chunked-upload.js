(function () {
    function formatBytes(bytes) {
        if (!bytes) {
            return "0 Б";
        }

        const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
        let value = bytes;
        let unitIndex = 0;

        while (value >= 1024 && unitIndex < units.length - 1) {
            value /= 1024;
            unitIndex += 1;
        }

        const digits = unitIndex === 0 ? 0 : value >= 10 ? 1 : 2;
        return `${value.toFixed(digits)} ${units[unitIndex]}`;
    }

    function formatSpeed(bytesPerSecond) {
        return `${formatBytes(bytesPerSecond)}/с`;
    }

    function buildUploadId() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return window.crypto.randomUUID();
        }

        return `upload-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function readJson(response) {
        return response.text().then((body) => {
            if (!body) {
                return {};
            }

            try {
                return JSON.parse(body);
            } catch (error) {
                return {};
            }
        });
    }

    async function sendChunk(chunkUrl, payload) {
        const response = await fetch(chunkUrl, {
            method: "POST",
            body: payload,
            credentials: "same-origin",
        });
        const data = await readJson(response);

        if (!response.ok || data.ok === false) {
            const message = data.error || `Сервер вернул код ${response.status}.`;
            throw new Error(message);
        }

        return data;
    }

    window.setupChunkedUpload = function setupChunkedUpload(uploadForm, options = {}) {
        if (!uploadForm) {
            return;
        }

        const fileInput = uploadForm.querySelector("[data-upload-input]");
        const fileLabel = uploadForm.querySelector(".upload-label");
        const uploadButton = uploadForm.querySelector('button[type="submit"]');
        const uploadProgress = uploadForm.querySelector(".upload-progress");
        const uploadStatus = uploadForm.querySelector(".upload-progress-status");
        const uploadPercent = uploadForm.querySelector(".upload-progress-percent");
        const uploadFill = uploadForm.querySelector(".upload-progress-fill");
        const uploadMeta = uploadForm.querySelector(".upload-progress-meta");
        const currentPathInput = uploadForm.querySelector('input[name="current_path"]');

        if (!fileInput || !uploadButton) {
            return;
        }

        const chunkUrl = options.chunkUrl || uploadForm.dataset.chunkUrl || uploadForm.action;
        const reloadUrl = options.reloadUrl || uploadForm.dataset.reloadUrl || window.location.href;
        const chunkSize = options.chunkSize || Number(uploadForm.dataset.chunkSize || 16 * 1024 * 1024);
        const defaultButtonText = uploadButton.textContent;

        function setUploadState(percent, statusText, metaText) {
            if (!uploadProgress || !uploadStatus || !uploadPercent || !uploadFill || !uploadMeta) {
                return;
            }

            uploadProgress.hidden = false;
            uploadStatus.textContent = statusText;
            uploadPercent.textContent = `${percent}%`;
            uploadFill.style.width = `${percent}%`;
            uploadMeta.textContent = metaText;
        }

        function resetInteractiveState() {
            uploadButton.disabled = false;
            uploadButton.textContent = defaultButtonText;
            fileInput.disabled = false;
        }

        fileInput.addEventListener("change", () => {
            const files = Array.from(fileInput.files || []);
            fileLabel.textContent = files.length ? `Выбрано файлов: ${files.length}` : "Выбрать файлы";

            if (!files.length) {
                if (uploadProgress) {
                    uploadProgress.hidden = true;
                }
                return;
            }

            const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
            setUploadState(0, "Файлы готовы", `${files.length} шт. • ${formatBytes(totalBytes)}`);
        });

        uploadForm.addEventListener("submit", async (event) => {
            const files = Array.from(fileInput.files || []);
            if (!files.length) {
                return;
            }

            event.preventDefault();
            uploadButton.disabled = true;
            uploadButton.textContent = "Загрузка...";
            fileInput.disabled = true;

            const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
            const startedAt = performance.now();
            let uploadedOverall = 0;

            try {
                for (let fileIndex = 0; fileIndex < files.length; fileIndex += 1) {
                    const file = files[fileIndex];
                    const uploadId = buildUploadId();
                    const totalChunks = Math.max(1, Math.ceil(file.size / chunkSize));

                    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
                        const chunkStart = chunkIndex * chunkSize;
                        const chunkEnd = Math.min(file.size, chunkStart + chunkSize);
                        const chunkBlob = file.slice(chunkStart, chunkEnd);
                        const formData = new FormData();

                        formData.append("chunk", chunkBlob, file.name);
                        formData.append("filename", file.name);
                        formData.append("upload_id", uploadId);
                        formData.append("chunk_index", String(chunkIndex));
                        formData.append("total_chunks", String(totalChunks));
                        formData.append("chunk_start", String(chunkStart));
                        formData.append("total_size", String(file.size));
                        if (currentPathInput) {
                            formData.append("current_path", currentPathInput.value || "");
                        }

                        await sendChunk(chunkUrl, formData);

                        const fileUploaded = chunkEnd;
                        const uploadedBytes = uploadedOverall + fileUploaded;
                        const elapsedSeconds = Math.max((performance.now() - startedAt) / 1000, 0.2);
                        const percent = totalBytes ? Math.min(100, Math.round((uploadedBytes / totalBytes) * 100)) : 100;
                        const speed = uploadedBytes / elapsedSeconds;
                        const statusText = files.length > 1
                            ? `Файл ${fileIndex + 1} из ${files.length}`
                            : "Идет загрузка";
                        const metaText = `${formatBytes(uploadedBytes)} из ${formatBytes(totalBytes)} • ${formatSpeed(speed)}`;
                        setUploadState(percent, statusText, metaText);
                    }

                    uploadedOverall += file.size;
                }

                setUploadState(100, "Загрузка завершена", `Отправлено файлов: ${files.length}.`);
                window.setTimeout(() => {
                    window.location.href = reloadUrl;
                }, 250);
            } catch (error) {
                setUploadState(0, "Ошибка загрузки", error.message || "Не удалось передать файлы на сервер.");
                resetInteractiveState();
            }
        });
    };
})();
