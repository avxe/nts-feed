import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/youtube-player-global.js'), 'utf8');

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatchEvent(event) {
    const normalized = event;
    normalized.target ||= this;
    normalized.currentTarget = this;
    const listeners = this.listeners.get(normalized.type) || [];
    listeners.forEach((listener) => listener(normalized));
    return !normalized.defaultPrevented;
  }
}

class FakeElement extends FakeEventTarget {
  constructor(tagName) {
    super();
    this.tagName = String(tagName || 'div').toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.style = {};
    this.className = '';
    this.id = '';
    this.textContent = '';
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  insertBefore(child) {
    return this.appendChild(child);
  }

  removeChild(child) {
    this.children = this.children.filter((candidate) => candidate !== child);
    child.parentNode = null;
    return child;
  }

  querySelector() {
    return null;
  }

  get classList() {
    return {
      contains: () => false,
      add() {},
      remove() {},
    };
  }
}

class FakeDocument extends FakeEventTarget {
  constructor() {
    super();
    this.visibilityState = 'visible';
    this.body = new FakeElement('body');
    this.body.ownerDocument = this;
    this._scripts = [new FakeElement('script')];
    this._scripts[0].parentNode = new FakeElement('head');
  }

  createElement(tagName) {
    const element = new FakeElement(tagName);
    element.ownerDocument = this;
    return element;
  }

  getElementsByTagName(tagName) {
    if (String(tagName).toLowerCase() === 'script') {
      return this._scripts;
    }
    return [];
  }

  getElementById() {
    return null;
  }

  querySelector() {
    return null;
  }
}

function createHarness(options = {}) {
  const { sendBeaconResult = true } = options;
  const document = new FakeDocument();
  const window = new FakeEventTarget();
  const beaconCalls = [];
  const fetchCalls = [];

  window.document = document;
  window.location = {
    pathname: '/discover',
    href: 'http://localhost/discover',
  };
  window.crypto = {
    randomUUID() {
      return 'listen-token-1';
    },
  };
  window.fetch = async (...args) => {
    fetchCalls.push(args);
    return { ok: true };
  };
  window.navigator = {
    sendBeacon(url, body) {
      beaconCalls.push({ url, body });
      return sendBeaconResult;
    },
  };
  window.setInterval = () => 1;
  window.clearInterval = () => {};
  window.setTimeout = (fn) => {
    fn();
    return 1;
  };
  window.clearTimeout = () => {};
  window.open = () => {};
  window.console = console;
  window.CustomEvent = class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
      this.defaultPrevented = false;
    }

    preventDefault() {
      this.defaultPrevented = true;
    }
  };
  window.window = window;

  const context = vm.createContext({
    window,
    document,
    navigator: window.navigator,
    fetch: window.fetch,
    console,
    Blob,
    CustomEvent: window.CustomEvent,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval,
    setTimeout: window.setTimeout,
    clearTimeout: window.clearTimeout,
  });

  vm.runInContext(source, context);

  return {
    window,
    beaconCalls,
    fetchCalls,
    tracker: window.NTSListeningTracker,
  };
}

test('closeSession dispatches listening:session-finalized after a meaningful final send', () => {
  const harness = createHarness();
  const events = [];

  harness.window.addEventListener('listening:session-finalized', (event) => {
    events.push(event.detail);
  });

  harness.tracker.beginSession({
    kind: 'episode',
    player: 'youtube',
    episode_url: 'https://www.nts.live/shows/show-alpha/episodes/alpha-new',
    show_url: 'https://www.nts.live/shows/show-alpha',
  });
  harness.tracker.syncProgress({
    current_time: 180,
    duration: 240,
    is_playing: false,
  });

  const sent = harness.tracker.closeSession('ended', {
    current_time: 240,
    duration: 240,
  });

  assert.equal(sent, true);
  assert.equal(harness.beaconCalls.length, 1);
  assert.equal(events.length, 1);
  assert.equal(events[0]?.reason, 'ended');
  assert.equal(events[0]?.sessionToken, 'listen-token-1');
});

test('closeSession does not dispatch listening:session-finalized when nothing meaningful is sent', () => {
  const harness = createHarness();
  const events = [];

  harness.window.addEventListener('listening:session-finalized', (event) => {
    events.push(event.detail);
  });

  harness.tracker.beginSession({
    kind: 'episode',
    player: 'youtube',
    episode_url: 'https://www.nts.live/shows/show-alpha/episodes/alpha-new',
    show_url: 'https://www.nts.live/shows/show-alpha',
  });

  const sent = harness.tracker.closeSession('close', {
    current_time: 0,
    duration: 0,
  });

  assert.equal(sent, false);
  assert.equal(harness.beaconCalls.length, 0);
  assert.equal(events.length, 0);
});
