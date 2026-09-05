// ── Shared site chrome: scroll progress bar, solid nav, scroll-reveal, count-up ──

(function () {
  const track = document.createElement('div');
  track.className = 'scroll-progress-track';
  const fill = document.createElement('div');
  fill.className = 'scroll-progress-fill';
  track.appendChild(fill);
  document.body.prepend(track);

  const updateProgress = () => {
    const h = document.documentElement.scrollHeight - window.innerHeight;
    fill.style.width = (h > 0 ? (window.scrollY / h) * 100 : 0) + '%';
  };
  window.addEventListener('scroll', updateProgress, { passive: true });
  updateProgress();
})();

(function () {
  const nav = document.querySelector('.site-nav');
  if (!nav) return;
  const update = () => nav.classList.toggle('is-solid', window.scrollY > 40);
  window.addEventListener('scroll', update, { passive: true });
  update();
})();

(function () {
  const targets = document.querySelectorAll('.rev');
  if (!targets.length) return;
  const obs = new IntersectionObserver(
    (entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add('on');
          obs.unobserve(e.target);
        }
      });
    },
    { threshold: 0.12 }
  );
  targets.forEach((t) => obs.observe(t));
})();

(function () {
  const targets = document.querySelectorAll('[data-countup]');
  if (!targets.length) return;
  const obs = new IntersectionObserver(
    (entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        obs.unobserve(e.target);
        const el = e.target;
        const to = parseFloat(el.getAttribute('data-countup'));
        const decimals = parseInt(el.getAttribute('data-decimals') || '0', 10);
        const suffix = el.getAttribute('data-suffix') || '';
        const dur = 1800;
        const start = performance.now();
        const tick = (now) => {
          const p = Math.min((now - start) / dur, 1);
          const ease = 1 - Math.pow(1 - p, 4);
          const v = ease * to;
          el.textContent = (decimals ? v.toFixed(decimals) : Math.floor(v).toLocaleString()) + suffix;
          if (p < 1) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
    },
    { threshold: 0.5 }
  );
  targets.forEach((t) => obs.observe(t));
})();
