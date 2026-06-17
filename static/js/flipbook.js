(function () {
  const sourcePages = window.READER_PAGES || [];
  let pages = sourcePages;
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
  let paginationKey = "";

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

  function layoutKey() {
    const rect = viewport.getBoundingClientRect();
    return [
      Math.round(rect.width),
      Math.round(rect.height),
      spreadSize(),
      fontScale,
      isFocusMode() ? "focus" : "normal",
    ].join(":");
  }

  function measuredSpreadWidth() {
    const size = spreadSize();
    const styles = getComputedStyle(viewport);
    const horizontalPadding = parseFloat(styles.paddingLeft) + parseFloat(styles.paddingRight);
    const maxSpread = Math.min(Math.max(1, viewport.clientWidth - horizontalPadding), 860);
    return size === 1 ? maxSpread : maxSpread / 2;
  }

  function pageFits(measurer, page) {
    const probe = pageElement(page, 0, "left");
    probe.classList.add("reader-measure-page");
    measurer.replaceChildren(probe);
    const blocks = probe.querySelectorAll("h2, p");
    const lastBlock = blocks[blocks.length - 1];
    if (!lastBlock) return true;
    const pageRect = probe.getBoundingClientRect();
    const contentBottom = lastBlock.getBoundingClientRect().bottom - pageRect.top;
    const reserve = isFocusMode() && window.matchMedia("(orientation: landscape) and (max-height: 700px)").matches ? 30 : 48;
    return contentBottom <= probe.clientHeight - reserve && probe.scrollHeight <= probe.clientHeight;
  }

  function pageTextLength(page) {
    return (page.title || "").length + (page.paragraphs || []).join(" ").length;
  }

  function maxPageChars() {
    if (isFocusMode() && window.matchMedia("(orientation: landscape) and (max-height: 700px)").matches) {
      return 450;
    }
    return spreadSize() === 1 ? 820 : 680;
  }

  function repaginate() {
    if (!sourcePages.length || !viewport) return sourcePages;
    const key = layoutKey();
    if (key === paginationKey) return pages;
    paginationKey = key;

    const nextPages = [];
    const cover = sourcePages.find((page) => page.kind === "cover");
    if (cover) nextPages.push(cover);

    const measurer = document.createElement("div");
    measurer.className = "reader-page-measurer";
    measurer.style.setProperty("--reader-scale", String(fontScale));
    const styles = getComputedStyle(viewport);
    const pageWidth = measuredSpreadWidth();
    const verticalPadding = parseFloat(styles.paddingTop) + parseFloat(styles.paddingBottom);
    const pageHeight = Math.max(1, viewport.clientHeight - verticalPadding);
    measurer.style.width = `${pageWidth}px`;
    measurer.style.height = `${pageHeight}px`;
    document.body.append(measurer);

    let current = null;
    for (const source of sourcePages) {
      if (source.kind === "cover") continue;
      const paragraphs = source.paragraphs || [];
      let pageTitle = source.title || "";
      if (!paragraphs.length && source.title) {
        nextPages.push({...source});
        continue;
      }
      for (const paragraph of paragraphs) {
        const startsNewSection = pageTitle || (current && source.chapter && source.chapter !== current.chapter);
        if (!current || startsNewSection) {
          if (current) nextPages.push(current);
          current = {
            kind: source.kind || "chapter",
            title: pageTitle || "",
            runningTitle: source.runningTitle || pageTitle || "",
            paragraphs: [],
            chapter: source.chapter || 0,
          };
          pageTitle = "";
        }
        const candidate = {...current, paragraphs: [...current.paragraphs, paragraph]};
        if (current.paragraphs.length && (pageTextLength(candidate) > maxPageChars() || !pageFits(measurer, candidate))) {
          nextPages.push(current);
          current = {
            kind: source.kind || "chapter",
            title: "",
            runningTitle: source.runningTitle || current.runningTitle || "",
            paragraphs: [paragraph],
            chapter: source.chapter || current.chapter || 0,
          };
        } else {
          current = candidate;
        }
      }
    }
    if (current) nextPages.push(current);
    measurer.remove();
    pages = nextPages;
    return pages;
  }

  function render(direction) {
    if (!spread || !sourcePages.length) return;
    repaginate();
    window.READER_RENDERED_PAGES = pages;
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
