// All numeric labels are verbatim facts. Arithmetic only positions SVG geometry.
function ledgerRow(item, element) {
  const row = element('li', 'bank-pie-txn');
  const main = element('div', 'bank-pie-txn-main');
  main.append(element('span', 'bank-pie-merchant', item.counterparty || '未注明交易对手'),
    element('strong', 'bank-pie-txn-amount', `${item.amount_yuan} 元`));
  const meta = element('div', 'bank-pie-txn-meta');
  meta.append(element('time', '', String(item.ts || '').replace('T', ' ')),
    element('span', '', item.channel || ''));
  row.append(main, meta);
  if (item.memo) row.title = String(item.memo);
  return row;
}

function renderLedger(root, data, select, element) {
  root.querySelector?.('.bank-pie-ledger')?.remove();
  if (!data.selected) return;
  const panel = element('section', 'bank-pie-ledger');
  panel.setAttribute('aria-label', `${data.selected}支出明细`);
  const header = element('div', 'bank-pie-ledger-head');
  header.append(element('strong', '', `${data.selected} · 支出明细`));
  const close = element('button', 'bank-pie-close', '收起');
  close.type = 'button';
  close.onclick = event => select(null, event?.detail === 0);
  header.append(close);
  panel.append(header);
  const detail = data.detail;
  if (!detail) panel.append(element('p', 'bank-pie-ledger-note', '正在读取明细…'));
  else if (!detail.ok) panel.append(element('p', 'bank-pie-ledger-note', detail.message || '暂时无法读取明细。'));
  else {
    panel.append(element('p', 'bank-pie-ledger-note', `${data.period} · ${detail.count} 笔 · 支出 ${detail.amount_yuan} 元`));
    if (!detail.items?.length) panel.append(element('p', 'bank-pie-ledger-note', '该分类在此账期暂无支出明细。'));
    else {
      const list = element('ul', 'bank-pie-ledger-scroll');
      list.tabIndex = 0;
      list.setAttribute('aria-label', '交易明细，可上下滚动');
      detail.items.forEach(item => list.append(ledgerRow(item, element)));
      panel.append(list);
    }
  }
  root.append(panel);
}

