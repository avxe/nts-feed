import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/global-search.js'), 'utf8');

class FakeClassList {
  constructor(owner) {
    this.owner = owner;
    this.tokens = new Set();
  }

  setFromString(value) {
    this.tokens = new Set(String(value || '').split(/\s+/).filter(Boolean));
    this.#sync();
  }

  add(...tokens) {
    tokens.filter(Boolean).forEach((token) => this.tokens.add(token));
    this.#sync();
  }

  remove(...tokens) {
    tokens.forEach((token) => this.tokens.delete(token));
    this.#sync();
  }

  toggle(token, force) {
    if (force === true) {
      this.tokens.add(token);
    } else if (force === false) {
      this.tokens.delete(token);
    } else if (this.tokens.has(token)) {
      this.tokens.delete(token);
    } else {
      this.tokens.add(token);
    }
    this.#sync();
    return this.tokens.has(token);
  }

  contains(token) {
    return this.tokens.has(token);
  }

  toString() {
    return Array.from(this.tokens).join(' ');
  }

  #sync() {
    this.owner._className = this.toString();
  }
}

class FakeElement {
  constructor(tagName) {
    this.tagName = String(tagName || 'div').toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.classList = new FakeClassList(this);
    this._className = '';
    this.id = '';
    this.value = '';
    this.innerHTML = '';
  }

  get className() {
    return this._className;
  }

  set className(value) {
    this.classList.setFromString(value);
  }

  setAttribute(name, value) {
    const normalized = String(value);
    this.attributes[name] = normalized;
    if (name === 'id') {
      this.id = normalized;
      return;
    }
    if (name === 'class') {
      this.className = normalized;
      return;
    }
    if (name.startsWith('data-')) {
      const key = name
        .slice(5)
        .replace(/-([a-z])/g, (_, char) => char.toUpperCase());
      this.dataset[key] = normalized;
    }
  }

  getAttribute(name) {
    if (name === 'id') return this.id || null;
    if (name === 'class') return this.className || null;
    if (name.startsWith('data-')) {
      const key = name
        .slice(5)
        .replace(/-([a-z])/g, (_, char) => char.toUpperCase());
      return this.dataset[key] ?? null;
    }
    return this.attributes[name] ?? null;
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
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

  focus() {}

  contains(node) {
    let current = node;
    while (current) {
      if (current === this) return true;
      current = current.parentElement;
    }
    return false;
  }

  closest(selector) {
    let current = this;
    while (current) {
      if (current.matches(selector)) return current;
      current = current.parentElement;
    }
    return null;
  }

  matches(selector) {
    if (!selector) return false;
    if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    return this.tagName.toLowerCase() === selector.toLowerCase();
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    const selectors = String(selector)
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
    const matches = [];
    const visit = (node) => {
      node.children.forEach((child) => {
        if (selectors.some((item) => child.matches(item))) {
          matches.push(child);
        }
        visit(child);
      });
    };
    visit(this);
    return matches;
  }
}

class FakeDocument {
  constructor() {
    this.body = new FakeElement('body');
    this.listeners = new Map();
  }

  createElement(tagName) {
    return new FakeElement(tagName);
  }

