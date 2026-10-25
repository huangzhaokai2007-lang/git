// All numeric labels are verbatim facts. Arithmetic only positions SVG geometry.
export default function ({ parentElement, data }) {
  const root = parentElement.querySelector('.bank-pie');
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
  const svg = svgElement('svg', { viewBox: '0 0 240 240', role: 'img', 'aria-label': '支出分类饼图' });
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
  const highlight = index => {
    paths.forEach(item => {
      item.path.classList.toggle('is-muted', index !== null && item.index !== index);
      item.path.classList.toggle('is-highlighted', item.index === index);
    });
    entries.forEach(item => item.node.classList.toggle('is-muted', index !== null && item.index !== index));
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
      node.onpointerenter = node.onfocus = () => highlight(index);
      node.onpointerleave = node.onblur = () => highlight(null);
    });
    legend.append(row);
  });
  details.append(legend);
  if (data.total != null) {
    const total = element('p', 'bank-pie-total', '支出合计');
    total.append(element('strong', '', `${data.total} 元`));
    details.append(total);
  }
  layout.append(figure, details);
  root.append(layout);
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
