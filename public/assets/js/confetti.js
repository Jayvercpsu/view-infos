/* Lightweight confetti — no dependencies, honors prefers-reduced-motion. */
(function () {
  "use strict";

  var COLORS = ["#8b5cf6", "#ec4899", "#22d3ee", "#fbbf24", "#34d399", "#f472b6", "#a78bfa"];
  var canvas = null;
  var ctx = null;
  var particles = [];
  var rafId = null;
  var running = false;

  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function ensureCanvas() {
    if (canvas) return;
    canvas = document.getElementById("confetti");
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.id = "confetti";
      canvas.setAttribute("aria-hidden", "true");
      document.body.appendChild(canvas);
    }
    ctx = canvas.getContext("2d");
    resize();
    window.addEventListener("resize", resize, { passive: true });
  }

  function resize() {
    if (!canvas) return;
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(window.innerWidth * dpr);
    canvas.height = Math.floor(window.innerHeight * dpr);
    canvas.style.width = window.innerWidth + "px";
    canvas.style.height = window.innerHeight + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function spawn(count, originX, originY) {
    var i;
    for (i = 0; i < count; i += 1) {
      var angle = Math.random() * Math.PI * 2;
      var speed = 4 + Math.random() * 7;
      particles.push({
        x: originX,
        y: originY,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 3,
        size: 4 + Math.random() * 6,
        color: COLORS[(Math.random() * COLORS.length) | 0],
        rot: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 0.3,
        life: 1,
        decay: 0.006 + Math.random() * 0.008,
        shape: Math.random() > 0.5 ? "rect" : "circle"
      });
    }
  }

  function tick() {
    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    var alive = 0;
    for (var i = 0; i < particles.length; i += 1) {
      var p = particles[i];
      p.vy += 0.16;
      p.vx *= 0.99;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      p.life -= p.decay;
      if (p.life <= 0 || p.y > window.innerHeight + 40) continue;
      particles[alive] = p;
      alive += 1;
      ctx.save();
      ctx.globalAlpha = Math.max(0, Math.min(1, p.life));
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      if (p.shape === "rect") {
        ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6);
      } else {
        ctx.beginPath();
        ctx.arc(0, 0, p.size / 2, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
    particles.length = alive;
    if (alive > 0) {
      rafId = window.requestAnimationFrame(tick);
    } else {
      running = false;
      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    }
  }

  function burst(options) {
    if (reducedMotion()) return;
    ensureCanvas();
    var opts = options || {};
    var count = opts.count || 120;
    var x = typeof opts.x === "number" ? opts.x : window.innerWidth / 2;
    var y = typeof opts.y === "number" ? opts.y : window.innerHeight * 0.35;
    spawn(count, x, y);
    if (!running) {
      running = true;
      rafId = window.requestAnimationFrame(tick);
    }
  }

  window.Confetti = { burst: burst };
})();