  getElementById(id) {
    return this.body.querySelector(`#${id}`);
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
  }
}

function createKeyboardEvent(key) {
  return {
    type: 'keydown',
    key,
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };
}

function createInputEvent() {
  return { type: 'input' };
}

async function flushAsyncWork() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

function createHarness(options = {}) {
  const { fetchResponse = null } = options;
  const navigations = [];
  let fetchCallCount = 0;
  const document = new FakeDocument();
  const searchBox = document.createElement('div');
  searchBox.className = 'search-box';

  const inputContainer = document.createElement('div');
  inputContainer.className = 'search-input-container';
  const input = document.createElement('input');
  input.id = 'globalSearch';
  input.className = 'search-input';
  inputContainer.appendChild(input);

  const clearButton = document.createElement('button');
  clearButton.className = 'search-clear';

  searchBox.appendChild(inputContainer);
  searchBox.appendChild(clearButton);
  document.body.appendChild(searchBox);

  const window = {
    document,
    location: { href: 'http://localhost/' },
    SPARouter: {
      navigate(href) {
        navigations.push(href);
      },
    },
  };

  const context = vm.createContext({
    window,
    document,
    console,
    fetch: fetchResponse
      ? async () => {
        fetchCallCount += 1;
        return ({
        ok: true,
        async json() {
          return fetchResponse;
        },
      });
      }
      : async () => {
        throw new Error('fetch should not run in this test');
      },
    setTimeout(fn) {
      fn();
      return 1;
    },
    clearTimeout() {},
    AbortController: class AbortController {
      constructor() {
        this.signal = {};
      }
      abort() {}
    },
  });

  vm.runInContext(source, context);
  document.dispatchEvent({ type: 'DOMContentLoaded' });

  const dropdown = searchBox.querySelector('.global-search-dropdown');

  return {
    document,
    input,
    dropdown,
    navigations,
    getFetchCallCount() {
      return fetchCallCount;
    },
    setOptions(options) {
      dropdown.children = [];
      options.forEach((option) => {
        const element = document.createElement('button');
        element.className = 'global-search-item';
        element.setAttribute('data-href', option.href);
        dropdown.appendChild(element);
      });
    },
  };
}

test('pressing Enter with a query opens the full search page even before suggestions render', () => {
  const harness = createHarness();
  harness.input.value = 'miles davis';

  harness.input.dispatchEvent(createKeyboardEvent('Enter'));

  assert.deepEqual(harness.navigations, ['/search?q=miles%20davis']);
});

test('pressing Enter on the active suggestion opens that suggestion', () => {
  const harness = createHarness();
  harness.input.value = 'miles davis';
  harness.setOptions([
    { href: '/show/alpha' },
    { href: '/search?q=miles%20davis&types=tracks' },
  ]);

  harness.input.dispatchEvent(createKeyboardEvent('ArrowDown'));
  harness.input.dispatchEvent(createKeyboardEvent('Enter'));

  assert.deepEqual(harness.navigations, ['/show/alpha']);
});

test('track suggestions deep-link to the matched episode instead of generic track search', async () => {
  const harness = createHarness({
    fetchResponse: {
      shows: [],
      episodes: [],
      tracks: [{
        title: 'All Of Me',
        artists: ['Billie Holiday'],
        episodes: [{
          episode_title: 'Leila Samir',
          episode_url: 'https://www.nts.live/shows/leila-samir/episodes/leila-samir-14th-december-2025',
          show_title: 'Leila Samir',
          show_url: 'https://www.nts.live/shows/leila-samir',
        }],
      }],
      artists: [],
      genres: [],
    },
  });

  harness.input.value = 'all of me';
  harness.input.dispatchEvent(createInputEvent());
  await flushAsyncWork();

  assert.match(
    harness.dropdown.innerHTML,
    /\/show\/https%3A%2F%2Fwww\.nts\.live%2Fshows%2Fleila-samir#ep=https%3A%2F%2Fwww\.nts\.live%2Fshows%2Fleila-samir%2Fepisodes%2Fleila-samir-14th-december-2025/,
  );
  assert.match(harness.dropdown.innerHTML, /track=All%20Of%20Me/);
  assert.match(harness.dropdown.innerHTML, /artist=Billie%20Holiday/);
  assert.doesNotMatch(harness.dropdown.innerHTML, /\/search\?q=Billie%20Holiday%20All%20Of%20Me&amp;types=tracks/);
});

test('spa page changes do not rebind duplicate global-search input listeners', async () => {
  const harness = createHarness({
    fetchResponse: {
      shows: [],
      episodes: [],
      tracks: [],
      artists: [],
      genres: [],
    },
  });

  harness.input.value = 'boards of canada';
  harness.input.dispatchEvent(createInputEvent());
  await flushAsyncWork();
  assert.equal(harness.getFetchCallCount(), 1);

  harness.input.value = 'burial';
  harness.input.dispatchEvent(createInputEvent());
  await flushAsyncWork();

  const baselineFetchCount = harness.getFetchCallCount();

  harness.document.dispatchEvent({ type: 'spa:pagechange', detail: { path: '/discover' } });
  harness.document.dispatchEvent({ type: 'spa:pagechange', detail: { path: '/likes' } });

  harness.input.value = 'burial';
  harness.input.dispatchEvent(createInputEvent());
  await flushAsyncWork();

  assert.equal(harness.getFetchCallCount(), baselineFetchCount + 1);
});
