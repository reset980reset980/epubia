(function () {
  "use strict";

  const coverImages = document.querySelectorAll("[data-cover-image]");

  function markMissingCover(image) {
    const frame = image.closest(".cover-frame");
    if (frame) frame.classList.add("is-missing");
    image.setAttribute("aria-hidden", "true");
  }

  coverImages.forEach((image) => {
    image.addEventListener("error", () => markMissingCover(image), { once: true });
    if (image.complete && image.naturalWidth === 0) markMissingCover(image);
  });

  const publishForm = document.querySelector("[data-publish-form]");
  const fileInput = publishForm && publishForm.querySelector("[data-file-input]");
  const fileName = publishForm && publishForm.querySelector("[data-file-name]");

  if (publishForm && fileInput && fileName) {
    const defaultFileCopy = fileName.textContent;
    const uploadBox = fileInput.closest(".upload-box");
    const maxUploadBytes = Number(publishForm.dataset.uploadMaxBytes) || 100 * 1024 * 1024;
    const uploadMb = Number(publishForm.dataset.uploadMb) || Math.round(maxUploadBytes / 1024 / 1024);
    const studio = publishForm.querySelector("[data-cover-studio]");
    const draftTokenInput = publishForm.querySelector("[data-draft-token]");
    const coverTokenInput = publishForm.querySelector("[data-cover-token]");
    const preview = publishForm.querySelector("[data-cover-preview]");
    const previewImage = publishForm.querySelector("[data-cover-preview-image]");
    const previewPlaceholder = publishForm.querySelector("[data-cover-placeholder]");
    const previewBadge = publishForm.querySelector("[data-cover-badge]");
    const loading = publishForm.querySelector("[data-cover-loading]");
    const staleNote = publishForm.querySelector("[data-cover-stale]");
    const generateButton = publishForm.querySelector("[data-cover-generate]");
    const regenerateButton = publishForm.querySelector("[data-cover-regenerate]");
    const status = publishForm.querySelector("[data-cover-status]");
    const publishButton = publishForm.querySelector("[data-publish-submit]");
    const publishButtonLabel = publishForm.querySelector("[data-publish-submit-label]");
    const publishingProgress = publishForm.querySelector("[data-publishing-progress]");
    const defaultPublishButtonLabel = publishButtonLabel ? publishButtonLabel.textContent : "출판본 만들기";
    const aiEnabled = publishForm.dataset.aiCoverEnabled === "1";
    const aiAvailable = publishForm.dataset.aiCoverAvailable === "1";
    const canGenerateCover = aiEnabled && aiAvailable;
    const metadataNames = ["title", "author", "subtitle", "publisher", "description"];
    const metadataInputs = metadataNames
      .map((name) => publishForm.elements.namedItem(name))
      .filter(Boolean);
    const allowedFileExtensions = new Set(["pdf", "txt", "md", "markdown", "zip"]);
    let coverRequestBusy = false;
    let publishRequestBusy = false;
    let dragDepth = 0;

    function formatMegabytes(bytes) {
      return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
    }

    function setCoverStatus(message, state) {
      if (!status) return;
      status.textContent = message;
      status.classList.remove("is-error", "is-success", "is-stale");
      if (state) status.classList.add(`is-${state}`);
    }

    function setBusy(isBusy) {
      coverRequestBusy = isBusy;
      if (studio) {
        studio.classList.toggle("is-busy", isBusy);
        studio.setAttribute("aria-busy", String(isBusy));
      }
      if (loading) loading.hidden = !isBusy;
      if (generateButton) generateButton.disabled = isBusy || !canGenerateCover;
      if (regenerateButton) regenerateButton.disabled = isBusy || !canGenerateCover;
    }

    function setPublishingBusy(isBusy) {
      publishRequestBusy = isBusy;
      publishForm.classList.toggle("is-publishing", isBusy);
      publishForm.setAttribute("aria-busy", String(isBusy));
      if (publishButton) publishButton.disabled = isBusy;
      if (publishButtonLabel) {
        publishButtonLabel.textContent = isBusy ? "출판 중…" : defaultPublishButtonLabel;
      }
      if (publishingProgress) publishingProgress.hidden = !isBusy;
    }

    function hasOversizedFile(file) {
      return Boolean(file && file.size > maxUploadBytes);
    }

    function hasAllowedExtension(file) {
      const name = file && typeof file.name === "string" ? file.name : "";
      const extension = name.includes(".") ? name.split(".").pop().toLocaleLowerCase("en-US") : "";
      return allowedFileExtensions.has(extension);
    }

    function hasDraggedFiles(event) {
      const types = event.dataTransfer && event.dataTransfer.types;
      return Boolean(types && Array.from(types).includes("Files"));
    }

    function clearDragState() {
      dragDepth = 0;
      if (uploadBox) uploadBox.classList.remove("is-dragover");
    }

    function resetCoverDraft() {
      if (draftTokenInput) draftTokenInput.value = "";
      if (coverTokenInput) coverTokenInput.value = "";
      if (preview) preview.classList.remove("has-cover", "is-stale");
      if (previewImage) {
        previewImage.hidden = true;
        previewImage.removeAttribute("src");
        previewImage.alt = "";
      }
      if (previewPlaceholder) previewPlaceholder.hidden = false;
      if (previewBadge) previewBadge.hidden = true;
      if (staleNote) staleNote.hidden = true;
      if (generateButton) generateButton.hidden = false;
      if (regenerateButton) {
        regenerateButton.hidden = true;
        regenerateButton.textContent = "다른 표지 만들기";
      }
    }

    function validateSelectedFile(file) {
      if (file && !hasAllowedExtension(file)) {
        fileInput.value = "";
        fileName.textContent = "지원하지 않는 파일입니다 · PDF, TXT, MD, ZIP만 가능";
        if (uploadBox) uploadBox.classList.remove("has-file");
        setCoverStatus("PDF, TXT, Markdown 또는 index.html이 포함된 HTML ZIP 파일을 선택해 주세요.", "error");
        return false;
      }
      if (!hasOversizedFile(file)) return true;
      fileInput.value = "";
      fileName.textContent = `파일이 너무 큽니다 · 최대 ${uploadMb}MB`;
      if (uploadBox) uploadBox.classList.remove("has-file");
      setCoverStatus(
        `선택한 파일은 ${formatMegabytes(file.size)}입니다. 최대 ${uploadMb}MB 이하의 원고를 선택해 주세요.`,
        "error"
      );
      return false;
    }

    function markCoverStale() {
      if (!coverTokenInput || !coverTokenInput.value) return;
      coverTokenInput.value = "";
      if (preview) preview.classList.add("is-stale");
      if (staleNote) staleNote.hidden = false;
      if (regenerateButton) {
        regenerateButton.hidden = false;
        regenerateButton.textContent = "변경 내용으로 다시 만들기";
      }
      setCoverStatus("책 정보가 변경되었습니다. 최신 내용에 맞게 표지를 다시 만들어 주세요.", "stale");
    }

    function appendMetadata(formData) {
      metadataNames.forEach((name) => {
        const field = publishForm.elements.namedItem(name);
        formData.append(name, field ? field.value : "");
      });
    }

    function cacheBustedUrl(url) {
      const separator = url.includes("?") ? "&" : "?";
      return `${url}${separator}v=${Date.now()}`;
    }

    async function generateCover() {
      if (!canGenerateCover || coverRequestBusy) return;

      const file = fileInput.files && fileInput.files[0];
      const existingDraft = draftTokenInput && draftTokenInput.value;
      if (!existingDraft && !file) {
        setCoverStatus("먼저 PDF, TXT, Markdown 또는 HTML ZIP 파일을 선택해 주세요.", "error");
        fileInput.focus();
        return;
      }
      if (file && !validateSelectedFile(file)) return;

      const formData = new FormData();
      const csrfField = publishForm.elements.namedItem("csrf_token");
      formData.append("csrf_token", csrfField ? csrfField.value : "");
      if (existingDraft) {
        formData.append("draft_token", existingDraft);
      } else {
        formData.append("source", file, file.name);
      }
      appendMetadata(formData);

      setBusy(true);
      setCoverStatus(
        existingDraft ? "책의 분위기를 다시 해석해 새로운 표지를 만들고 있습니다." : "책 소개 또는 원고 앞부분을 참고해 표지를 만들고 있습니다."
      );

      try {
        const response = await fetch("/cover-drafts", {
          method: "POST",
          body: formData,
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        let payload = {};
        try {
          payload = await response.json();
        } catch (_error) {
          payload = {};
        }
        if (!response.ok) {
          const fallback = response.status === 413
            ? `파일 용량이 너무 큽니다. 최대 ${uploadMb}MB까지 업로드할 수 있습니다.`
            : "AI 표지를 만들지 못했습니다. 잠시 후 다시 시도해 주세요.";
          throw new Error(payload.error || payload.message || fallback);
        }
        if (!payload.draft_token || !payload.cover_token || !payload.cover_url) {
          throw new Error("표지 생성 결과가 올바르지 않습니다. 다시 시도해 주세요.");
        }

        if (draftTokenInput) draftTokenInput.value = payload.draft_token;
        if (coverTokenInput) coverTokenInput.value = payload.cover_token;

        const titleField = publishForm.elements.namedItem("title");
        if (titleField && !titleField.value.trim() && payload.title) titleField.value = payload.title;

        if (previewImage) {
          previewImage.onload = () => {
            if (preview) preview.classList.add("has-cover");
          };
          previewImage.onerror = () => {
            setCoverStatus("표지는 생성됐지만 미리보기를 불러오지 못했습니다. 출판은 계속할 수 있습니다.", "error");
          };
          previewImage.alt = `${payload.title || titleField && titleField.value || "전자책"} AI 생성 표지 미리보기`;
          previewImage.src = cacheBustedUrl(payload.cover_url);
          previewImage.hidden = false;
        }
        if (preview) preview.classList.remove("is-stale");
        if (previewPlaceholder) previewPlaceholder.hidden = true;
        if (previewBadge) {
          previewBadge.textContent = payload.mode === "ai" ? "AI 생성 표지" : "한글 안전 표지";
          previewBadge.hidden = false;
        }
        if (staleNote) staleNote.hidden = true;
        if (generateButton) generateButton.hidden = true;
        if (regenerateButton) {
          regenerateButton.hidden = false;
          regenerateButton.textContent = "다른 표지 만들기";
        }
        setCoverStatus(payload.message || "AI 표지가 완성되었습니다. 이 표지는 서재 썸네일로 함께 사용됩니다.", "success");
      } catch (error) {
        setCoverStatus(error instanceof Error ? error.message : "AI 표지를 만들지 못했습니다.", "error");
      } finally {
        setBusy(false);
      }
    }

    fileInput.addEventListener("change", () => {
      const file = fileInput.files && fileInput.files[0];
      resetCoverDraft();
      fileName.textContent = file ? `${file.name} · ${formatMegabytes(file.size)}` : defaultFileCopy;
      if (uploadBox) uploadBox.classList.toggle("has-file", Boolean(file));
      if (!file) return;
      if (!validateSelectedFile(file)) return;
      if (canGenerateCover) {
        setCoverStatus("원고가 준비되었습니다. AI 표지 자동 생성을 눌러 내용을 분석해 보세요.");
      }
    });

    if (uploadBox) {
      uploadBox.addEventListener("dragenter", (event) => {
        if (!hasDraggedFiles(event)) return;
        event.preventDefault();
        dragDepth += 1;
        uploadBox.classList.add("is-dragover");
      });

      uploadBox.addEventListener("dragover", (event) => {
        if (!hasDraggedFiles(event)) return;
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
        uploadBox.classList.add("is-dragover");
      });

      uploadBox.addEventListener("dragleave", (event) => {
        if (!hasDraggedFiles(event)) return;
        dragDepth = Math.max(0, dragDepth - 1);
        if (dragDepth === 0) uploadBox.classList.remove("is-dragover");
      });

      uploadBox.addEventListener("drop", (event) => {
        if (!hasDraggedFiles(event)) return;
        event.preventDefault();
        clearDragState();
        const files = event.dataTransfer && Array.from(event.dataTransfer.files || []);
        if (!files || files.length === 0) return;
        if (files.length > 1) {
          setCoverStatus("한 번에 원고 파일 하나만 끌어 놓아 주세요.", "error");
          return;
        }

        try {
          const transfer = new DataTransfer();
          transfer.items.add(files[0]);
          fileInput.files = transfer.files;
          fileInput.dispatchEvent(new Event("change", { bubbles: true }));
        } catch (_error) {
          setCoverStatus("이 브라우저에서는 드래그한 파일을 선택하지 못했습니다. 파일 찾기를 이용해 주세요.", "error");
        }
      });

      window.addEventListener("dragover", (event) => {
        if (hasDraggedFiles(event)) event.preventDefault();
      });
      window.addEventListener("drop", (event) => {
        if (hasDraggedFiles(event)) event.preventDefault();
        clearDragState();
      });
      window.addEventListener("blur", clearDragState);
    }

    metadataInputs.forEach((field) => field.addEventListener("input", markCoverStale));
    if (generateButton) generateButton.addEventListener("click", generateCover);
    if (regenerateButton) regenerateButton.addEventListener("click", generateCover);

    publishForm.addEventListener("submit", (event) => {
      if (publishRequestBusy) {
        event.preventDefault();
        return;
      }
      if (coverRequestBusy) {
        event.preventDefault();
        setCoverStatus("표지를 만드는 중입니다. 완료된 뒤 출판해 주세요.", "error");
        return;
      }
      const file = fileInput.files && fileInput.files[0];
      if (file && !validateSelectedFile(file)) {
        event.preventDefault();
        return;
      }
      const hasDraft = Boolean(draftTokenInput && draftTokenInput.value);
      if (!file && !hasDraft) {
        event.preventDefault();
        fileInput.reportValidity();
        fileInput.focus();
        return;
      }
      if (hasDraft) {
        fileInput.required = false;
        fileInput.disabled = true;
      }
      clearDragState();
      setPublishingBusy(true);
    });

    window.addEventListener("pageshow", () => {
      setPublishingBusy(false);
      fileInput.disabled = false;
      fileInput.required = true;
    });
  }

  const library = document.querySelector("[data-library]");
  if (!library) return;

  const search = library.querySelector("[data-library-search]");
  const cards = Array.from(library.querySelectorAll("[data-book-card]"));
  const count = library.querySelector("[data-library-count]");
  const noResults = library.querySelector("[data-no-results]");

  if (!search || !cards.length) return;

  function normalize(value) {
    return value.trim().toLocaleLowerCase("ko-KR").replace(/\s+/g, " ");
  }

  function filterLibrary() {
    const query = normalize(search.value);
    let visible = 0;

    cards.forEach((card) => {
      const matches = !query || normalize(card.dataset.search || "").includes(query);
      card.hidden = !matches;
      if (matches) visible += 1;
    });

    if (count) count.textContent = String(visible);
    if (noResults) noResults.hidden = visible !== 0;
  }

  search.addEventListener("input", filterLibrary);
})();
