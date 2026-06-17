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
  const storageKey = `epubia:${bookId}:page`;
  const fontKey = `epubia:${bookId}:font`;
  const themeKey = `epubia:${bookId}:theme`;
  let pageIndex = Number(localStorage.getItem(storageKey) || 0);
  let fontScale = Number(localStorage.getItem(fontKey) || 1);
  let dark = localStorage.getItem(themeKey) === "dark";

  function spreadSize() {
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
    spread.classList.remove("turn-forward", "turn-back");
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
  window.addEventListener("resize", () => render());
  theme.textContent = dark ? "밤" : "종이";
  render();
})();
