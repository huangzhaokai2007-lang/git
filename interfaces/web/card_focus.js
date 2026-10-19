// Presentation only: native Streamlit buttons still own every business action.
export default function () {
  let frame = 0;
  let root = null;
  let signature = '';
  let resizeObserver = null;
  let pending = null;
  let settleTimer = 0;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');

  function cardId(tile) {
    return tile.querySelector('.bank-card-identity')?.dataset.card || '';
  }

  function layout(selected, immediate = false) {
    if (!root || !root.isConnected) return;
    const tiles = [...root.querySelectorAll('[class*="st-key-bank-tile-"]')];
    const width = root.clientWidth;
    if (!width || !tiles.length) return;
    const count = width < 600 ? 1 : 2;
    const gap = 28;
    const slot = (width - gap * (count - 1)) / count;
    const focusWidth = Math.min(width, 480);
    const duration = reduced.matches || immediate ? '0ms' : '700ms';
    const fade = reduced.matches || immediate ? '0ms' : '450ms';
    const active = tiles.find(tile => cardId(tile) === selected);
    root.style.position = 'relative';
    root.style.transition = `height ${duration} cubic-bezier(.22,.8,.25,1)`;
    let rowY = 0;
    let rowHeight = 0;
    let totalHeight = 0;
    tiles.forEach((tile, index) => {
      const id = cardId(tile);
      const chosen = active && id === selected;
      const muted = !!active && !chosen;
      if (index % count === 0) { rowY = totalHeight; rowHeight = 0; }
      tile.style.position = 'absolute';
      tile.style.width = `${chosen ? focusWidth : slot}px`;
      tile.style.top = '0';
      tile.style.left = '0';
      tile.style.transition = `transform ${duration} cubic-bezier(.22,.8,.25,1), width ${duration} ease, opacity ${fade} ease, filter ${fade} ease`;
      tile.style.transitionDelay = !active && !immediate && !reduced.matches ? '0ms,0ms,180ms,180ms' : '0ms';
      const x = chosen ? (width - focusWidth) / 2 : (index % count) * (slot + gap);
      const y = chosen ? 0 : rowY;
      tile.style.transform = `translate(${x}px, ${y}px)`;
      tile.style.opacity = muted ? '0' : '1';
      tile.style.filter = muted ? 'blur(10px)' : 'blur(0px)';
      tile.style.pointerEvents = muted ? 'none' : '';
      tile.style.zIndex = chosen ? '2' : '1';
      tile.inert = muted;
      tile.setAttribute('aria-hidden', muted ? 'true' : 'false');
      rowHeight = Math.max(rowHeight, tile.offsetHeight);
      if (index % count === count - 1 || index === tiles.length - 1) totalHeight = rowY + rowHeight + gap;
    });
    const height = `${active ? active.offsetHeight + gap : totalHeight}px`;
    root.style.height = height;
    root.style.minHeight = height;
    root.style.flexShrink = '0';
    root.style.overflow = active ? 'clip' : 'visible';
    root.dataset.focus = active ? selected : '';
  }

  function sync() {
    frame = 0;
    const next = document.querySelector('.st-key-bank-gallery');
    if (!next) return;
    const first = next !== root;
    root = next;
    if (first) {
      resizeObserver?.disconnect();
      resizeObserver = new ResizeObserver(schedule);
      resizeObserver.observe(root);
    }
    const marker = root.querySelector('.bank-gallery-state');
    if (pending && marker?.dataset.revision !== pending.revision) pending = null;
    const selected = pending ? pending.target : marker?.dataset.selected || '';
    const tiles = [...root.querySelectorAll('[class*="st-key-bank-tile-"]')];
    const current = [selected, root.clientWidth, ...tiles.map(tile => `${cardId(tile)}:${tile.offsetHeight}`)].join('|');
    if (!first && signature === current && root.dataset.focus === selected) return;
    signature = current;
    layout(selected, first);
  }

  function schedule() {
    if (!frame) frame = requestAnimationFrame(sync);
  }

  function click(event) {
    const button = event.target.closest('button');
    if (!button) return;
    const tile = button.closest('[class*="st-key-bank-tile-"]');
    let target;
    if (tile && button.closest('[class*="st-key-detail-"]')) {
      target = cardId(tile);
    } else if (button.closest('.st-key-detail-close')) {
      target = '';
    }
    if (target === undefined || !root) return;
    pending = {target, revision: root.querySelector('.bank-gallery-state')?.dataset.revision};
    clearTimeout(settleTimer);
    settleTimer = setTimeout(() => { pending = null; schedule(); }, 2500);
    layout(target);
    root.style.scrollMarginTop = '70px';
    root.scrollIntoView?.({block: 'start', behavior: reduced.matches ? 'auto' : 'smooth'});
  }

  const observer = new MutationObserver(schedule);
  observer.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['data-selected', 'data-revision']});
  document.addEventListener('click', click, true);
  window.addEventListener('resize', schedule);
  reduced.addEventListener('change', schedule);
  schedule();
  return () => {
    observer.disconnect();
    resizeObserver?.disconnect();
    cancelAnimationFrame(frame);
    clearTimeout(settleTimer);
    document.removeEventListener('click', click, true);
    window.removeEventListener('resize', schedule);
    reduced.removeEventListener('change', schedule);
  };
}
