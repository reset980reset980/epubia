(function () {
  const pages = window.READER_PAGES || [];
  const bookId = window.READER_BOOK_ID || "book";
  const spread = document.getElementById("bookSpread");
  const viewport = document.getElementById("bookViewport");
  const prevButton = document.getElementById("readerPrev");
  const nextButton = document.getElementById("readerNext");
  const label = document.getElementById("readerPageLabel");
  const progress = document.getElementById("readerProgress");
  const smaller = document.getElementById("readerSmaller");
  const larger = document.getElementById("readerLarger");
  const theme = document.getElementById("readerTheme");
  const focus = document.getElementById("readerFocus");
  const storageKey = `epubia:${bookId}:page`;
  const fontKey = `epubia:${bookId}:font`;
  const themeKey = `epubia:${bookId}:theme`;
  let pageIndex = Number(localStorage.getItem(storageKey) || 0);
  let fontScale = Number(localStorage.getItem(fontKey) || 1);
  let dark = localStorage.getItem(themeKey) === "dark";
  let drag = null;

  function isFocusMode() {
    return document.body.classList.contains("reader-focus-mode");
  }

  function spreadSize() {
    if (isFocusMode() && window.matchMedia("(orientation: landscape) and (max-height: 700px)").matches) {
      return 1;
    }
    return window.matchMedia("(max-width: 860px)").matches ? 1 : 2;
  }

  function normalizeIndex(index) {
    const size = spreadSize();
    const max = Math.max(0, pages.length - size);
    const next = Math.min(Math.max(index, 0), max);
    return size === 2 && next > 0 ? next - (next % 2) : next;
  }

  function pageElement(page, absoluteIndex, side) {
    const element = document.createElement("section");
    element.className = `book-page ${page ? page.kind || "chapter" : "blank"} ${side}`;
    element.dataset.page = String(absoluteIndex);

    if (!page) {
      element.className += " blank";
      return element;
    }

    if (page.kind === "cover") {
      element.innerHTML = "";
      const cover = document.createElement("div");
      cover.className = "book-cover-page";
      const studio = document.createElement("span");
      studio.textContent = "혜경 전자책 스튜디오";
      const title = document.createElement("h1");
      title.textContent = page.title || "";
      const author = document.createElement("p");
      author.textContent = page.author || "";
      const meta = document.createElement("small");
      meta.textContent = page.meta || "";
      cover.append(studio, title, author, meta);
      element.append(cover);
      return element;
    }

    const running = document.createElement("header");
    running.className = "page-running";
    running.textContent = page.runningTitle || page.title || "";
    element.append(running);

    if (page.title) {
      const heading = document.createElement("h2");
      heading.textContent = page.title;
      element.append(heading);
    }

    (page.paragraphs || []).forEach((text) => {
      const paragraph = document.createElement("p");
      paragraph.textContent = text;
      element.append(paragraph);
    });

    const number = document.createElement("footer");
    number.className = "page-number";
    number.textContent = String(absoluteIndex + 1);
    element.append(number);
    return element;
  }

  function render(direction) {
    if (!spread || !pages.length) return;
    pageIndex = normalizeIndex(pageIndex);
    viewport.classList.toggle("dark", dark);
    spread.style.setProperty("--reader-scale", String(fontScale));
    spread.style.setProperty("--drag-x", "0");
    spread.style.setProperty("--drag-progress", "0");
    spread.classList.remove("turn-forward", "turn-back", "dragging", "drag-left", "drag-right");
    if (direction) {
      spread.classList.add(direction === "next" ? "turn-forward" : "turn-back");
    }

    spread.innerHTML = "";
    const size = spreadSize();
    for (let offset = 0; offset < size; offset += 1) {
      const absolute = pageIndex + offset;
      const page = pages[absolute];
      spread.append(pageElement(page, absolute, offset === 0 ? "left" : "right"));
    }
    localStorage.setItem(storageKey, String(pageIndex));
    localStorage.setItem(fontKey, String(fontScale));
    localStorage.setItem(themeKey, dark ? "dark" : "light");

    const endPage = Math.min(pageIndex + size, pages.length);
    label.textContent = `${pageIndex + 1}-${endPage} / ${pages.length}`;
    progress.textContent = `${Math.round((endPage / pages.length) * 100)}%`;
    prevButton.disabled = pageIndex === 0;
    nextButton.disabled = endPage >= pages.length;
  }

  function move(delta) {
    const nextIndex = normalizeIndex(pageIndex + delta * spreadSize());
    if (nextIndex === pageIndex) return;
    pageIndex = nextIndex;
    render(delta > 0 ? "next" : "prev");
  }

  function setFocusMode(enabled) {
    document.body.classList.toggle("reader-focus-mode", enabled);
    if (focus) focus.textContent = enabled ? "나가기" : "전체 보기";
    render();
  }

  function dragProgress(deltaX) {
    const width = Math.max(1, viewport.getBoundingClientRect().width);
    return Math.max(-1, Math.min(1, deltaX / width));
  }

  function beginDrag(event) {
    if (!pages.length || event.button > 0) return;
    drag = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      active: false,
    };
    viewport.setPointerCapture(event.pointerId);
  }

  function updateDrag(event) {
    if (!drag || drag.id !== event.pointerId) return;
    const deltaX = event.clientX - drag.x;
    const deltaY = event.clientY - drag.y;
    if (!drag.active && Math.abs(deltaX) < 8) return;
    if (!drag.active && Math.abs(deltaY) > Math.abs(deltaX) * 1.25) {
      drag = null;
      return;
    }

    drag.active = true;
    event.preventDefault();
    const progressValue = dragProgress(deltaX);
    spread.classList.add("dragging");
    spread.classList.toggle("drag-left", deltaX < 0);
    spread.classList.toggle("drag-right", deltaX > 0);
    spread.style.setProperty("--drag-x", String(deltaX));
    spread.style.setProperty("--drag-progress", String(progressValue));
    spread.style.setProperty("--drag-abs", String(Math.abs(progressValue)));
  }

  function endDrag(event) {
    if (!drag || drag.id !== event.pointerId) return;
    const deltaX = event.clientX - drag.x;
    const active = drag.active;
    drag = null;
    spread.classList.remove("dragging", "drag-left", "drag-right");
    spread.style.setProperty("--drag-x", "0");
    spread.style.setProperty("--drag-progress", "0");
    spread.style.setProperty("--drag-abs", "0");
    if (!active) return;
    const threshold = Math.min(140, Math.max(70, viewport.getBoundingClientRect().width * 0.16));
    if (deltaX <= -threshold) move(1);
    if (deltaX >= threshold) move(-1);
  }

  prevButton.addEventListener("click", () => move(-1));
  nextButton.addEventListener("click", () => move(1));
  smaller.addEventListener("click", () => {
    fontScale = Math.max(0.86, Number((fontScale - 0.08).toFixed(2)));
    render();
  });
  larger.addEventListener("click", () => {
    fontScale = Math.min(1.3, Number((fontScale + 0.08).toFixed(2)));
    render();
  });
  theme.addEventListener("click", () => {
    dark = !dark;
    theme.textContent = dark ? "밤" : "종이";
    render();
  });
  focus.addEventListener("click", async () => {
    const shouldFocus = !isFocusMode();
    setFocusMode(shouldFocus);
    try {
      if (shouldFocus && !document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
      } else if (!shouldFocus && document.fullscreenElement) {
        await document.exitFullscreen();
      }
    } catch (_) {
      // Browser fullscreen can be denied; CSS focus mode still works.
    }
  });
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) {
      setFocusMode(false);
    }
  });
  document.querySelectorAll(".chapter-nav button").forEach((button) => {
    button.addEventListener("click", () => {
      pageIndex = normalizeIndex(Number(button.dataset.page || 0));
      render("next");
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "PageDown") move(1);
    if (event.key === "ArrowLeft" || event.key === "PageUp") move(-1);
  });
  viewport.addEventListener("pointerdown", beginDrag);
  viewport.addEventListener("pointermove", updateDrag);
  viewport.addEventListener("pointerup", endDrag);
  viewport.addEventListener("pointercancel", endDrag);
  window.addEventListener("resize", () => render());
  theme.textContent = dark ? "밤" : "종이";
  render();
})();