export default function ({ parentElement, data, setStateValue = () => {} }) {
  const root = parentElement.querySelector('.bank-pie');
  const active = document.activeElement;
  const restoreFocus = root.dataset.focusTarget || (root.contains?.(active) && active?.getAttribute('aria-label'));
  delete root.dataset.focusTarget;
  const ns = 'http://www.w3.org/2000/svg';
  const colors = ['#a49be0', '#7e9fac', '#b3bda0', '#c5a888', '#8b9bc5', '#b08f9e', '#8caeaa'];
  const element = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  const svgElement = (tag, attrs = {}) => {
    const node = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  };
  root.replaceChildren();
  root.dataset.phase = 'waiting';
  const rows = (data.groups || []).map((group, index) => ({ group, index,
    weight: Number(String(group.amount_yuan ?? '').replaceAll(',', '')) }));
  const positive = rows.filter(row => Number.isFinite(row.weight) && row.weight > 0);
  // This denominator is for angles only; totals and percentages are never recomputed.
  const denominator = positive.reduce((weight, row) => weight + row.weight, 0);
  if (!denominator) {
    root.append(element('p', '', '该账期暂无支出分类。'));
    root.dataset.phase = 'done';
    return;
  }
  const layout = element('div', 'bank-pie-layout');
  const figure = element('figure', 'bank-pie-figure');
  const svg = svgElement('svg', { viewBox: '0 0 240 240', role: 'group', 'aria-label': '支出分类饼图' });
  const defs = svgElement('defs');
  const maskId = `pie-mask-${data.animation_id}`;
  const mask = svgElement('mask', { id: maskId, maskUnits: 'userSpaceOnUse', x: 0, y: 0, width: 240, height: 240 });
  const sweep = svgElement('circle', { cx: 120, cy: 120, r: 52, fill: 'none', stroke: 'white',
    'stroke-width': 104, pathLength: 1, 'stroke-dasharray': 1, transform: 'rotate(-90 120 120)', class: 'bank-pie-sweep' });
  mask.append(sweep);
  defs.append(mask);
  svg.append(defs, svgElement('circle', { cx: 120, cy: 120, r: 104, fill: '#191b20' }));
  const slices = svgElement('g', { mask: `url(#${maskId})` });
  const paths = [];
  let angle = -Math.PI / 2;
  positive.forEach(({ group, index, weight }) => {
    const next = angle + (weight / denominator) * Math.PI * 2;
    const point = a => `${120 + 104 * Math.cos(a)} ${120 + 104 * Math.sin(a)}`;
    const attrs = { fill: colors[index % colors.length], stroke: '#0b0c0e', 'stroke-width': 1.5,
      'vector-effect': 'non-scaling-stroke', tabindex: 0, class: 'bank-pie-slice' };
    const path = positive.length === 1 ? svgElement('circle', { ...attrs, cx: 120, cy: 120, r: 104 })
      : svgElement('path', { ...attrs, d: `M120 120 L${point(angle)} A104 104 0 ${next - angle > Math.PI ? 1 : 0} 1 ${point(next)} Z` });
    const title = svgElement('title');
    title.textContent = `${group.key} · ${group.amount_yuan} 元 · ${group.pct}%`;
    path.append(title);
    paths.push({ path, index });
    slices.append(path);
    angle = next;
  });
  svg.append(slices);
  figure.append(svg);
  const details = element('div', 'bank-pie-details');
  const legend = element('ul', 'bank-pie-legend');
  legend.setAttribute('aria-label', '分类金额与占比');
  const entries = [];
  let selected = data.selected || null;
  const selectedIndex = () => rows.find(row => row.group.key === selected)?.index ?? null;
  const highlight = index => {
    paths.forEach(item => {
      item.path.classList.toggle('is-muted', index !== null && item.index !== index);
      item.path.classList.toggle('is-highlighted', item.index === index);
    });
    entries.forEach(item => item.node.classList.toggle('is-muted', index !== null && item.index !== index));
  };
  const select = (category, keyboard = false) => {
    const previous = selected;
    selected = category === selected ? null : category;
    if (keyboard) root.dataset.focusTarget = selected ? 'ledger' : `查看${previous}支出明细`;
    highlight(selectedIndex());
    renderLedger(root, { ...data, selected, detail: null }, select, element);
    setStateValue('selected', keyboard ? { category: selected,
      focus_target: selected ? 'ledger' : `查看${previous}支出明细`,
      focus_id: `${Date.now()}:${Math.random()}` } : selected);
  };
  rows.forEach(({ group, index }) => {
    const row = element('li');
    row.tabIndex = 0;
    const dot = element('i', 'bank-pie-dot');
    dot.style.backgroundColor = colors[index % colors.length];
    row.append(dot, element('span', '', group.key), element('span', 'bank-pie-value', `${group.amount_yuan} 元`),
      element('span', 'bank-pie-pct', `${group.pct}%`));
    entries.push({ node: row, index });
    const targets = [row, paths.find(item => item.index === index)?.path].filter(Boolean);
    targets.forEach(node => {
      node.setAttribute('role', 'button');
      node.setAttribute('aria-label', `查看${group.key}支出明细`);
      node.setAttribute('aria-pressed', String(selected === group.key));
      node.onclick = () => select(group.key);
      node.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          select(group.key, true);
        }
      };
      node.onpointerenter = node.onfocus = () => highlight(index);
      node.onpointerleave = node.onblur = () => highlight(selectedIndex());
    });
    legend.append(row);
  });
  details.append(legend);
  if (data.total != null) {
    const total = element('p', 'bank-pie-total', '支出合计');
    total.append(element('strong', '', `${data.total} 元`));
    details.append(total);
  }
  // Comment 10: the income pie mirrors the expense one (same geometry, colors, legend markup,
  // totals label) and only carries facts values — no summation, no percentages recomputed.
  const incomePie = () => {
    const groups = data.income_groups || [];
    const weights = groups.map(group => Number(String(group.amount_yuan ?? '').replaceAll(',', '')));
    const total = weights.filter(weight => Number.isFinite(weight) && weight > 0)
      .reduce((sum, weight) => sum + weight, 0);
    const figure = element('figure', 'bank-pie-figure');
    const svg = svgElement('svg', { viewBox: '0 0 240 240', role: 'group', 'aria-label': '收入分类饼图' });
    const defs = svgElement('defs');
    const maskId = `pie-mask-income-${data.animation_id}`;
    const mask = svgElement('mask', { id: maskId, maskUnits: 'userSpaceOnUse', x: 0, y: 0, width: 240, height: 240 });
    mask.append(svgElement('circle', { cx: 120, cy: 120, r: 52, fill: 'none', stroke: 'white',
      'stroke-width': 104, pathLength: 1, 'stroke-dasharray': 1, transform: 'rotate(-90 120 120)', class: 'bank-pie-sweep' }));
    defs.append(mask);
    svg.append(defs, svgElement('circle', { cx: 120, cy: 120, r: 104, fill: '#191b20' }));
    const slices = svgElement('g', { mask: `url(#${maskId})` });
    let angle = -Math.PI / 2;
    weights.forEach((weight, index) => {
      if (!(Number.isFinite(weight) && weight > 0) || !total) return;
      const next = angle + (weight / total) * Math.PI * 2;
      const point = a => `${120 + 104 * Math.cos(a)} ${120 + 104 * Math.sin(a)}`;
      const attrs = { fill: colors[index % colors.length], stroke: '#0b0c0e', 'stroke-width': 1.5,
        'vector-effect': 'non-scaling-stroke' };
      const path = weights.filter(item => item > 0).length === 1
        ? svgElement('circle', { ...attrs, cx: 120, cy: 120, r: 104 })
        : svgElement('path', { ...attrs, d: `M120 120 L${point(angle)} A104 104 0 ${next - angle > Math.PI ? 1 : 0} 1 ${point(next)} Z` });
      const title = svgElement('title');
      title.textContent = `${groups[index].key} · ${groups[index].amount_yuan} 元 · ${groups[index].pct}%`;
      path.append(title);
      slices.append(path);
      angle = next;
    });
    svg.append(slices);
    figure.append(svg);
    const details = element('div', 'bank-pie-details');
    const legend = element('ul', 'bank-pie-legend');
    legend.setAttribute('aria-label', '收入分类金额与占比');
    groups.forEach((group, index) => {
      const row = element('li');
      const dot = element('i', 'bank-pie-dot');
      dot.style.backgroundColor = colors[index % colors.length];
      row.append(dot, element('span', '', group.key), element('span', 'bank-pie-value', `${group.amount_yuan} 元`),
        element('span', 'bank-pie-pct', `${group.pct}%`));
      legend.append(row);
    });
    details.append(legend);
    if (data.income_total != null) {
      const totalNode = element('p', 'bank-pie-total', '收入合计');
      totalNode.append(element('strong', '', `${data.income_total} 元`));
      details.append(totalNode);
    }
    const box = element('div', 'bank-pie-layout bank-pie-static');
    box.append(figure, details);
    return box;
  };
  layout.append(figure, details);
  if ((data.income_groups || []).some(group => Number(String(group.amount_yuan ?? '').replaceAll(',', '')) > 0)) {
    const pair = element('div', 'bank-pie-pair');
    pair.append(layout, incomePie());
    root.append(pair);
  } else {
    root.append(layout);
  }
  highlight(selectedIndex());
  renderLedger(root, data, select, element);
  const focusKey = `bank-pie-focus:${data.animation_id}`;
  let needsFocus = Boolean(data.focus_id);
  try { needsFocus &&= sessionStorage.getItem(focusKey) !== data.focus_id; } catch (_) { /* optional storage */ }
  const focusTarget = needsFocus ? data.focus_target : restoreFocus;
  if (focusTarget) {
    const target = ['ledger', '交易明细，可上下滚动'].includes(focusTarget)
      ? root.querySelector('.bank-pie-ledger-scroll') || root.querySelector('.bank-pie-close')
      : paths.find(item => item.path.getAttribute('aria-label') === focusTarget)?.path;
    requestAnimationFrame(() => {
      target?.focus({ preventScroll: true });
      if (needsFocus) try { sessionStorage.setItem(focusKey, data.focus_id); } catch (_) { /* optional storage */ }
    });
  }
  const storageKey = `bank-pie-played:${data.animation_id}`;
  let played = false;
  try { played = sessionStorage.getItem(storageKey) === '1'; } catch (_) { /* storage may be unavailable */ }
  if (played || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    root.dataset.phase = 'done';
    return;
  }
  const observer = new IntersectionObserver(entries => {
    if (!entries.some(entry => entry.isIntersecting)) return;
    root.dataset.phase = 'playing';
    try { sessionStorage.setItem(storageKey, '1'); } catch (_) { /* no business state is stored */ }
    observer.disconnect();
  }, { threshold: .2 });
  observer.observe(root);
  sweep.onanimationend = () => { root.dataset.phase = 'done'; };
  return () => observer.disconnect();
}
