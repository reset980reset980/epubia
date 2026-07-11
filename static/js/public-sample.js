(() => {
  const toc = document.querySelector("#sample-toc");
  const trigger = document.querySelector("[data-toc-open]");
  const closers = document.querySelectorAll("[data-toc-close]");
  const scrim = document.querySelector(".toc-scrim");
  const progress = document.querySelector("[data-reading-progress]");
  const links = [...document.querySelectorAll("[data-toc-link]")];
  const pages = [...document.querySelectorAll("[data-reader-page]")];

  const closeToc = () => {
    toc?.classList.remove("is-open");
    document.body.classList.remove("toc-open");
    trigger?.setAttribute("aria-expanded", "false");
    if (scrim) scrim.hidden = true;
  };
  const openToc = () => {
    toc?.classList.add("is-open");
    document.body.classList.add("toc-open");
    trigger?.setAttribute("aria-expanded", "true");
    if (scrim) scrim.hidden = false;
  };

  trigger?.addEventListener("click", openToc);
  closers.forEach((element) => element.addEventListener("click", closeToc));
  links.forEach((link) => link.addEventListener("click", closeToc));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeToc();
  });

  const updateProgress = () => {
    if (!progress) return;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    progress.style.width = `${max > 0 ? Math.min(100, (window.scrollY / max) * 100) : 0}%`;
  };
  updateProgress();
  window.addEventListener("scroll", updateProgress, { passive: true });
  window.addEventListener("resize", updateProgress, { passive: true });

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (!visible) return;
        links.forEach((link) => {
          link.setAttribute("aria-current", link.getAttribute("href") === `#${visible.target.id}` ? "true" : "false");
        });
      },
      { rootMargin: "-18% 0px -62%", threshold: [0, .25, .6] }
    );
    pages.forEach((page) => observer.observe(page));
  }
})();
