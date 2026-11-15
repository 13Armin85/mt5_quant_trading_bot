(() => {
  const app = document.querySelector('[data-testid="stApp"]');
  if (!app) return;
  window.__noaAppearanceObserver?.disconnect();
  let queued = false;
  const sync = () => {
    queued = false;
    const channels = getComputedStyle(app).backgroundColor.match(/[\d.]+/g);
    if (!channels || channels.length < 3) return;
    const [red, green, blue] = channels.map(Number);
    const appearance = (red * 0.2126 + green * 0.7152 + blue * 0.0722) > 128
      ? 'light' : 'dark';
    if (document.documentElement.dataset.noaAppearance !== appearance) {
      document.documentElement.dataset.noaAppearance = appearance;
    }
  };
  const observer = new MutationObserver(() => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(sync);
    }
  });
  observer.observe(app, {attributes: true, attributeFilter: ['class', 'style']});
  window.__noaAppearanceObserver = observer;
  sync();
})();
