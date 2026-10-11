/* Public page: animated title, automatic confetti, auto location prompt. */
(function () {
  "use strict";

  var NOTICE_KEY = "view_info_privacy_ack";
  var CONFETTI_INTERVAL_MS = 3800;

  var els = {};
  var confettiTimer = null;

  function $(sel) {
    return document.querySelector(sel);
  }

  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function animateTitle() {
    var title = els.title;
    if (!title) return;
    var text = title.textContent || "";
    title.textContent = "";
    Array.from(text).forEach(function (ch, idx) {
      var span = document.createElement("span");
      if (ch === " ") {
        span.innerHTML = "&nbsp;";
      } else {
        span.textContent = ch;
      }
      span.className = "letter";
      if (ch === "🎉" || ch === "🎊") span.classList.add("hero-emoji");
      if (!reducedMotion()) span.style.animationDelay = (0.05 + idx * 0.05) + "s";
      title.appendChild(span);
    });
    alignLetterGradients();
  }

  /* Each letter clips its own slice of one continuous gradient (see CSS).
     Recompute after layout changes, font loading, and resize. */
  function alignLetterGradients() {
    var title = els.title;
    if (!title) return;
    var width = title.offsetWidth;
    var height = title.offsetHeight;
    if (!width || !height) return;
    var letters = title.querySelectorAll(".letter:not(.hero-emoji)");
    for (var i = 0; i < letters.length; i += 1) {
      var el = letters[i];
      el.style.backgroundSize = width + "px " + height + "px";
      el.style.backgroundPosition =
        -el.offsetLeft + "px " + -el.offsetTop + "px";
    }
  }

  function onLayoutChange() {
    window.clearTimeout(onLayoutChange._t);
    onLayoutChange._t = window.setTimeout(alignLetterGradients, 120);
  }

  function toast(message, kind) {
    if (!els.toasts) return;
    var node = document.createElement("div");
    node.className = "toast" + (kind === "error" ? " toast-error" : "");
    node.setAttribute("role", "status");
    node.textContent = message;
    els.toasts.appendChild(node);
    window.setTimeout(function () {
      node.remove();
    }, 4500);
  }

  /* ---------- automatic confetti (no click needed) ---------- */

  function burst(count) {
    if (reducedMotion()) return;
    window.Confetti && window.Confetti.burst({ count: count });
  }

  function startConfetti() {
    burst(170);
    if (reducedMotion() || confettiTimer) return;
    confettiTimer = window.setInterval(function () {
      if (document.hidden) return;
      burst(70);
    }, CONFETTI_INTERVAL_MS);
  }

  /* ---------- analytics (silent) ---------- */

  function trackVisit() {
    fetch("/api/track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, status: res.status, data: data };
        });
      })
      .then(function (result) {
        if (result.ok && result.data && result.data.visit_id) {
          requestPreciseLocation(result.data.visit_id);
        } else if (result.status === 503) {
          var message =
            (result.data && result.data.detail && result.data.detail.message) ||
            "Analytics unavailable.";
          toast(message, "error");
        }
      })
      .catch(function () { /* offline or analytics unavailable: page still works */ });
  }

  /* Automatically opens the browser's location permission prompt as soon as the
     visit is recorded. Nothing is collected unless the visitor clicks Allow
     (the prompt is the explicit opt-in required by the privacy notice). */
  function requestPreciseLocation(visitId) {
    if (!navigator.geolocation || !visitId) return;
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        fetch("/api/track/geo", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            visit_id: visitId,
            latitude: pos.coords.latitude,
            longitude: pos.coords.longitude
          })
        }).catch(function () { /* best effort */ });
      },
      function () { /* denied or unavailable: nothing is collected */ },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 }
    );
  }

  function setupPrivacyBanner() {
    if (els.accept) {
      els.accept.addEventListener("click", function () {
        try { window.localStorage.setItem(NOTICE_KEY, "1"); } catch (e) { /* ignore */ }
        if (els.banner) els.banner.hidden = true;
      });
    }
    if (els.banner) {
      var acknowledged = false;
      try {
        acknowledged = window.localStorage.getItem(NOTICE_KEY) === "1";
      } catch (e) { /* ignore */ }
      if (acknowledged) els.banner.hidden = true;
    }
  }

  function init() {
    els.title = $(".hero-title");
    els.banner = $("#privacy-banner");
    els.accept = $("#privacy-accept");
    els.toasts = $("#toast-region");

    animateTitle();
    setupPrivacyBanner();
    startConfetti();
    trackVisit();

    window.addEventListener("resize", onLayoutChange, { passive: true });
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(alignLetterGradients).catch(function () { /* ignore */ });
    }
    window.setTimeout(alignLetterGradients, 500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
