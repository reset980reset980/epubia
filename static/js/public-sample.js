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
    observer: null,
    drag: null,
    curl: null,
    motion: null,
    frame: 0,
    lastFrame: 0,
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

  function markSpread(index, resetScroll = true) {
    const leftIndex = index - 1;
    leaves.forEach((leaf, leafIndex) => {
      const onLeft = leafIndex === leftIndex;
      const onRight = leafIndex === index;
      const active = onLeft || onRight;
      leaf.classList.toggle("is-active", active);
      leaf.classList.toggle("is-left-page", onLeft);
      leaf.classList.toggle("is-right-page", onRight);
      leaf.setAttribute("aria-hidden", String(!active));
      leaf.inert = !active;
      if (active && resetScroll) leaf.scrollTop = 0;
    });
    stage?.classList.toggle("has-left-page", leftIndex >= 0);
  }

  function stripCount() {
    if (window.matchMedia("(max-width: 520px), (pointer: coarse)").matches) return 16;
    return 22;
  }

  function visualClone(source, width, height, offsetX) {
    const clone = source.cloneNode(true);
    clone.classList.remove("is-active");
    clone.classList.add("curl-face-content");
    clone.removeAttribute("id");
    clone.removeAttribute("data-reader-leaf");
    clone.removeAttribute("data-reader-page");
    clone.setAttribute("aria-hidden", "true");
    clone.inert = true;
    clone.style.width = `${width}px`;
    clone.style.height = `${height}px`;
    clone.style.left = `${-offsetX}px`;
    clone.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));
    clone.querySelectorAll("[data-reader-leaf], [data-reader-page]").forEach((element) => {
      element.removeAttribute("data-reader-leaf");
      element.removeAttribute("data-reader-page");
    });
    clone.querySelectorAll("a, button, input, textarea, select, summary, [tabindex]").forEach((element) => {
      element.setAttribute("tabindex", "-1");
      element.setAttribute("aria-hidden", "true");
    });
    clone.scrollTop = source.scrollTop;
    return clone;
  }

  function buildCurl(direction, targetIndex) {
    if (!stage) return null;
    removeCurl();

    const fromIndex = state.index;
    const turningSource = direction === "forward" ? leaves[fromIndex] : leaves[targetIndex];
    const rect = stage.getBoundingClientRect();
    const pageWidth = rect.width / 2;
    const count = stripCount();
    const stripWidth = pageWidth / count;

    const shadow = document.createElement("span");
    shadow.className = `curl-cast-shadow is-${direction}`;
    shadow.setAttribute("aria-hidden", "true");

    const curl = document.createElement("div");
    curl.className = `page-curl is-${direction}`;
    curl.setAttribute("aria-hidden", "true");
    curl.style.setProperty("--curl-strips", String(count));
    curl.style.setProperty("--curl-width", `${pageWidth}px`);
    curl.style.setProperty("--curl-height", `${rect.height}px`);

    const strips = [];
    let host = curl;
    for (let index = 0; index < count; index += 1) {
      const strip = document.createElement("div");
      strip.className = "curl-strip";
      strip.style.setProperty("--strip-index", String(index));

      const front = document.createElement("div");
      front.className = "curl-face curl-front";
      const back = document.createElement("div");
      back.className = "curl-face curl-back";

      const sourceOffset = direction === "forward"
        ? index * stripWidth
        : pageWidth - (index + 1) * stripWidth;
      front.appendChild(visualClone(turningSource, pageWidth, rect.height, sourceOffset));
      back.appendChild(visualClone(turningSource, pageWidth, rect.height, sourceOffset));
      strip.append(front, back);
      host.appendChild(strip);
      host = strip;
      strips.push(strip);
    }
    strips.at(-1)?.classList.add("is-edge");

    markSpread(targetIndex);

    stage.append(shadow, curl);
    stage.classList.add("is-curling");
    state.curl = {
      element: curl,
      shadow,
      strips,
      count,
      direction,
      fromIndex,
      targetIndex,
      progress: 0,
    };
    state.animating = true;
    applyCurl(0);
    return state.curl;
  }

  function applyCurl(progressValue) {
    const curlState = state.curl;
    if (!curlState) return;
    const progressValueSafe = clamp(progressValue, 0, 1);
    curlState.progress = progressValueSafe;

    // A chain of narrow tangents approximates a flexible sheet. The curve is
    // flat at both ends and reaches its strongest bend halfway through.
    const phase = progressValueSafe;
    const sweep = Math.PI * phase;
    const bend = 0.62 * Math.sin(Math.PI * phase);
    const directionSign = curlState.direction === "forward" ? -1 : 1;
    const rootAngle = directionSign * (sweep + bend) * (180 / Math.PI);
    const segmentAngle = (2 * bend / curlState.count) * (180 / Math.PI);
    const shade = Math.sin(Math.PI * phase);

    curlState.element.style.transform = `rotateY(${rootAngle.toFixed(3)}deg)`;
    curlState.element.style.setProperty("--curl-shade", shade.toFixed(3));
    curlState.element.style.setProperty("--curl-step", `${segmentAngle.toFixed(4)}deg`);
    curlState.shadow.style.opacity = (shade * .72).toFixed(3);
    curlState.shadow.style.transform = `scaleX(${(.2 + shade * .8).toFixed(3)})`;

    curlState.strips.forEach((strip, index) => {
      const nearLight = Math.abs(Math.cos((sweep + bend) - index * (2 * bend / curlState.count)));
      const farLight = Math.abs(Math.cos((sweep + bend) - (index + 1) * (2 * bend / curlState.count)));
      strip.style.setProperty("--curl-light", nearLight.toFixed(3));
      strip.style.setProperty("--curl-a-near", ((1 - nearLight) * .5).toFixed(3));
      strip.style.setProperty("--curl-a-far", ((1 - farLight) * .5).toFixed(3));
    });
  }

  function stopMotion() {
    if (state.frame) window.cancelAnimationFrame(state.frame);
    state.frame = 0;
    state.motion = null;
  }

  function removeCurl() {
    stopMotion();
    stage?.querySelector(".page-curl")?.remove();
    stage?.querySelector(".curl-cast-shadow")?.remove();
    stage?.classList.remove("is-curling", "is-dragging");
    state.curl = null;
    state.animating = false;
    state.drag = null;
  }

  function finishTurn(targetIndex, announce = true) {
    removeCurl();
    state.index = clamp(targetIndex, 0, leaves.length - 1);
    markSpread(state.index);
    updateControls();
    replaceLocation();
    if (announce) setStatus(`${leafLabel(state.index)} 페이지를 펼쳤습니다.`);
    turnHint?.classList.add("is-dismissed");
  }

  function restoreTurn(fromIndex) {
    removeCurl();
    state.index = fromIndex;
    markSpread(state.index, false);
    updateControls();
    setStatus(`${leafLabel(state.index)}에 머물렀습니다.`);
  }

  function motionFrame(now) {
    state.frame = 0;
    if (!state.motion || !state.curl) return;
    const motion = state.motion;
    const delta = Math.min(.032, (now - state.lastFrame) / 1000 || .016);
    state.lastFrame = now;
    const distance = state.curl.progress - motion.target;
    motion.velocity += (-motion.stiffness * distance - motion.damping * motion.velocity) * delta;
    const next = state.curl.progress + motion.velocity * delta;
    const crossedTarget = (motion.target === 0 && next <= 0) || (motion.target === 1 && next >= 1);
    if (crossedTarget) {
      applyCurl(motion.target);
      const done = motion.done;
      state.motion = null;
      done?.();
      return;
    }
    applyCurl(next);

    if (Math.abs(state.curl.progress - motion.target) < .002 && Math.abs(motion.velocity) < .025) {
      applyCurl(motion.target);
      const done = motion.done;
      state.motion = null;
      done?.();
      return;
    }
    state.frame = window.requestAnimationFrame(motionFrame);
  }

  function springTo(target, done, velocity = 0) {
    if (!state.curl) return;
    if (reducedMotion.matches) {
      applyCurl(target);
      done?.();
      return;
    }
    stopMotion();
    state.motion = {
      target,
      velocity,
      stiffness: 180,
      damping: 24,
      done,
    };
    state.lastFrame = performance.now();
    state.frame = window.requestAnimationFrame(motionFrame);
  }

  function animateTurn(targetIndex, direction) {
    if (!stage || state.animating || targetIndex === state.index || targetIndex < 0 || targetIndex >= leaves.length) return;
    const curlState = buildCurl(direction, targetIndex);
    if (!curlState) return;
    springTo(1, () => finishTurn(targetIndex));
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
    removeCurl();
    state.bookMode = true;
    let blankPage = stage.querySelector(".book-blank-page");
    if (!blankPage) {
      blankPage = document.createElement("span");
      blankPage.className = "book-blank-page";
      blankPage.setAttribute("aria-hidden", "true");
      stage.prepend(blankPage);
    }
    leaves.forEach((leaf) => stage.appendChild(leaf));
    stage.appendChild(stage.querySelector(".book-spine") || document.createElement("span"));
    stageShell.hidden = false;
    body.classList.add("reader-book-mode");
    body.classList.remove("reader-scroll-mode");
    markSpread(state.index);
    modeToggle?.setAttribute("aria-pressed", "true");
    modeToggle?.setAttribute("aria-label", "연속 스크롤 읽기로 전환");
    if (modeLabel) modeLabel.textContent = "연속 읽기";
    updateControls();
    replaceLocation();
    state.observer?.disconnect();
    setStatus(`${leafLabel(state.index)}. 곡면 책 넘김 방식으로 읽습니다.`);
  }

  function enterScrollMode() {
    if (!stageShell || !flow) return;
    removeCurl();
    state.bookMode = false;
    leaves.forEach((leaf) => {
      leaf.classList.remove("is-active", "is-left-page", "is-right-page");
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
      lastProgress: 0,
      velocity: 0,
      direction,
      targetIndex,
      progress: 0,
      started: false,
      moved: 0,
    };
    stage.setPointerCapture?.(event.pointerId);
  }

  function setupDragVisual(drag) {
    if (!stage) return;
    const curlState = buildCurl(drag.direction, drag.targetIndex);
    if (!curlState) return;
    stage.classList.add("is-dragging");
    drag.started = true;
    state.drag = drag;
  }

  function moveDrag(event) {
    const drag = state.drag;
    if (!drag || drag.pointerId !== event.pointerId || !stage) return;
    const deltaX = event.clientX - drag.startX;
    const deltaY = event.clientY - drag.startY;
    const intended = drag.direction === "forward" ? -deltaX : deltaX;
    drag.moved = Math.max(drag.moved, Math.abs(deltaX));

    if (!drag.started) {
      if (Math.abs(deltaY) > Math.abs(deltaX) * 1.2 && Math.abs(deltaY) > 10) {
        stage.releasePointerCapture?.(event.pointerId);
        state.drag = null;
        return;
      }
      if (intended < 7) return;
      setupDragVisual(drag);
    }
    if (!state.curl) return;

    event.preventDefault();
    const now = performance.now();
    const elapsed = Math.max(.001, (now - drag.lastTime) / 1000);
    drag.progress = clamp(intended / (stage.clientWidth * .31), 0, 1);
    drag.velocity = (drag.progress - drag.lastProgress) / elapsed;
    drag.lastProgress = drag.progress;
    drag.lastX = event.clientX;
    drag.lastTime = now;
    applyCurl(drag.progress);
  }

  function endDrag(event) {
    const drag = state.drag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    stage?.releasePointerCapture?.(event.pointerId);

    if (!drag.started || !state.curl) {
      state.drag = null;
      if (event.type !== "pointercancel" && drag.moved < 6) goTo(drag.targetIndex, { direction: drag.direction });
      return;
    }

    state.drag = null;
    const shouldCommit = event.type !== "pointercancel" && (drag.progress > .42 || drag.velocity > 1.1);
    if (shouldCommit) springTo(1, () => finishTurn(drag.targetIndex), Math.max(0, drag.velocity));
    else springTo(0, () => restoreTurn(state.curl?.fromIndex ?? state.index), Math.min(0, drag.velocity));
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
  stage?.addEventListener("dragstart", (event) => event.preventDefault());

  window.addEventListener("scroll", updateScrollProgress, { passive: true });
  window.addEventListener("resize", () => {
    updateScrollProgress();
    if (state.curl) restoreTurn(state.curl.fromIndex);
  }, { passive: true });

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
