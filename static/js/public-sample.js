(() => {
  "use strict";

  const body = document.body;
  const toc = document.querySelector("#sample-toc");
  const trigger = document.querySelector("[data-toc-open]");
  const closers = document.querySelectorAll("[data-toc-close]");
  const scrim = document.querySelector(".toc-scrim");
  const progress = document.querySelector("[data-reading-progress]");
  const progressFill = progress?.querySelector("span") || progress;
  const links = [...document.querySelectorAll("[data-toc-link]")];
  const pageArticles = [...document.querySelectorAll("[data-reader-page]")];
  const flow = document.querySelector("[data-reader-flow]");
  const stageShell = document.querySelector("[data-reader-stage]");
  const stage = document.querySelector("[data-book-stage]");
  const leaves = [...document.querySelectorAll("[data-reader-leaf]")];
  const previousButtons = [...document.querySelectorAll("[data-reader-prev]")];
  const nextButtons = [...document.querySelectorAll("[data-reader-next]")];
  const modeToggle = document.querySelector("[data-reader-mode-toggle]");
  const modeLabel = document.querySelector("[data-mode-label]");
  const position = document.querySelector("[data-reader-position]");
  const counter = document.querySelector("[data-reader-counter]");
  const bookProgress = document.querySelector("[data-reader-progress]");
  const status = document.querySelector("[data-reader-status]");
  const turnHint = document.querySelector("[data-turn-hint]");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const state = {
    index: 0,
    bookMode: true,
    animating: false,
    turnTimer: 0,
    observer: null,
    drag: null,
  };

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function closeToc() {
    toc?.classList.remove("is-open");
    body.classList.remove("toc-open");
    trigger?.setAttribute("aria-expanded", "false");
    if (scrim) scrim.hidden = true;
  }

  function openToc() {
    toc?.classList.add("is-open");
    body.classList.add("toc-open");
    trigger?.setAttribute("aria-expanded", "true");
    if (scrim) scrim.hidden = false;
  }

  trigger?.addEventListener("click", openToc);
  closers.forEach((element) => element.addEventListener("click", closeToc));

  function leafLabel(index) {
    return leaves[index]?.dataset.readerLabel || `${index + 1}번째 페이지`;
  }

  function setStatus(message) {
    if (status) status.textContent = message;
  }

  function updateToc() {
    links.forEach((link) => {
      const target = Number.parseInt(link.dataset.readerTarget || "-1", 10);
      if (target === state.index) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
  }

  function updateControls() {
    const atStart = state.index <= 0;
    const atEnd = state.index >= leaves.length - 1;
    previousButtons.forEach((button) => { button.disabled = atStart; });
    nextButtons.forEach((button) => { button.disabled = atEnd; });
    if (position) position.textContent = leafLabel(state.index);
    if (counter) counter.textContent = `${state.index + 1} / ${leaves.length}`;
    const percent = leaves.length > 1 ? (state.index / (leaves.length - 1)) * 100 : 100;
    if (bookProgress) bookProgress.style.width = `${percent}%`;
    if (state.bookMode && progressFill) progressFill.style.width = `${percent}%`;
    updateToc();
  }

  function replaceLocation() {
    if (!state.bookMode) return;
    const hash = `leaf=${state.index + 1}`;
    if (window.location.hash.slice(1) !== hash) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${hash}`);
    }
  }

  function currentIndexFromLocation() {
    const leafMatch = window.location.hash.match(/^#?leaf=(\d+)$/);
    if (leafMatch) return clamp(Number.parseInt(leafMatch[1], 10) - 1, 0, leaves.length - 1);
    const pageMatch = window.location.hash.match(/^#?page-(\d+)$/);
    if (pageMatch) {
      const index = leaves.findIndex((leaf) => leaf.id === `page-${pageMatch[1]}`);
      if (index >= 0) return index;
    }
    return 0;
  }

  function markActive(index) {
    leaves.forEach((leaf, leafIndex) => {
      const active = leafIndex === index;
      leaf.classList.toggle("is-active", active);
      leaf.setAttribute("aria-hidden", String(!active));
      leaf.inert = !active;
      if (active) leaf.scrollTop = 0;
    });
  }

  function sanitizeClone(clone) {
    clone.classList.remove("is-active");
    clone.classList.add("turn-sheet");
    clone.removeAttribute("id");
    clone.setAttribute("aria-hidden", "true");
    clone.inert = true;
    clone.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));
    clone.querySelectorAll("a, button, input, textarea, select, summary, [tabindex]").forEach((element) => {
      element.setAttribute("tabindex", "-1");
      element.setAttribute("aria-hidden", "true");
    });
    return clone;
  }

  function removeTurnSheet() {
    window.clearTimeout(state.turnTimer);
    stage?.querySelector(".turn-sheet")?.remove();
    stage?.classList.remove("is-dragging");
    state.animating = false;
    state.drag = null;
  }

  function finishTurn(targetIndex, announce = true) {
    removeTurnSheet();
    state.index = clamp(targetIndex, 0, leaves.length - 1);
    markActive(state.index);
    updateControls();
    replaceLocation();
    if (announce) setStatus(`${leafLabel(state.index)}을 펼쳤습니다.`);
    turnHint?.classList.add("is-dismissed");
  }

  function animateTurn(targetIndex, direction) {
    if (!stage || state.animating || targetIndex === state.index || targetIndex < 0 || targetIndex >= leaves.length) return;
    const fromIndex = state.index;
    const source = direction === "forward" ? leaves[fromIndex] : leaves[targetIndex];
    const ghost = sanitizeClone(source.cloneNode(true));
    ghost.classList.add(direction === "forward" ? "is-forward" : "is-backward");
    ghost.style.setProperty("--turn-progress", "0");

    state.animating = true;
    if (direction === "forward") markActive(targetIndex);
    else markActive(fromIndex);
    stage.appendChild(ghost);

    if (reducedMotion.matches) {
      finishTurn(targetIndex);
      return;
    }

    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => ghost.style.setProperty("--turn-progress", "1"));
    });
    ghost.addEventListener("transitionend", () => finishTurn(targetIndex), { once: true });
    state.turnTimer = window.setTimeout(() => finishTurn(targetIndex), 850);
  }

  function goTo(index, options = {}) {
    const target = clamp(Number.parseInt(index, 10) || 0, 0, leaves.length - 1);
    if (!state.bookMode) {
      state.index = target;
      leaves[target]?.scrollIntoView({ behavior: reducedMotion.matches ? "auto" : "smooth", block: "start" });
      updateControls();
      return;
    }
    if (target === state.index) return;
    const direction = options.direction || (target > state.index ? "forward" : "backward");
    animateTurn(target, direction);
  }

  function updateScrollProgress() {
    if (state.bookMode || !progressFill) return;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    const value = max > 0 ? Math.min(100, (window.scrollY / max) * 100) : 0;
    progressFill.style.width = `${value}%`;
  }

  function observeFlow() {
    state.observer?.disconnect();
    if (state.bookMode || !("IntersectionObserver" in window)) return;
    state.observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      const index = leaves.indexOf(visible.target);
      if (index < 0) return;
      state.index = index;
      updateControls();
    }, { rootMargin: "-18% 0px -58%", threshold: [0, .2, .55] });
    leaves.forEach((leaf) => state.observer.observe(leaf));
  }

  function enterBookMode() {
    if (!stage || !stageShell || !flow || !leaves.length) return;
    removeTurnSheet();
    state.bookMode = true;
    leaves.forEach((leaf) => stage.appendChild(leaf));
    stage.appendChild(stage.querySelector(".book-spine") || document.createElement("span"));
    stageShell.hidden = false;
    body.classList.add("reader-book-mode");
    body.classList.remove("reader-scroll-mode");
    markActive(state.index);
    modeToggle?.setAttribute("aria-pressed", "true");
    modeToggle?.setAttribute("aria-label", "연속 스크롤 읽기로 전환");
    if (modeLabel) modeLabel.textContent = "연속 읽기";
    updateControls();
    replaceLocation();
    state.observer?.disconnect();
    setStatus(`${leafLabel(state.index)}. 책 넘김 방식으로 읽습니다.`);
  }

  function enterScrollMode() {
    if (!stageShell || !flow) return;
    removeTurnSheet();
    state.bookMode = false;
    leaves.forEach((leaf) => {
      leaf.classList.remove("is-active");
      leaf.removeAttribute("aria-hidden");
      leaf.inert = false;
      flow.appendChild(leaf);
    });
    stageShell.hidden = true;
    body.classList.remove("reader-book-mode");
    body.classList.add("reader-scroll-mode");
    modeToggle?.setAttribute("aria-pressed", "false");
    modeToggle?.setAttribute("aria-label", "책 넘김 읽기로 전환");
    if (modeLabel) modeLabel.textContent = "책 넘김";
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${leaves[state.index]?.id ? `#${leaves[state.index].id}` : ""}`);
    observeFlow();
    updateScrollProgress();
    setStatus("연속 스크롤 읽기로 전환했습니다.");
    window.requestAnimationFrame(() => leaves[state.index]?.scrollIntoView({ block: "start" }));
  }

  function toggleMode() {
    if (state.bookMode) enterScrollMode();
    else enterBookMode();
    closeToc();
  }

  function beginDrag(event) {
    if (!state.bookMode || state.animating || !stage || event.button > 0) return;
    const interactive = event.target instanceof Element && event.target.closest("a, button, input, textarea, select, summary, details[open]");
    if (interactive) return;
    const rect = stage.getBoundingClientRect();
    const localX = event.clientX - rect.left;
    const direction = localX >= rect.width * .5 ? "forward" : "backward";
    const targetIndex = state.index + (direction === "forward" ? 1 : -1);
    if (targetIndex < 0 || targetIndex >= leaves.length) return;
    state.drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      lastX: event.clientX,
      lastTime: performance.now(),
      velocity: 0,
      direction,
      targetIndex,
      progress: 0,
      ghost: null,
      started: false,
    };
    stage.setPointerCapture?.(event.pointerId);
  }

  function setupDragVisual(drag) {
    if (!stage) return;
    const source = drag.direction === "forward" ? leaves[state.index] : leaves[drag.targetIndex];
    const ghost = sanitizeClone(source.cloneNode(true));
    ghost.classList.add(drag.direction === "forward" ? "is-forward" : "is-backward");
    ghost.style.transition = "none";
    ghost.style.setProperty("--turn-progress", "0");
    if (drag.direction === "forward") markActive(drag.targetIndex);
    else markActive(state.index);
    stage.appendChild(ghost);
    stage.classList.add("is-dragging");
    drag.ghost = ghost;
    drag.started = true;
    state.animating = true;
  }

  function moveDrag(event) {
    const drag = state.drag;
    if (!drag || drag.pointerId !== event.pointerId || !stage) return;
    const deltaX = event.clientX - drag.startX;
    const deltaY = event.clientY - drag.startY;
    const intended = drag.direction === "forward" ? -deltaX : deltaX;
    if (!drag.started) {
      if (Math.abs(deltaY) > Math.abs(deltaX) * 1.2 && Math.abs(deltaY) > 10) {
        state.drag = null;
        return;
      }
      if (intended < 8) return;
      setupDragVisual(drag);
    }
    if (!drag.ghost) return;
    event.preventDefault();
    const now = performance.now();
    const dt = Math.max(1, now - drag.lastTime);
    const dx = event.clientX - drag.lastX;
    drag.velocity = (drag.direction === "forward" ? -dx : dx) / dt;
    drag.lastX = event.clientX;
    drag.lastTime = now;
    drag.progress = clamp(intended / (stage.clientWidth * .78), 0, 1);
    const eased = 1 - Math.pow(1 - drag.progress, 1.18);
    drag.ghost.style.setProperty("--turn-progress", String(eased));
  }

  function endDrag(event) {
    const drag = state.drag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    stage?.releasePointerCapture?.(event.pointerId);
    if (!drag.started || !drag.ghost) {
      state.drag = null;
      return;
    }
    const shouldCommit = drag.progress > .24 || (drag.progress > .08 && drag.velocity > .45);
    const ghost = drag.ghost;
    ghost.style.transition = "";
    ghost.classList.add("is-settling");
    ghost.getBoundingClientRect();
    ghost.style.setProperty("--turn-progress", shouldCommit ? "1" : "0");

    const complete = () => {
      if (shouldCommit) finishTurn(drag.targetIndex);
      else {
        removeTurnSheet();
        markActive(state.index);
        updateControls();
        setStatus(`${leafLabel(state.index)}에 머물렀습니다.`);
      }
    };
    if (reducedMotion.matches) complete();
    else {
      ghost.addEventListener("transitionend", complete, { once: true });
      state.turnTimer = window.setTimeout(complete, 520);
    }
  }

  modeToggle?.addEventListener("click", toggleMode);
  previousButtons.forEach((button) => button.addEventListener("click", () => goTo(state.index - 1, { direction: "backward" })));
  nextButtons.forEach((button) => button.addEventListener("click", () => goTo(state.index + 1, { direction: "forward" })));

  links.forEach((link) => link.addEventListener("click", (event) => {
    const target = Number.parseInt(link.dataset.readerTarget || "0", 10);
    if (state.bookMode) event.preventDefault();
    closeToc();
    goTo(target);
  }));

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeToc();
      return;
    }
    if (!state.bookMode || state.animating || event.altKey || event.ctrlKey || event.metaKey) return;
    const target = event.target;
    if (target instanceof HTMLElement && (target.matches("input, textarea, select, button, a") || target.isContentEditable)) return;
    if (event.key === "ArrowLeft" || event.key === "PageUp") {
      event.preventDefault();
      goTo(state.index - 1, { direction: "backward" });
    } else if (event.key === "ArrowRight" || event.key === "PageDown" || event.key === " ") {
      event.preventDefault();
      goTo(state.index + 1, { direction: "forward" });
    } else if (event.key === "Home") {
      event.preventDefault();
      goTo(0, { direction: "backward" });
    } else if (event.key === "End") {
      event.preventDefault();
      goTo(leaves.length - 1, { direction: "forward" });
    }
  });

  stage?.addEventListener("pointerdown", beginDrag);
  stage?.addEventListener("pointermove", moveDrag, { passive: false });
  stage?.addEventListener("pointerup", endDrag);
  stage?.addEventListener("pointercancel", endDrag);

  window.addEventListener("scroll", updateScrollProgress, { passive: true });
  window.addEventListener("resize", updateScrollProgress, { passive: true });

  if (!stage || !stageShell || !flow || leaves.length < 2) {
    body.classList.add("reader-scroll-mode");
    stageShell?.setAttribute("hidden", "");
    observeFlow();
    updateScrollProgress();
    return;
  }

  state.index = currentIndexFromLocation();
  enterBookMode();
})();
