(function () {
  "use strict";

  const reader = document.getElementById("pdfReader");
  if (!reader) return;

  const pageCount = Math.max(1, Number.parseInt(reader.dataset.pageCount || "1", 10));
  const pageUrlTemplate = reader.dataset.pageUrlTemplate || "";
  const renderVersion = reader.dataset.renderVersion || "";
  const bookTitle = reader.dataset.bookTitle || "전자책";
  const mobileQuery = window.matchMedia("(max-width: 768px)");

  const drawer = document.getElementById("pdfPageDrawer");
  const drawerToggle = document.getElementById("pdfPageToggle");
  const drawerClose = document.getElementById("pdfDrawerClose");
  const drawerOverlay = document.getElementById("pdfDrawerOverlay");
  const thumbnailNav = document.getElementById("pdfThumbnailNav");
  const thumbnailButtons = Array.from(document.querySelectorAll(".pdf-thumbnail-button"));
  const previousButton = document.getElementById("pdfPrev");
  const nextButton = document.getElementById("pdfNext");
  const pageInput = document.getElementById("pdfPageInput");
  const zoomOutButton = document.getElementById("pdfZoomOut");
  const zoomInButton = document.getElementById("pdfZoomIn");
  const zoomValue = document.getElementById("pdfZoomValue");
  const scroller = document.getElementById("pdfCanvasScroller");
  const pageSheet = document.getElementById("pdfPageSheet");
  const readerMain = reader.querySelector(".pdf-reader-main");
  const pageImage = document.getElementById("pdfPageImage");
  const pageCaption = document.getElementById("pdfPageCaption");
  const status = document.getElementById("pdfReaderStatus");
  const errorPanel = document.getElementById("pdfErrorPanel");
  const retryButton = document.getElementById("pdfRetry");

  if (!drawer || !drawerToggle || !drawerClose || !drawerOverlay || !thumbnailNav ||
      !previousButton || !nextButton || !pageInput || !zoomOutButton ||
      !zoomInButton || !zoomValue || !scroller || !pageSheet || !readerMain || !pageImage ||
      !pageCaption || !status || !errorPanel || !retryButton) return;

  const state = {
    page: 1,
    zoom: 1,
    loadToken: 0,
    drawerOpen: false,
    drawerHideTimer: 0,
    previousFocus: null,
    touchStartX: 0,
    touchStartY: 0,
    touchStartTime: 0
  };

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function pageButton(pageNumber) {
    return thumbnailButtons[pageNumber - 1] || null;
  }

  function screenSource(pageNumber) {
    if (pageUrlTemplate.includes("987654321")) {
      const version = renderVersion ? `&v=${encodeURIComponent(renderVersion)}` : "";
      return `${pageUrlTemplate.replace("987654321", String(pageNumber))}?variant=screen${version}`;
    }
    const button = pageButton(pageNumber);
    return button ? button.dataset.screenSrc : "";
  }

  function setStatus(message) {
    status.textContent = message;
  }

  function revealThumbnail(pageNumber) {
    const button = pageButton(pageNumber);
    if (!button) return;
    const navRect = thumbnailNav.getBoundingClientRect();
    const buttonRect = button.getBoundingClientRect();
    const inset = 8;
    if (buttonRect.top < navRect.top + inset) {
      thumbnailNav.scrollTop += buttonRect.top - navRect.top - inset;
    } else if (buttonRect.bottom > navRect.bottom - inset) {
      thumbnailNav.scrollTop += buttonRect.bottom - navRect.bottom + inset;
    }
  }

  function updateControls() {
    previousButton.disabled = state.page <= 1;
    nextButton.disabled = state.page >= pageCount;
    pageInput.value = String(state.page);
    zoomOutButton.disabled = state.zoom <= 0.7;
    zoomInButton.disabled = state.zoom >= 2.5;
    zoomValue.value = `${Math.round(state.zoom * 100)}%`;
    zoomValue.textContent = zoomValue.value;

    thumbnailButtons.forEach((button) => {
      const isCurrent = Number.parseInt(button.dataset.page || "0", 10) === state.page;
      button.classList.toggle("is-current", isCurrent);
      button.tabIndex = isCurrent ? 0 : -1;
      if (isCurrent) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
      const marker = button.querySelector(".pdf-thumbnail-meta small");
      if (marker) marker.hidden = !isCurrent;
    });
  }

  function updateSheetSize(preserveScrollPosition) {
    if (!pageImage.naturalWidth || !pageImage.naturalHeight) return;

    const oldScrollWidth = scroller.scrollWidth;
    const oldScrollHeight = scroller.scrollHeight;
    const centerX = oldScrollWidth > 0 ? (scroller.scrollLeft + scroller.clientWidth / 2) / oldScrollWidth : 0.5;
    const centerY = oldScrollHeight > 0 ? (scroller.scrollTop + scroller.clientHeight / 2) / oldScrollHeight : 0.5;
    const padding = mobileQuery.matches ? 22 : 48;
    const availableWidth = Math.max(180, scroller.clientWidth - padding);
    const availableHeight = Math.max(240, scroller.clientHeight - padding);
    const fitScale = Math.min(
      availableWidth / pageImage.naturalWidth,
      availableHeight / pageImage.naturalHeight
    );
    const fittedWidth = Math.max(180, pageImage.naturalWidth * fitScale);

    pageSheet.style.width = `${Math.round(fittedWidth * state.zoom)}px`;
    pageSheet.style.aspectRatio = `${pageImage.naturalWidth} / ${pageImage.naturalHeight}`;

    if (preserveScrollPosition) {
      window.requestAnimationFrame(() => {
        scroller.scrollLeft = Math.max(0, centerX * scroller.scrollWidth - scroller.clientWidth / 2);
        scroller.scrollTop = Math.max(0, centerY * scroller.scrollHeight - scroller.clientHeight / 2);
      });
    }
  }

  function preloadNextPage() {
    if (state.page >= pageCount) return;
    const source = screenSource(state.page + 1);
    if (!source) return;
    const preloadImage = new Image();
    preloadImage.decoding = "async";
    preloadImage.src = source;
  }

  function replacePageHash() {
    const nextHash = `page=${state.page}`;
    if (window.location.hash.slice(1) === nextHash) return;
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${nextHash}`);
  }

  function loadPage(pageNumber, options) {
    const settings = Object.assign({ focusCanvas: false, closeDrawer: false }, options || {});
    const targetPage = clamp(Number.parseInt(pageNumber, 10) || 1, 1, pageCount);
    const source = screenSource(targetPage);
    if (!source) return;

    state.page = targetPage;
    state.loadToken += 1;
    const requestToken = state.loadToken;

    updateControls();
    replacePageHash();
    reader.classList.add("is-loading");
    reader.classList.remove("has-page-error");
    errorPanel.hidden = true;
    pageImage.classList.remove("is-ready");
    pageImage.setAttribute("aria-busy", "true");
    pageImage.alt = `${bookTitle} PDF ${targetPage}페이지`;
    pageCaption.textContent = `${targetPage}페이지`;
    setStatus(`${targetPage}페이지를 불러오는 중입니다.`);

    let requestSettled = false;

    pageImage.onload = function () {
      if (requestSettled) return;
      requestSettled = true;
      if (requestToken !== state.loadToken) return;
      reader.classList.remove("is-loading");
      pageImage.classList.add("is-ready");
      pageImage.setAttribute("aria-busy", "false");
      updateSheetSize(false);
      scroller.scrollTo({ left: 0, top: 0, behavior: "auto" });
      setStatus(`${targetPage}페이지를 표시했습니다. 좌우 방향키로 페이지를 이동할 수 있습니다.`);
      revealThumbnail(targetPage);
      preloadNextPage();
      if (settings.focusCanvas) scroller.focus({ preventScroll: true });
    };

    pageImage.onerror = function () {
      if (requestSettled) return;
      requestSettled = true;
      if (requestToken !== state.loadToken) return;
      reader.classList.remove("is-loading");
      reader.classList.add("has-page-error");
      pageImage.setAttribute("aria-busy", "false");
      errorPanel.hidden = false;
      setStatus(`${targetPage}페이지 이미지를 불러오지 못했습니다. 다시 시도해 주세요.`);
    };

    pageImage.src = source;
    if (pageImage.complete) {
      window.requestAnimationFrame(() => {
        if (requestSettled || requestToken !== state.loadToken) return;
        if (pageImage.naturalWidth > 0) pageImage.onload();
        else pageImage.onerror();
      });
    }

    if (settings.closeDrawer && mobileQuery.matches) closeDrawer(true);
  }

  function setZoom(nextZoom) {
    state.zoom = clamp(Math.round(nextZoom * 100) / 100, 0.7, 2.5);
    updateControls();
    updateSheetSize(true);
    setStatus(`확대 비율을 ${Math.round(state.zoom * 100)}%로 변경했습니다.`);
  }

  function setDrawerAccessibility(isOpen) {
    drawerToggle.setAttribute("aria-expanded", String(isOpen));
    drawerToggle.setAttribute("aria-label", isOpen ? "페이지 목록 닫기" : "페이지 목록 열기");
    drawer.setAttribute("aria-hidden", String(!isOpen));
    drawerOverlay.setAttribute("aria-hidden", String(!isOpen));
    drawer.inert = !isOpen;
    setBackgroundInert(isOpen);
    if (isOpen) {
      drawer.setAttribute("role", "dialog");
      drawer.setAttribute("aria-modal", "true");
    } else {
      drawer.removeAttribute("role");
      drawer.removeAttribute("aria-modal");
    }
  }

  function setBackgroundInert(isInert) {
    readerMain.inert = isInert;
    [document.querySelector(".site-header"), document.querySelector(".site-footer")]
      .filter(Boolean)
      .forEach((element) => { element.inert = isInert; });
  }

  function openDrawer() {
    if (!mobileQuery.matches || state.drawerOpen) return;
    window.clearTimeout(state.drawerHideTimer);
    state.previousFocus = document.activeElement;
    state.drawerOpen = true;
    drawer.hidden = false;
    drawerOverlay.hidden = false;
    setDrawerAccessibility(true);
    document.body.classList.add("pdf-page-drawer-open");

    window.requestAnimationFrame(() => {
      reader.classList.add("is-drawer-open");
      drawerClose.focus({ preventScroll: true });
      revealThumbnail(state.page);
    });
  }

  function closeDrawer(restoreFocus) {
    if (!mobileQuery.matches) return;
    window.clearTimeout(state.drawerHideTimer);
    state.drawerOpen = false;
    reader.classList.remove("is-drawer-open");
    document.body.classList.remove("pdf-page-drawer-open");

    if (restoreFocus) {
      setBackgroundInert(false);
      const previousFocus = state.previousFocus;
      const focusTarget = previousFocus instanceof HTMLElement &&
        previousFocus !== document.body && previousFocus.isConnected
        ? previousFocus
        : drawerToggle;
      focusTarget.focus({ preventScroll: true });
    }
    setDrawerAccessibility(false);

    state.drawerHideTimer = window.setTimeout(() => {
      if (state.drawerOpen) return;
      drawer.hidden = true;
      drawerOverlay.hidden = true;
    }, 290);

  }

  function syncDrawerMode() {
    window.clearTimeout(state.drawerHideTimer);
    if (mobileQuery.matches) {
      state.drawerOpen = false;
      reader.classList.remove("is-drawer-open");
      document.body.classList.remove("pdf-page-drawer-open");
      setDrawerAccessibility(false);
      drawer.hidden = true;
      drawerOverlay.hidden = true;
    } else {
      state.drawerOpen = false;
      reader.classList.remove("is-drawer-open");
      document.body.classList.remove("pdf-page-drawer-open");
      drawer.hidden = false;
      drawerOverlay.hidden = true;
      drawer.inert = false;
      readerMain.inert = false;
      [document.querySelector(".site-header"), document.querySelector(".site-footer")]
        .filter(Boolean)
        .forEach((element) => { element.inert = false; });
      drawer.setAttribute("aria-hidden", "false");
      drawer.removeAttribute("role");
      drawer.removeAttribute("aria-modal");
      drawerOverlay.setAttribute("aria-hidden", "true");
      drawerToggle.setAttribute("aria-expanded", "false");
    }
    window.requestAnimationFrame(() => updateSheetSize(false));
  }

  function focusableDrawerElements() {
    return Array.from(drawer.querySelectorAll(
      "a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])"
    )).filter((element) => !element.hidden && element.getClientRects().length > 0);
  }

  function trapDrawerFocus(event) {
    if (event.key !== "Tab" || !state.drawerOpen || !mobileQuery.matches) return;
    const focusable = focusableDrawerElements();
    if (!focusable.length) {
      event.preventDefault();
      drawer.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function pageFromLocation() {
    const hashMatch = window.location.hash.match(/^#?page=(\d+)$/);
    if (hashMatch) return clamp(Number.parseInt(hashMatch[1], 10), 1, pageCount);
    const queryPage = new URLSearchParams(window.location.search).get("page");
    return clamp(Number.parseInt(queryPage || "1", 10), 1, pageCount);
  }

  thumbnailButtons.forEach((button) => {
    const thumbnail = button.querySelector("img");
    if (thumbnail) {
      if (thumbnail.complete && thumbnail.naturalWidth > 0) button.classList.add("is-thumb-ready");
      thumbnail.addEventListener("load", () => button.classList.add("is-thumb-ready"));
      thumbnail.addEventListener("error", () => {
        button.classList.remove("is-thumb-ready");
        button.classList.add("is-thumb-error");
      });
    }

    button.addEventListener("click", () => {
      loadPage(button.dataset.page, { closeDrawer: true });
    });

    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const nextPage = clamp(Number.parseInt(button.dataset.page || "1", 10) + direction, 1, pageCount);
      const target = pageButton(nextPage);
      if (target) target.focus();
    });
  });

  previousButton.addEventListener("click", () => loadPage(state.page - 1));
  nextButton.addEventListener("click", () => loadPage(state.page + 1));
  zoomOutButton.addEventListener("click", () => setZoom(state.zoom - 0.25));
  zoomInButton.addEventListener("click", () => setZoom(state.zoom + 0.25));
  retryButton.addEventListener("click", () => loadPage(state.page, { focusCanvas: true }));

  pageInput.addEventListener("change", () => loadPage(pageInput.value, { focusCanvas: true }));
  pageInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    loadPage(pageInput.value, { focusCanvas: true });
  });

  drawerToggle.addEventListener("click", openDrawer);
  drawerClose.addEventListener("click", () => closeDrawer(true));
  drawerOverlay.addEventListener("click", () => closeDrawer(true));

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.drawerOpen) {
      event.preventDefault();
      closeDrawer(true);
      return;
    }

    trapDrawerFocus(event);
    if (event.defaultPrevented || state.drawerOpen || event.altKey || event.ctrlKey || event.metaKey) return;

    const target = event.target;
    const isEditing = target instanceof HTMLElement && (
      target.matches("input, textarea, select") || target.isContentEditable
    );
    if (isEditing) return;

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      loadPage(state.page - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      loadPage(state.page + 1);
    }
  });

  scroller.addEventListener("touchstart", (event) => {
    if (event.touches.length !== 1) return;
    state.touchStartX = event.touches[0].clientX;
    state.touchStartY = event.touches[0].clientY;
    state.touchStartTime = Date.now();
  }, { passive: true });

  scroller.addEventListener("touchend", (event) => {
    if (!mobileQuery.matches || event.changedTouches.length !== 1) return;
    const deltaX = event.changedTouches[0].clientX - state.touchStartX;
    const deltaY = event.changedTouches[0].clientY - state.touchStartY;
    const elapsed = Date.now() - state.touchStartTime;
    const canTurnPage = state.zoom <= 1.05 || scroller.scrollWidth <= scroller.clientWidth + 4;
    if (!canTurnPage || elapsed > 700 || Math.abs(deltaX) < 52 || Math.abs(deltaX) < Math.abs(deltaY) * 1.35) return;
    if (deltaX < 0) loadPage(state.page + 1);
    else loadPage(state.page - 1);
  }, { passive: true });

  let resizeFrame = 0;
  window.addEventListener("resize", () => {
    window.cancelAnimationFrame(resizeFrame);
    resizeFrame = window.requestAnimationFrame(() => updateSheetSize(false));
  });

  if (typeof mobileQuery.addEventListener === "function") {
    mobileQuery.addEventListener("change", syncDrawerMode);
  } else {
    mobileQuery.addListener(syncDrawerMode);
  }

  syncDrawerMode();
  updateControls();
  loadPage(pageFromLocation());
})();
