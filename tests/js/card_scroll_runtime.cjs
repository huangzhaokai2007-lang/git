const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const frames = new Map();
const listeners = {};
let sequence = 0;
let mutation;
let resize;
let scrollRequest;
const tile = {
  style: {}, offsetHeight: 300,
  querySelector: () => ({dataset: {card: 'card-1'}}),
  setAttribute() {},
};
const marker = {dataset: {selected: '', revision: '1'}};
const root = {
  isConnected: true, clientWidth: 900, style: {}, dataset: {},
  querySelectorAll: () => [tile], querySelector: () => marker,
  contains: node => node === root || node === tile,
  scrollIntoView: options => { scrollRequest = options; },
};
const reduced = {matches: false, addEventListener() {}, removeEventListener() {}};
const sandbox = {
  document: {
    body: {}, querySelector: () => root,
    addEventListener: (type, callback) => { listeners[type] = callback; },
    removeEventListener() {},
  },
  window: {matchMedia: () => reduced, addEventListener() {}, removeEventListener() {}},
  MutationObserver: class {
    constructor(callback) { mutation = callback; }
    observe() {} disconnect() {}
  },
  ResizeObserver: class {
    constructor(callback) { resize = callback; }
    observe() {} disconnect() {}
  },
  requestAnimationFrame: callback => { frames.set(++sequence, callback); return sequence; },
  cancelAnimationFrame: id => frames.delete(id),
  setTimeout: () => 1, clearTimeout() {},
};
const source = fs.readFileSync('interfaces/web/card_focus.js', 'utf8')
  .replace('export default function ()', '(function ()') + ')';
const cleanup = vm.runInNewContext(source, sandbox)();
function flush() {
  const pending = [...frames.values()];
  frames.clear();
  pending.forEach(callback => callback());
}
flush();
assert.equal(root.style.transition, 'none', 'Card animation must not continuously resize the page');

mutation([{target: {}, addedNodes: [], removedNodes: []}]);
assert.equal(frames.size, 0, 'Unrelated chat updates must not remeasure the gallery');
resize([{target: root, contentRect: {width: 900, height: 300}}]);
assert.equal(frames.size, 0, 'Gallery height animation must not schedule its own layout');
resize([{target: root, contentRect: {width: 700, height: 300}}]);
assert.equal(frames.size, 1, 'Sidebar width changes must still update card layout');
flush();
for (const target of [root, tile]) {
  mutation([{target, addedNodes: [], removedNodes: []}]);
  assert.equal(frames.size, 1, 'Selection, content and status changes must still update layout');
  flush();
}

const button = {closest: selector => selector.includes('bank-tile') ? tile : button};
listeners.click({target: {closest: () => button}});
assert.equal(scrollRequest.behavior, 'auto', 'Card focus must not start a smooth scroll');
assert.equal(scrollRequest.block, 'nearest', 'Card focus must preserve nearby reading position');
cleanup();
assert.equal(frames.size, 0);
