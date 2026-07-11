(function () {
  const sourcePages = window.READER_PAGES || [];
  let pages = sourcePages;
  const bookId = window.READER_BOOK_ID || "book";
  const studioName = window.READER_STUDIO_NAME || "전자책 스튜디오";
  const coverUrl = window.READER_COVER_URL || "";
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
  const toc = document.getElementById("readerToc");
  const tocToggle = document.getElementById("readerTocToggle");
  const tocClose = document.getElementById("readerTocClose");
  const tocOverlay = document.getElementById("readerTocOverlay");
  const tocProgress = document.getElementById("readerTocProgress");
  const tocProgressBar = document.getElementById("readerTocProgressBar");
  const tocLocation = document.getElementById("readerTocLocation");
  const tocMedia = window.matchMedia("(max-width: 768px)");
  const storageKey = `epubia:${bookId}:page`;
  const fontKey = `epubia:${bookId}:font`;
  const themeKey = `epubia:${bookId}:theme`;
  let pageIndex = Number(localStorage.getItem(storageKey) || 0);
  let fontScale = Number(localStorage.getItem(fontKey) || 1);
  let dark = localStorage.getItem(themeKey) === "dark";
  let drag = null;
  let paginationKey = "";
  let tocOpen = false;
  let activeChapter = null;

  function isFocusMode() {
    return document.body.classList.contains("reader-focus-mode");
  }

  function isMobileToc() {
    return tocMedia.matches;
  }

  function tocFocusableElements() {
    if (!toc) return [];
    return Array.from(toc.querySelectorAll("a[href], button:not([disabled]), [tabindex]:not([tabindex='-1'])"))
      .filter((element) => element.getClientRects().length > 0);
  }

  function openToc() {
    if (!toc || !tocToggle || !isMobileToc()) return;
    tocOpen = true;
    toc.inert = false;
    toc.setAttribute("aria-hidden", "false");
    tocToggle.setAttribute("aria-expanded", "true");
    tocToggle.setAttribute("aria-label", "목차 닫기");
    if (tocOverlay) tocOverlay.setAttribute("aria-hidden", "false");
    document.body.classList.add("reader-toc-open");
    window.requestAnimationFrame(() => (tocClose || tocFocusableElements()[0])?.focus({preventScroll: true}));
  }

  function closeToc(options = {}) {
    if (!toc) return;
    const restoreFocus = options.restoreFocus !== false;
    const wasOpen = tocOpen;
    tocOpen = false;
    if (restoreFocus && wasOpen && tocToggle) {
      tocToggle.focus({preventScroll: true});
    }
    document.body.classList.remove("reader-toc-open");
    if (tocToggle) {
      tocToggle.setAttribute("aria-expanded", "false");
      tocToggle.setAttribute("aria-label", "목차 열기");
    }
    if (tocOverlay) tocOverlay.setAttribute("aria-hidden", "true");
    toc.setAttribute("aria-hidden", isMobileToc() ? "true" : "false");
    toc.inert = isMobileToc();
  }

  function syncTocMode() {
    if (!toc) return;
    if (isMobileToc()) {
      if (!tocOpen) {
        toc.setAttribute("aria-hidden", "true");
        toc.inert = true;
      }
      return;
    }
    closeToc({restoreFocus: false});
    toc.setAttribute("aria-hidden", "false");
    toc.inert = false;
  }

  function trapTocFocus(event) {
    if (!tocOpen || event.key !== "Tab") return false;
    const focusable = tocFocusableElements();
    if (!focusable.length) {
      event.preventDefault();
      return true;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    } else if (!toc.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    }
    return true;
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
      const details = document.createElement("div");
      details.className = "book-cover-details";
      const studio = document.createElement("span");
      studio.textContent = studioName;
      const title = document.createElement("h1");
      title.textContent = page.title || "";
      const author = document.createElement("p");
      author.textContent = page.author || "";
      const meta = document.createElement("small");
      meta.textContent = page.meta || "";
      details.append(studio, title, author, meta);
      cover.append(details);

      if (coverUrl) {
        const artwork = document.createElement("img");
        artwork.className = "reader-cover-art";
        artwork.alt = "";
        artwork.setAttribute("role", "presentation");
        artwork.loading = "eager";
        artwork.decoding = "async";
        cover.classList.add("cover-loading");
        artwork.addEventListener("load", () => {
          cover.classList.remove("cover-loading", "cover-fallback");
          cover.classList.add("has-cover");
        });
        artwork.addEventListener("error", () => {
          artwork.remove();
          cover.classList.remove("cover-loading", "has-cover");
          cover.classList.add("cover-fallback");
        });
        artwork.src = coverUrl;
        cover.prepend(artwork);
      }
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

  function availablePageHeight() {
    const styles = getComputedStyle(viewport);
    const verticalPadding = parseFloat(styles.paddingTop) + parseFloat(styles.paddingBottom);
    return Math.max(1, viewport.clientHeight - verticalPadding);
  }

  function maxPageChars() {
    if (isFocusMode()) {
      // Focus mode uses a ~100dvh viewport and a more compact font, so a page
      // holds far more text than the normal ~600px page. Scale the cap by the
      // actual page height (calibrated against the normal page) with headroom
      // so pageFits() — the measured gate — stays the binding constraint
      // instead of this heuristic cap leaving blank space at the bottom. This
      // covers every focus layout (desktop 2-col, mobile portrait/landscape
      // single-col) without per-orientation fixed caps.
      const base = spreadSize() === 1 ? 820 : 680;
      return Math.round((base * availablePageHeight() / 600) * 1.4);
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
    const pageWidth = measuredSpreadWidth();
    const pageHeight = availablePageHeight();
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

  function updateTocState(endPage) {
    const percentage = pages.length ? Math.round((endPage / pages.length) * 100) : 0;
    const visiblePage = pages
      .slice(pageIndex, endPage)
      .find((page) => Number(page?.chapter || 0) > 0) || pages[pageIndex];
    const chapterNumber = Number(visiblePage?.chapter || 0);
    let activeButton = null;

    document.querySelectorAll(".chapter-nav button[data-chapter]").forEach((button) => {
      const selected = Number(button.dataset.chapter || 0) === chapterNumber;
      button.classList.toggle("is-active", selected);
      if (selected) {
        button.setAttribute("aria-current", "location");
        activeButton = button;
      } else {
        button.removeAttribute("aria-current");
      }
    });

    if (tocProgress) {
      tocProgress.setAttribute("aria-valuenow", String(percentage));
      tocProgress.setAttribute("aria-valuetext", `${percentage}% 읽음`);
    }
    if (tocProgressBar) tocProgressBar.style.width = `${percentage}%`;
    if (tocLocation) {
      tocLocation.textContent = activeButton?.textContent.trim() || (chapterNumber ? `${chapterNumber}장` : "표지");
    }

    if (activeButton && activeChapter !== chapterNumber) {
      activeButton.scrollIntoView({block: "nearest"});
    }
    activeChapter = chapterNumber;
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
    // spreadSize() is the single source of truth for column count; drive the
    // grid directly so CSS media-query breakpoints can never disagree and
    // leave an empty column (e.g. mobile landscape single-page focus mode).
    spread.style.gridTemplateColumns = size === 1 ? "1fr" : "1fr 1fr";
    spread.classList.toggle("single-page", size === 1);
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
    updateTocState(endPage);
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
  tocToggle?.addEventListener("click", () => {
    if (tocOpen) closeToc();
    else openToc();
  });
  tocClose?.addEventListener("click", () => closeToc());
  tocOverlay?.addEventListener("click", () => closeToc());
  document.querySelectorAll(".chapter-nav button[data-chapter]").forEach((button) => {
    button.addEventListener("click", () => {
      repaginate();
      const previousIndex = pageIndex;
      const chapterNumber = Number(button.dataset.chapter || 0);
      const repaginatedIndex = pages.findIndex((page) => Number(page?.chapter || 0) === chapterNumber);
      const targetIndex = repaginatedIndex >= 0 ? repaginatedIndex : Number(button.dataset.page || 0);
      pageIndex = normalizeIndex(targetIndex);
      render(pageIndex === previousIndex ? undefined : pageIndex > previousIndex ? "next" : "prev");
      closeToc();
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && tocOpen) {
      event.preventDefault();
      closeToc();
      return;
    }
    if (trapTocFocus(event) || tocOpen) return;
    if (event.key === "ArrowRight" || event.key === "PageDown") move(1);
    if (event.key === "ArrowLeft" || event.key === "PageUp") move(-1);
  });
  viewport.addEventListener("pointerdown", beginDrag);
  viewport.addEventListener("pointermove", updateDrag);
  viewport.addEventListener("pointerup", endDrag);
  viewport.addEventListener("pointercancel", endDrag);
  window.addEventListener("resize", () => render());
  if (typeof tocMedia.addEventListener === "function") {
    tocMedia.addEventListener("change", syncTocMode);
  } else {
    tocMedia.addListener(syncTocMode);
  }
  syncTocMode();
  theme.textContent = dark ? "밤" : "종이";
  render();
  window.requestAnimationFrame(() => {
    // The first render can introduce the page scrollbar and change the usable
    // width. Re-measure once after layout settles so chapter targets stay stable.
    paginationKey = "";
    render();
  });
})();
