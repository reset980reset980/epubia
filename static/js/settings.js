(function () {
  "use strict";

  const form = document.querySelector("[data-appearance-form]");
  const preview = document.querySelector("[data-appearance-preview]");
  if (!form || !preview) return;

  form.querySelectorAll("[data-site-field]").forEach((field) => {
    field.addEventListener("input", () => {
      const key = field.dataset.siteField;
      if (key === "accent_color") {
        preview.style.setProperty("--preview-accent", field.value);
        return;
      }
      if (key === "primary_color") {
        preview.style.setProperty("--preview-primary", field.value);
        return;
      }
      preview.querySelectorAll(`[data-preview="${key}"]`).forEach((target) => {
        const emptyValue = field.dataset.allowEmpty === "true" ? "" : field.dataset.previewFallback || "미리보기";
        target.textContent = field.value.trim() || emptyValue;
      });
    });
  });
})();
