// Live HLS players with loading / offline / reconnecting states and automatic recovery.
(() => {
  "use strict";

  const MAX_BACKOFF_MS = 30000;

  function setOverlay(root, state, text) {
    const overlay = root.querySelector(".player-overlay");
    overlay.dataset.state = state;
    overlay.querySelector(".overlay-text").textContent = text;
    overlay.hidden = state === "playing";
  }

  function setupControls(root, video) {
    const playBtn = root.querySelector('[data-action="play"]');
    const muteBtn = root.querySelector('[data-action="mute"]');
    const fsBtn = root.querySelector('[data-action="fullscreen"]');

    const sync = () => {
      playBtn.textContent = video.paused ? "Play" : "Pause";
      playBtn.setAttribute("aria-label", playBtn.textContent);
      muteBtn.textContent = video.muted ? "Unmute" : "Mute";
      muteBtn.setAttribute("aria-label", muteBtn.textContent);
    };
    playBtn.addEventListener("click", () => (video.paused ? video.play().catch(() => {}) : video.pause()));
    muteBtn.addEventListener("click", () => { video.muted = !video.muted; sync(); });
    fsBtn.addEventListener("click", () => {
      if (document.fullscreenElement) return document.exitFullscreen();
      if (root.requestFullscreen) return root.requestFullscreen();
      if (video.webkitEnterFullscreen) video.webkitEnterFullscreen(); // iOS Safari
    });
    ["play", "pause", "volumechange"].forEach((evt) => video.addEventListener(evt, sync));
    sync();
  }

  function startLive(root) {
    const video = root.querySelector("video");
    const src = root.dataset.hlsSrc;
    let hls = null;
    let failures = 0;
    let retryTimer = null;
    let stallTimer = null;

    setupControls(root, video);

    const scheduleRetry = (reason) => {
      if (hls) { hls.destroy(); hls = null; }
      clearTimeout(retryTimer);
      failures += 1;
      const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.min(failures, 5));
      if (failures >= 4) {
        setOverlay(root, "offline", `Camera offline · retrying in ${Math.round(delay / 1000)}s`);
      } else {
        setOverlay(root, "reconnecting", reason || "Reconnecting…");
      }
      retryTimer = setTimeout(connect, delay);
    };

    const statusEl = root.closest(".camera-card")?.querySelector(".camera-status");
    const onPlaying = () => {
      failures = 0;
      setOverlay(root, "playing", "");
      // The on-demand stream just came up: refresh the online badge now, not in 20s.
      if (statusEl && window.htmx) setTimeout(() => window.htmx.trigger(statusEl, "refresh"), 6000);
    };
    video.addEventListener("playing", onPlaying);
    video.addEventListener("waiting", () => {
      clearTimeout(stallTimer);
      setOverlay(root, "loading", "Buffering…");
      // A stream that stays stalled is treated like a disconnect.
      stallTimer = setTimeout(() => { if (video.readyState < 3) scheduleRetry("Stream stalled, reconnecting…"); }, 15000);
    });
    video.addEventListener("timeupdate", () => clearTimeout(stallTimer));

    function connect() {
      setOverlay(root, failures ? "reconnecting" : "loading", failures ? "Reconnecting…" : "Connecting…");
      if (window.Hls && window.Hls.isSupported()) {
        hls = new window.Hls({
          lowLatencyMode: false,
          liveSyncDurationCount: 3,
          liveMaxLatencyDurationCount: 8,
          backBufferLength: 30,
          maxBufferLength: 20,
          manifestLoadPolicy: { default: { maxTimeToFirstByteMs: 15000, maxLoadTimeMs: 20000,
            timeoutRetry: { maxNumRetry: 1, retryDelayMs: 0, maxRetryDelayMs: 0 },
            errorRetry: { maxNumRetry: 2, retryDelayMs: 1000, maxRetryDelayMs: 4000 } } },
        });
        hls.on(window.Hls.Events.ERROR, (_evt, data) => {
          if (data.response && (data.response.code === 401 || data.response.code === 403)) {
            setOverlay(root, "offline", "Session expired — reload the page to sign in");
            hls.destroy(); hls = null;
            return;
          }
          if (!data.fatal) return;
          if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && failures < 2) {
            failures += 1;
            hls.recoverMediaError();
            return;
          }
          scheduleRetry();
        });
        hls.on(window.Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => onPlaying()));
        hls.loadSource(src);
        hls.attachMedia(video);
      } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
        // Safari / iOS native HLS (cookies are sent automatically, same origin).
        video.src = src;
        video.addEventListener("error", () => scheduleRetry(), { once: true });
        video.play().catch(() => {});
      } else {
        setOverlay(root, "offline", "This browser cannot play live video");
      }
    }

    // Do not hold a stream open in background tabs; saves CPU on server and phone.
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        clearTimeout(retryTimer);
        if (hls) { hls.destroy(); hls = null; }
        video.removeAttribute("src");
        video.load();
      } else {
        failures = 0;
        connect();
      }
    });

    connect();
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".live-player[data-hls-src]").forEach(startLive);
  });
})();
