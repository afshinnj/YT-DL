// Runs the real popup and worker code with mocked Chrome APIs/native host.
// No browser access, network requests, or downloads are performed.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const extensionDir = path.resolve(process.argv[2] || path.join(__dirname, '../chrome_extension'));
const watchUrl = 'https://www.youtube.com/watch?v=k4eYTC4INe4';
const quietEvent = () => ({addListener() {}});

function harness(tabs) {
  let router, nativeReply;
  const nativeActions = [];
  const elements = new Map();
  const chrome = {
    runtime: {
      id: 'test-extension',
      getURL: name => `chrome-extension://test-extension/${name}`,
      onMessage: {addListener(fn) { router = fn; }},
      onConnect: quietEvent(), onInstalled: quietEvent(), onStartup: quietEvent(),
      connectNative() {
        return {
          onMessage: {addListener(fn) { nativeReply = fn; }},
          onDisconnect: quietEvent(),
          postMessage(request) {
            nativeActions.push(request.action);
            const payload = request.action === 'ping'
              ? {status: 'connected'}
              : {title: 'Video metadata received', url: request.url, qualities: []};
            queueMicrotask(() => nativeReply({
              ok: true, request_id: request.request_id, ...payload,
            }));
          },
        };
      },
      sendMessage(message, callback) {
        router(message, {id: chrome.runtime.id, url: chrome.runtime.getURL('popup.html')}, callback);
      },
    },
    tabs: {
      async query(query) {
        return tabs.filter(tab => !query.lastFocusedWindow || tab.windowId === 1);
      },
      onActivated: quietEvent(),
    },
  };
  const common = {chrome, URL, setTimeout, clearTimeout, console: {info() {}, error() {}}};
  const worker = vm.createContext({...common});
  vm.runInContext(fs.readFileSync(path.join(extensionDir, 'service_worker.js'), 'utf8'), worker);
  const events = [];
  const popup = vm.createContext({...common, events, document: {
    addEventListener() {},
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {textContent: '', title: ''});
      return elements.get(id);
    },
  }});
  vm.runInContext(fs.readFileSync(path.join(extensionDir, 'popup.js'), 'utf8'), popup);
  // Replace rendering only; keep the actual RPC/loadTab/pingHost/fetchInfo flow.
  vm.runInContext(`
    showView = name => events.push(['view', name]);
    showUnsupported = detail => events.push(['unsupported', detail]);
    showNotInstalled = () => events.push(['not-installed']);
    showMainError = (code, message) => events.push(['error', code, message]);
    paintEnvironment = () => {};
    setBusy = () => {};
    releaseBusy = () => {};
    paintVideo = info => events.push(['video', info.title]);
    buildPickers = () => {};
  `, popup);
  return {popup, events, nativeActions, elements};
}

test('tab-info preserves URL and supported flag across the worker/popup RPC boundary', async () => {
  const h = harness([{id: 1, windowId: 1, url: watchUrl, title: 'YouTube'}]);
  const info = await vm.runInContext('rpc("tab-info")', h.popup);
  assert.equal(info?.url, watchUrl);
  assert.equal(info?.supported, true);
  assert.deepEqual(h.nativeActions, []);
});

test('opening popup on a watch video reaches ping, get_info, and metadata rendering', async () => {
  const h = harness([{id: 1, windowId: 1, url: watchUrl}]);
  await vm.runInContext('loadTab()', h.popup);
  assert.deepEqual(h.nativeActions, ['ping', 'get_info']);
  assert.equal(h.elements.get('page-url').textContent, watchUrl);
  assert.ok(h.events.some(e => e[0] === 'video' && e[1] === 'Video metadata received'));
});

test('unsupported active page does not select a YouTube tab in another window', async () => {
  const h = harness([
    {id: 2, windowId: 2, url: watchUrl, lastAccessed: 200},
    {id: 1, windowId: 1, url: 'https://example.com/', lastAccessed: 100},
  ]);
  const info = await vm.runInContext('rpc("tab-info")', h.popup);
  assert.equal(info?.url, 'https://example.com/');
  assert.equal(info?.supported, false);
  await vm.runInContext('loadTab()', h.popup);
  assert.deepEqual(h.nativeActions, []);
});

