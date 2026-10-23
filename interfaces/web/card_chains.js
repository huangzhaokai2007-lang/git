// Decorative overlay only. A status change must come from the Python fact marker.
function createCardChains(reduced) {
  const records = new Map();

  function links(start, end) {
    let result = '';
    for (let x = start; x < end; x += 22) {
      result += `<rect x="${x}" y="-7" width="28" height="14" rx="7" fill="none" stroke="#17283c" stroke-width="6"/>`;
      result += `<rect x="${x}" y="-7" width="28" height="14" rx="7" fill="none" stroke="#bacbdc" stroke-width="3.5"/>`;
      result += `<path d="M ${x + 7},-10 h 13" stroke="#f4f8ff" stroke-width="1.3" stroke-linecap="round"/>`;
    }
    return result;
  }

  function overlay(button) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.classList.add('bank-chain-overlay');
    svg.setAttribute('viewBox', '0 0 480 208');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    svg.innerHTML = `<rect width="480" height="208" rx="20" fill="#071522" opacity=".16"/>` +
      [23.43, -23.43].map(angle => `<g transform="translate(240 104) rotate(${angle})">` +
        `<g class="bank-chain-half" data-side="-1">${links(-286, 0)}</g>` +
        `<g class="bank-chain-half" data-side="1">${links(0, 286)}</g></g>`).join('') +
      `<g transform="translate(240 104)"><g class="bank-chain-badge">` +
      `<circle r="32" fill="#14263c" stroke="#9eafc4" stroke-width="2"/>` +
      `<path class="bank-chain-shackle" d="M -12,-1 V -12 A 12,12 0 0 1 12,-12 V -1" fill="none" stroke="#e5edf7" stroke-width="6" stroke-linecap="round"/>` +
      `<rect x="-20" y="-3" width="40" height="30" rx="7" fill="#e8bc72" stroke="#fff0c8" stroke-width="1.5"/>` +
      `<circle cx="0" cy="9" r="3.4" fill="#3c3024"/><path d="M 0,11 v 7" stroke="#3c3024" stroke-width="3"/>` +
      `</g></g>`;
    button.appendChild(svg);
    return svg;
  }

  function cancel(record) {
    record.generation += 1;
    record.animations.forEach(animation => animation.cancel());
    record.animations = [];
  }

  function settle(record) {
    if (!record.overlay) return;
    record.overlay.style.display = record.status === 'locked' ? 'block' : 'none';
    record.overlay.dataset.phase = record.status === 'locked' ? 'locked' : 'idle';
  }

  function animate(record, locking) {
    cancel(record);
    const svg = record.overlay;
    svg.style.display = 'block';
    svg.dataset.phase = locking ? 'locking' : 'unlocking';
    const generation = record.generation;
    svg.querySelectorAll('.bank-chain-half').forEach((half, index) => {
      const outside = `translateX(${Number(half.dataset.side) * 310}px)`;
      const frames = [{transform: outside, opacity: 0}, {transform: 'translateX(0px)', opacity: 1}];
      record.animations.push(half.animate(locking ? frames : frames.slice().reverse(), {
        duration: 850, delay: (locking ? 0 : 200) + (index % 2) * 60,
        easing: 'cubic-bezier(.22,.8,.25,1)', fill: 'both',
      }));
    });
    const badge = svg.querySelector('.bank-chain-badge');
    const frames = [{opacity: 0, transform: 'scale(.75)'}, {opacity: 1, transform: 'scale(1)'}];
    record.animations.push(badge.animate(locking ? frames : frames.slice().reverse(), {
      duration: locking ? 350 : 250, delay: locking ? 550 : 150, fill: 'both',
    }));
    const shackle = svg.querySelector('.bank-chain-shackle');
    shackle.style.transformOrigin = '-12px -1px';
    const turn = [{transform: 'rotate(-35deg)'}, {transform: 'rotate(0deg)'}];
    record.animations.push(shackle.animate(locking ? turn : turn.slice().reverse(), {
      duration: 250, delay: locking ? 650 : 0, fill: 'both',
    }));
    Promise.all(record.animations.map(animation => animation.finished.catch(() => null))).then(() => {
      if (record.generation !== generation) return;
      cancel(record);
      settle(record);
    });
  }

  function sync(root) {
    const present = new Set();
    root.querySelectorAll('[class*="st-key-bank-tile-"]').forEach(tile => {
      const marker = tile.querySelector('.bank-card-identity');
      const button = tile.querySelector('[class*="st-key-detail-"] button');
      const id = marker?.dataset.card;
      const status = marker?.dataset.status;
      if (!id || !button || !['normal', 'locked', 'lost', 'frozen'].includes(status)) return;
      present.add(id);
      let record = records.get(id);
      const previous = record?.status;
      if (!record) {
        record = {status, button, overlay: null, animations: [], generation: 0};
        records.set(id, record);
      }
      if (record.button !== button || (record.overlay && !record.overlay.isConnected)) {
        cancel(record);
        record.overlay?.remove();
        record.button = button;
        record.overlay = null;
      }
      record.status = status;
      if ((status === 'locked' || previous === 'locked') && !record.overlay) record.overlay = overlay(button);
      if (reduced.matches || !previous || (!['normal', 'locked'].includes(previous) && status === 'locked') ||
          (status !== 'normal' && status !== 'locked')) {
        cancel(record);
        settle(record);
      } else if (previous === 'normal' && status === 'locked') {
        animate(record, true);
      } else if (previous === 'locked' && status === 'normal') {
        animate(record, false);
      } else {
        if (previous !== status) cancel(record);
        if (!record.animations.length) settle(record);
      }
    });
    for (const [id, record] of records) {
      if (!present.has(id)) { cancel(record); record.overlay?.remove(); records.delete(id); }
    }
  }

  return {sync, destroy() {
    records.forEach(record => { cancel(record); record.overlay?.remove(); });
    records.clear();
  }};
}
