// Recording playback: waits for the server-side MKV->MP4 remux, then plays with native seeking.
(() => {
  "use strict";

  function setOverlay(root, state, text) {
    const overlay = root.querySelector(".player-overlay");
    if (!overlay) return;
    overlay.dataset.state = state;
    overlay.querySelector(".overlay-text").textContent = text;
    overlay.hidden = state === "ready";
  }

  async function prepare(root) {
    if (root.dataset.started) return;
    root.dataset.started = "1";
    const url = root.dataset.playUrl;
    const video = root.querySelector("video");
    const started = Date.now();

    for (;;) {
      if (!document.body.contains(root)) return; // user picked another hour
      let response;
      try {
        // A 1-byte range request tells us whether the MP4 is ready without downloading it.
        response = await fetch(url, { headers: { Range: "bytes=0-0" }, credentials: "same-origin" });
      } catch (err) {
        setOverlay(root, "error", "Network error — retrying…");
        await new Promise((r) => setTimeout(r, 5000));
        continue;
      }
      if (response.status === 206 || response.status === 200) {
        if (response.body) response.body.cancel().catch(() => {});
        video.src = url;
        setOverlay(root, "ready", "");
        video.play().catch(() => {});
        return;
      }
      if (response.status === 202) {
        const body = await response.json().catch(() => ({}));
        const secs = Math.round((Date.now() - started) / 1000);
        const what = body.status === "queued" ? "Waiting for another video to finish converting" : "Preparing video";
        setOverlay(root, "loading", `${what}… ${secs}s`);
        const wait = parseInt(response.headers.get("Retry-After") || "3", 10) * 1000;
        await new Promise((r) => setTimeout(r, wait));
        continue;
      }
      if (response.status === 401 || response.status === 403) {
        setOverlay(root, "error", "Session expired — reload the page to sign in");
        return;
      }
      const body = await response.json().catch(() => ({}));
      const detail = body.error && typeof body.error.detail === "string" ? body.error.detail : `Error ${response.status}`;
      setOverlay(root, "error", detail);
      return;
    }
  }

  function scan(node) {
    (node.querySelectorAll ? node : document).querySelectorAll(".recording-player[data-play-url]").forEach(prepare);
  }

  document.addEventListener("DOMContentLoaded", () => scan(document));
  document.addEventListener("htmx:afterSwap", (evt) => {
    scan(evt.detail.target);
    // On phones the player sits below the hour grid; bring it into view.
    if (evt.detail.target.id === "player" && window.matchMedia("(max-width: 860px)").matches) {
      evt.detail.target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
})();
