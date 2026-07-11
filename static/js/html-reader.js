(() => {
  "use strict";

  const frame = document.getElementById("htmlBookFrame");
  const stage = document.getElementById("htmlReaderStage");
  const reload = document.getElementById("htmlReaderReload");
  const fullscreen = document.getElementById("htmlReaderFullscreen");

  if (!frame || !stage) return;

  frame.addEventListener("load", () => {
    stage.classList.add("is-ready");
  });

  reload?.addEventListener("click", () => {
    stage.classList.remove("is-ready");
    frame.src = frame.src;
  });

  fullscreen?.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await stage.requestFullscreen();
      }
    } catch (_error) {
      // 전체 화면 API를 막은 브라우저에서도 일반 읽기는 그대로 유지한다.
    }
  });

  document.addEventListener("fullscreenchange", () => {
    if (fullscreen) {
      fullscreen.textContent = document.fullscreenElement ? "화면 닫기" : "전체 화면";
    }
  });
})();