test('no active tab produces the unsupported view without calling the native host', async () => {
  const h = harness([]);
  await vm.runInContext('loadTab()', h.popup);
  assert.deepEqual(h.nativeActions, []);
  assert.ok(h.events.some(e => e[0] === 'unsupported'));
});

function pickerHarness() {
  const elements = new Map();
  const classList = () => ({add() {}, remove() {}, toggle() {}, contains() { return true; }});
  const select = {
    children: [], disabled: true, _value: '',
    get options() { return this.children; },
    get firstChild() { return this.children[0] || null; },
    get value() { return this.children.some(option => option.value === this._value)
      ? this._value : (this.children[0]?.value || ''); },
    set value(value) { this._value = this.children.some(option => option.value === value) ? value : ''; },
    appendChild(option) {
      this.children.push(option);
      if (this.children.length === 1 && !this._value) this._value = option.value;
      return option;
    },
    removeChild(option) {
      this.children.splice(this.children.indexOf(option), 1);
      if (!this.children.some(item => item.value === this._value)) this._value = '';
      return option;
    },
  };
  const segments = ['video', 'audio'].map(type => ({
    dataset: {type}, classList: classList(), addEventListener() {},
  }));
  for (const id of ['quality-panel-title', 'quality-hint', 'start-button']) {
    elements.set(id, {classList: classList(), disabled: true, textContent: ''});
  }
  elements.set('quality-select', select);
  elements.set('type-switch', {querySelectorAll: () => segments});
  const document = {
    addEventListener() {},
    createElement(tag) { return {tagName: tag, value: '', textContent: '', disabled: false, classList: classList()}; },
    getElementById(id) { return elements.get(id) || {classList: classList(), textContent: ''}; },
  };
  const popup = vm.createContext({
    document, chrome: {runtime: {getManifest: () => ({version: '1.0.6'})}},
    URL, setTimeout, clearTimeout, console: {info() {}, error() {}},
  });
  vm.runInContext(fs.readFileSync(path.join(extensionDir, 'popup.js'), 'utf8'), popup);
  return {popup, elements, select};
}

test('quality and audio pickers become enabled and selections map to download parameters', () => {
  const h = pickerHarness();
  h.popup.__pickerInfo = {
    qualities: [
      {quality: 'best', label: 'Best available', available: true},
      {quality: '1080', label: '1080p', available: true},
      {quality: '720', label: '720p', available: true},
    ],
    audio_formats: [
      {format: 'best', label: 'Original', available: true},
      {format: 'm4a', label: 'M4A', available: true},
      {format: 'mp3', label: 'MP3', available: true},
    ],
  };
  vm.runInContext('lastInfo = __pickerInfo; buildPickers(lastInfo)', h.popup);
  assert.equal(h.select.disabled, false);
  assert.equal(h.select.value, '1080');
  h.select.value = '720';
  assert.deepEqual({...vm.runInContext('currentSelection()', h.popup)}, {media_type: 'video', quality: '720'});

  vm.runInContext('applyType("audio")', h.popup);
  assert.equal(h.select.disabled, false);
  assert.equal(h.select.value, 'best');
  h.select.value = 'mp3';
  assert.deepEqual({...vm.runInContext('currentSelection()', h.popup)}, {media_type: 'audio', audio_format: 'mp3'});
});

test('picker stays disabled when the video has no selectable stream', () => {
  const h = pickerHarness();
  h.popup.__pickerInfo = {qualities: [], audio_formats: []};
  vm.runInContext('lastInfo = __pickerInfo; buildPickers(lastInfo)', h.popup);
  assert.equal(h.select.disabled, true);
  vm.runInContext('applyType("audio")', h.popup);
  assert.equal(h.select.disabled, true);
});

