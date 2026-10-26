const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.textContent = '';
    this.className = '';
    this.classList = { toggle: (name, on) => {
      const classes = new Set(this.className.split(' ').filter(Boolean));
      if (on) classes.add(name); else classes.delete(name);
      this.className = [...classes].join(' ');
    } };
  }
  setAttribute(key, value) { this.attrs[key] = value; if (key === 'class') this.className = value; }
  getAttribute(key) { return this.attrs[key]; }
  append(...nodes) { nodes.forEach(n => { n.parent = this; }); this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  contains(node) { return walk(this).includes(node); }
  querySelector(selector) { return byClass(this, selector.slice(1))[0] || null; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); }
  focus() { context.document.activeElement = this; }
}
const walk = root => [root, ...root.children.flatMap(walk)];
const byClass = (root, cls) => walk(root).filter(node => node.className.split(' ').includes(cls));
const storage = new Map();
const observers = [];
let reduced = false;
const context = {
  document: { createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag) },
  window: { matchMedia: () => ({ matches: reduced }) },
  sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
  requestAnimationFrame: callback => callback(),
  IntersectionObserver: class {
    constructor(callback, options) { this.callback = callback; this.options = options; observers.push(this); }
    observe(root) { this.root = root; this.connected = true; }
    disconnect() { this.connected = false; }
  },
};
vm.createContext(context);
const source = fs.readFileSync('interfaces/web/spending_pie.js', 'utf8');
vm.runInContext(source.replace('export default function', 'function render') + '\nthis.render = render;', context);
const root = new Element('section');
const events = [];
const mount = data => context.render({ parentElement: { querySelector: () => root }, data,
  setStateValue: (key, value) => events.push({ key, value }) });
const data = { animation_id: 'reply-1', total: '9,152.00', groups: [
  { key: '电商', amount_yuan: '4,370.00', pct: 48 },
  { key: '<img onerror=alert(1)>', amount_yuan: '52.00', pct: 0 },
] };
const cleanup = mount(data);
assert.equal(root.dataset.phase, 'waiting');
assert.equal(byClass(root, 'bank-pie-slice').length, 2, 'positive amounts must remain visible even when rounded pct is 0');
assert.equal(observers.length, 1);
assert.equal(observers[0].options.threshold, .2);
observers[0].callback([{ isIntersecting: false }]);
assert.equal(root.dataset.phase, 'waiting', 'offscreen pies do not animate');
observers[0].callback([{ isIntersecting: true }]);
assert.equal(root.dataset.phase, 'playing');
assert.equal(observers[0].connected, false);
const legend = byClass(root, 'bank-pie-legend')[0];
assert.equal(legend.children[1].children[1].textContent, '<img onerror=alert(1)>');
assert(!walk(root).some(node => node.tagName === 'img'), 'labels must never become HTML');
assert.equal(legend.children[1].children[2].textContent, '52.00 元');
assert.equal(legend.children[1].children[3].textContent, '0%');
legend.children[0].onpointerenter();
assert(byClass(root, 'bank-pie-slice')[1].className.includes('is-muted'));
legend.children[0].onpointerleave();
assert(!byClass(root, 'bank-pie-slice')[1].className.includes('is-muted'));
byClass(root, 'bank-pie-sweep')[0].onanimationend();
assert.equal(root.dataset.phase, 'done');
cleanup();
mount(data);
assert.equal(root.dataset.phase, 'done', 'rerenders must not replay a seen animation');
assert.equal(observers.length, 1);
mount({ animation_id: 'zero', groups: [{ key: '空', amount_yuan: '0.00', pct: 0 }] });
assert.equal(root.dataset.phase, 'done');
assert.equal(root.children[0].textContent, '该账期暂无支出分类。');
assert(!walk(root).some(node => node.tagName === 'svg'));
reduced = true;
mount({ animation_id: 'single', groups: [{ key: '餐饮', amount_yuan: '1.00', pct: 100 }] });
assert.equal(root.dataset.phase, 'done');
assert.equal(byClass(root, 'bank-pie-slice')[0].tagName, 'circle', 'one category fills a complete circle');
assert.equal(observers.length, 1, 'reduced motion must skip animation observers');
mount(data);
byClass(root, 'bank-pie-slice')[0].onclick();
assert.equal(events.at(-1).key, 'selected');
assert.equal(events.at(-1).value, '电商');
const detail = { ok: true, count: 8, amount_yuan: '4,370.00', items: Array.from({ length: 8 }, (_, i) => ({
  id: `txn-${i}`, ts: '2026-08-05T12:00:00', counterparty: '<img onerror=alert(1)>',
  channel: '卡', amount_yuan: '-546.25', memo: '订单说明' })) };
mount({ ...data, selected: '电商', detail });
assert.equal(byClass(root, 'bank-pie-ledger').length, 1);
assert.equal(byClass(root, 'bank-pie-txn').length, 8, 'every returned transaction stays available for scrolling');
assert(!walk(root).some(node => node.tagName === 'img'), 'transaction labels must also be safe text');
assert.equal(byClass(root, 'bank-pie-slice')[0].attrs['aria-pressed'], 'true');
const legendNow = byClass(root, 'bank-pie-legend')[0];
let prevented = false;
legendNow.children[1].onkeydown({ key: 'Enter', preventDefault: () => { prevented = true; } });
assert(prevented);
assert.equal(events.at(-1).value.category, '<img onerror=alert(1)>', 'keyboard switches category');
const keyboardSelection = events.at(-1).value;
mount({ ...data, ...keyboardSelection, selected: keyboardSelection.category, detail });
assert.equal(context.document.activeElement, byClass(root, 'bank-pie-ledger-scroll')[0], 'keyboard focus enters loaded ledger');
byClass(root, 'bank-pie-close')[0].onclick({ detail: 0 });
const closeSelection = events.at(-1).value;
mount({ ...data, ...closeSelection, selected: null });
assert.equal(context.document.activeElement, byClass(root, 'bank-pie-slice')[1], 'keyboard collapse returns focus to the selected slice');
mount({ ...data, selected: '电商', detail });
byClass(root, 'bank-pie-slice')[0].onclick();
assert.equal(events.at(-1).value, null, 'clicking the same slice collapses its ledger');
mount({ ...data, selected: '电商', detail: { ok: true, count: 0, amount_yuan: '0.00', items: [] } });
assert(walk(root).some(node => node.textContent === '该分类在此账期暂无支出明细。'));
mount({ ...data, selected: '电商', detail: { ok: false, message: '查询暂不可用' } });
assert(walk(root).some(node => node.textContent === '查询暂不可用'));
console.log('spending pie lifecycle, safe labels, hover, and edge cases passed');
