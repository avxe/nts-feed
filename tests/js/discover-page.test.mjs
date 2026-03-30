import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/discover-page.js'), 'utf8');

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
    this.ownerDocument = null;
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.classList = new FakeClassList(this);
    this._className = '';
    this.id = '';
    this._innerHTML = '';
    this._textContent = '';
  }

  get className() {
    return this._className;
  }

  set className(value) {
    this.classList.setFromString(value);
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(value) {
    this._innerHTML = String(value);
    this.children = [];
    this.#hydrateInnerHTML(this._innerHTML);
  }

  get textContent() {
    return [this._textContent, ...this.children.map((child) => child.textContent)].join('');
  }

  set textContent(value) {
    this._textContent = String(value);
  }

  #hydrateInnerHTML(html) {
    const source = String(html || '');
    const tokenPattern = /<\/?([a-zA-Z0-9]+)\b([^>]*)>/g;
    const stack = [this];
    let lastIndex = 0;
    let match;

    while ((match = tokenPattern.exec(source))) {
      const [token, tagName, attrSource] = match;
      this.#appendText(stack[stack.length - 1], source.slice(lastIndex, match.index));

      if (token.startsWith('</')) {
        const normalizedTag = tagName.toLowerCase();
        for (let index = stack.length - 1; index > 0; index -= 1) {
          if (stack[index].tagName.toLowerCase() === normalizedTag) {
            stack.length = index;
            break;
          }
        }
      } else {
        const child = this.ownerDocument ? this.ownerDocument.createElement(tagName) : new FakeElement(tagName);
        child.ownerDocument = this.ownerDocument;
        this.#applyAttributes(child, attrSource);
        stack[stack.length - 1].appendChild(child);

        const normalizedTag = tagName.toLowerCase();
        if (!this.#isVoidTag(normalizedTag) && !token.endsWith('/>')) {
          stack.push(child);
        }
      }

      lastIndex = tokenPattern.lastIndex;
    }

    this.#appendText(stack[stack.length - 1], source.slice(lastIndex));
  }

  #appendText(element, value) {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (!text) return;
    element._textContent = element._textContent ? `${element._textContent} ${text}` : text;
  }

  #applyAttributes(element, attrSource) {
    const pattern = /([a-zA-Z0-9:-]+)=["']([^"']*)["']/g;
    let match;

    while ((match = pattern.exec(String(attrSource || '')))) {
      const [, name, value] = match;
      element.setAttribute(name, value);
    }
  }

  #isVoidTag(tagName) {
    return new Set(['img', 'br', 'hr', 'input', 'meta', 'link', 'source']).has(tagName);
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
    child.ownerDocument = this.ownerDocument;
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
    if (normalized.bubbles === undefined) {
      normalized.bubbles = true;
    }
    normalized.target ||= this;
    normalized.currentTarget = this;
    if (typeof normalized.stopPropagation !== 'function') {
      normalized.stopPropagation = function stopPropagation() {
        this.cancelBubble = true;
        this._stopped = true;
      };
    }

    let current = this;
    while (current) {
      normalized.currentTarget = current;
      const listeners = current.listeners.get(normalized.type) || [];
      listeners.forEach((listener) => listener(normalized));
      if (normalized._stopped || normalized.bubbles === false) {
        break;
      }
      current = current.parentElement;
    }

    if (!normalized._stopped && normalized.bubbles !== false && this.ownerDocument) {
      normalized.currentTarget = this.ownerDocument;
      const listeners = this.ownerDocument.listeners.get(normalized.type) || [];
      listeners.forEach((listener) => listener(normalized));
    }

    return !normalized.defaultPrevented;
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
    const normalized = String(selector || '').trim();
    if (!normalized) return false;
    if (normalized.startsWith('#')) return this.id === normalized.slice(1);
    if (normalized.startsWith('.')) return this.classList.contains(normalized.slice(1));
    if (normalized.startsWith('[') && normalized.endsWith(']')) {
      const raw = normalized.slice(1, -1);
      const [attr, quotedValue] = raw.split('=');
      const attributeName = attr.trim();
      if (quotedValue === undefined) {
        return this.getAttribute(attributeName) !== null;
      }
      const expected = quotedValue.trim().replace(/^['"]|['"]$/g, '');
      return this.getAttribute(attributeName) === expected;
    }
    return this.tagName.toLowerCase() === normalized.toLowerCase();
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
    this.visibilityState = 'visible';
  }

  createElement(tagName) {
    return new FakeElement(tagName);
  }

  getElementById(id) {
    return this.body.querySelector(`#${id}`);
  }

  querySelector(selector) {
    return this.body.querySelector(selector);
  }

  querySelectorAll(selector) {
    return this.body.querySelectorAll(selector);
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

async function flushAsyncWork() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

function createEvent(type) {
  return {
    type,
    bubbles: true,
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };
}

function createCustomEvent(type, detail = {}) {
  return {
    type,
    detail,
    bubbles: false,
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };
}

function append(parent, tagName, options = {}) {
  const el = parent.ownerDocument ? parent.ownerDocument.createElement(tagName) : new FakeElement(tagName);
  el.ownerDocument = parent.ownerDocument || null;
  if (options.id) el.id = options.id;
  if (options.className) el.className = options.className;
  if (options.dataset) {
    Object.entries(options.dataset).forEach(([key, value]) => {
      el.dataset[key] = String(value);
    });
  }
  if (options.textContent) el.textContent = options.textContent;
  parent.appendChild(el);
  return el;
}

function buildHarness() {
  const document = new FakeDocument();
  document.body.ownerDocument = document;
  const registeredPages = new Map();

  const page = append(document.body, 'div', { dataset: { discoverPage: 'true' } });
  const loading = append(page, 'div', { id: 'discoverLoading' });
  const empty = append(page, 'div', { id: 'discoverEmpty' });
  const content = append(page, 'div', { id: 'discoverContent' });
  const meta = append(page, 'div', { id: 'discoverMeta' });
  const surpriseBtn = append(page, 'button', { id: 'discoverSurpriseBtn' });
  const addShowBtn = append(page, 'button', { id: 'addShowBtn' });

  const nextUpTab = append(page, 'button', { id: 'discoverNextUpTab', dataset: { mode: 'next-up' } });
  const exploreTab = append(page, 'button', { id: 'discoverExploreTab', dataset: { mode: 'explore' } });
  const nextUpContent = append(page, 'div', { id: 'discoverNextUpContent' });
  const continueListening = append(nextUpContent, 'section', { id: 'discoverContinueListeningSection' });
  const continueListeningList = append(continueListening, 'div', { id: 'discoverContinueListening' });
  const playNext = append(nextUpContent, 'section', { id: 'discoverPlayNextSection' });
  const playNextList = append(playNext, 'div', { id: 'discoverPlayNext' });
  const curiosityBridges = append(nextUpContent, 'section', { id: 'discoverCuriosityBridgesSection' });
  const curiosityBridgesList = append(curiosityBridges, 'div', { id: 'discoverCuriosityBridges' });
  const savedForLater = append(nextUpContent, 'section', { id: 'discoverSavedForLaterSection' });
  const savedForLaterList = append(savedForLater, 'div', { id: 'discoverSavedForLater' });
  nextUpContent.classList.add('hidden');
  continueListening.classList.add('hidden');
  playNext.classList.add('hidden');
  curiosityBridges.classList.add('hidden');
  savedForLater.classList.add('hidden');

  const discoverSurpriseSection = append(page, 'section', { id: 'discoverSurpriseSection' });
  const discoverSurprise = append(discoverSurpriseSection, 'div', { id: 'discoverSurprise' });
  const discoverLatestSection = append(page, 'section', { id: 'discoverLatestSection' });
  const discoverLatest = append(discoverLatestSection, 'div', { id: 'discoverLatest' });
  const discoverBecauseSection = append(page, 'section', { id: 'discoverBecauseSection' });
  const discoverBecause = append(discoverBecauseSection, 'div', { id: 'discoverBecause' });
  const discoverGenresSection = append(page, 'section', { id: 'discoverGenresSection' });
  const discoverGenres = append(discoverGenresSection, 'div', { id: 'discoverGenres' });

  const fetchCalls = [];
  const nextUpPayload = {
    success: true,
    sections: {
      continue_listening: [
        {
          episode_title: 'Alpha New',
          episode_url: 'https://www.nts.live/shows/show-alpha/episodes/alpha-new',
          show_title: 'Show Alpha',
          show_url: 'https://www.nts.live/shows/show-alpha',
          episode_date: 'March 10, 2026',
          reason_label: 'Continue listening',
          matched_genres: ['House'],
          actions: [
            { action: 'play', label: 'Play next' },
          ],
        },
      ],
      play_next: [
        {
          episode_title: 'Bridge Set',
          episode_url: 'https://www.nts.live/shows/show-bridge/episodes/bridge-set',
          show_title: 'Show Bridge',
          show_url: 'https://www.nts.live/shows/show-bridge',
          episode_date: 'March 14, 2026',
          reason_label: 'Bridge between House and Ambient',
          matched_genres: ['House', 'Ambient'],
          actions: [
            { action: 'dismiss', label: 'Dismiss' },
            { action: 'save', label: 'Save for later' },
          ],
        },
      ],
      curiosity_bridges: [
        {
          episode_title: 'Bridge Set',
          episode_url: 'https://www.nts.live/shows/show-bridge/episodes/bridge-set',
          show_title: 'Show Bridge',
          show_url: 'https://www.nts.live/shows/show-bridge',
          episode_date: 'March 14, 2026',
          reason_label: 'Bridge from your listening history',
          matched_genres: ['House', 'Ambient'],
          actions: [
            { action: 'dismiss', label: 'Dismiss' },
            { action: 'snooze', label: 'Snooze' },
          ],
        },
      ],
      saved_for_later: [
        {
          episode_title: 'Bridge Set',
          episode_url: 'https://www.nts.live/shows/show-bridge/episodes/bridge-set',
          show_title: 'Show Bridge',
          show_url: 'https://www.nts.live/shows/show-bridge',
          episode_date: 'March 14, 2026',
          reason_label: 'Saved for later',
          matched_genres: ['House', 'Ambient'],
          actions: [
            { action: 'unsave', label: 'Remove' },
            { action: 'dismiss', label: 'Dismiss' },
          ],
        },
      ],
    },
  };

  const window = {
    listeners: new Map(),
    document,
    location: {
      pathname: '/discover',
      href: 'http://localhost/discover',
    },
    console,
    showNotification() {},
    openSubscribeModal() {},
    NTSPageModules: {
      register(name, definition) {
        registeredPages.set(name, definition);
      },
    },
    setTimeout(fn) {
      fn();
      return 1;
    },
    clearTimeout() {},
    addEventListener(type, listener) {
      const listeners = this.listeners.get(type) || [];
      listeners.push(listener);
      this.listeners.set(type, listeners);
    },
    dispatchEvent(event) {
      const normalized = event;
      normalized.target ||= this;
      normalized.currentTarget = this;
      const listeners = this.listeners.get(normalized.type) || [];
      listeners.forEach((listener) => listener(normalized));
      return !normalized.defaultPrevented;
    },
    fetch: async (url, options = {}) => {
      fetchCalls.push({
        url,
        method: options.method || 'GET',
      });

      if (url === '/api/discover') {
        return {
          ok: true,
          async json() {
            return {
              success: true,
              sections: {
                new_from_your_shows: [],
                because_you_like: [],
                by_genre: [],
              },
              surprise_episode: null,
            };
          },
        };
      }

      if (url === '/api/discover/next-up') {
        return {
          ok: true,
          async json() {
            return nextUpPayload;
          },
        };
      }

      if (url === '/api/discover/next-up/state') {
        return {
          ok: true,
          async json() {
            return { success: true };
          },
        };
      }

      throw new Error(`unexpected fetch: ${url}`);
    },
  };
  window.window = window;

  const context = vm.createContext({
    window,
    document,
    console,
    fetch: window.fetch,
    setTimeout: window.setTimeout,
    clearTimeout: window.clearTimeout,
    AbortController: class AbortController {
      constructor() {
        this.signal = {};
      }
      abort() {}
    },
  });

  vm.runInContext(source, context);
  document.dispatchEvent(createEvent('DOMContentLoaded'));

  return {
    window,
    document,
    fetchCalls,
    nextUpTab,
    exploreTab,
    nextUpContent,
    continueListening,
    continueListeningList,
    playNext,
    playNextList,
    curiosityBridges,
    curiosityBridgesList,
    savedForLater,
    savedForLaterList,
    loading,
    empty,
    content,
    meta,
    surpriseBtn,
    addShowBtn,
    discoverLatest,
    discoverBecause,
    discoverGenres,
    discoverSurprise,
    nextUpPayload,
    registeredPages,
  };
}

// Legacy: harness expects Next Up tabs/sections removed from the unified Discover page.
test.skip('discover page loads the next-up contract and exposes Next Up mode', async () => {
  const harness = buildHarness();
  harness.nextUpTab.dispatchEvent(createEvent('click'));
  await flushAsyncWork();

  assert.match(harness.fetchCalls.map((call) => call.url).join('\n'), /\/api\/discover\/next-up/);
  assert.equal(harness.nextUpContent.classList.contains('hidden'), false);
  assert.deepEqual(
    [
      harness.continueListening.querySelector('.section-title')?.textContent.trim(),
      harness.playNext.querySelector('.section-title')?.textContent.trim(),
      harness.curiosityBridges.querySelector('.section-title')?.textContent.trim(),
      harness.savedForLater.querySelector('.section-title')?.textContent.trim(),
    ],
    ['Continue listening', 'Play next', 'Curiosity bridges', 'Saved for later'],
  );
  assert.ok(harness.continueListeningList.querySelectorAll('button').length >= 1);
  assert.ok(harness.playNextList.querySelectorAll('button').length >= 1);
  assert.ok(harness.curiosityBridgesList.querySelectorAll('button').length >= 1);
  assert.ok(harness.savedForLaterList.querySelectorAll('button').length >= 1);
  assert.ok(harness.savedForLaterList.querySelector('[data-action="unsave"]'));
});

test.skip('dismissing a next-up card posts state and removes it optimistically', async () => {
  const harness = buildHarness();
  harness.nextUpTab.dispatchEvent(createEvent('click'));
  await flushAsyncWork();

  const dismissButton = harness.savedForLaterList.querySelector('[data-action="dismiss"]');
  assert.ok(dismissButton);
  const episodeUrl = dismissButton.getAttribute('data-episode-url');

  dismissButton.dispatchEvent(createEvent('click'));
  await flushAsyncWork();

  assert.match(harness.fetchCalls.map((call) => `${call.method} ${call.url}`).join('\n'), /POST \/api\/discover\/next-up\/state/);
  assert.equal(harness.savedForLaterList.querySelector(`[data-episode-url="${episodeUrl}"]`), null);
  assert.equal(harness.savedForLaterList.querySelector('[data-action="dismiss"]'), null);
});

test.skip('listening finalization refreshes next-up immediately when Next Up is active', async () => {
  const harness = buildHarness();
  harness.nextUpTab.dispatchEvent(createEvent('click'));
  await flushAsyncWork();

  const initialNextUpRequests = harness.fetchCalls.filter((call) => call.url === '/api/discover/next-up').length;
  harness.window.dispatchEvent(createCustomEvent('listening:session-finalized', {
    reason: 'ended',
    sessionToken: 'listen-token-1',
  }));
  await flushAsyncWork();

  assert.equal(
    harness.fetchCalls.filter((call) => call.url === '/api/discover/next-up').length,
    initialNextUpRequests + 1,
  );
});

test.skip('listening finalization defers refresh until Next Up is reopened', async () => {
  const harness = buildHarness();
  harness.nextUpTab.dispatchEvent(createEvent('click'));
  await flushAsyncWork();
  harness.exploreTab.dispatchEvent(createEvent('click'));
  await flushAsyncWork();

  const initialNextUpRequests = harness.fetchCalls.filter((call) => call.url === '/api/discover/next-up').length;
  harness.window.dispatchEvent(createCustomEvent('listening:session-finalized', {
    reason: 'close',
    sessionToken: 'listen-token-2',
  }));
  await flushAsyncWork();

  assert.equal(
    harness.fetchCalls.filter((call) => call.url === '/api/discover/next-up').length,
    initialNextUpRequests,
  );

  harness.nextUpTab.dispatchEvent(createEvent('click'));
  await flushAsyncWork();

  assert.equal(
    harness.fetchCalls.filter((call) => call.url === '/api/discover/next-up').length,
    initialNextUpRequests + 1,
  );
});

test('discover episode cards use tile + grid classes for poster layout', () => {
  assert.ok(source.includes('discover-episode-card'));
  assert.ok(source.includes('discover-episode-card--tile'));
  assert.ok(source.includes('discover-episode-grid'));
  assert.ok(source.includes('discover-episode-thumb-play'));
});

test('discover page registers a single init/cleanup page module contract', () => {
  const harness = buildHarness();
  const discoverModule = harness.registeredPages.get('discover');

  assert.ok(discoverModule);
  assert.equal(typeof discoverModule.init, 'function');
  assert.equal(typeof discoverModule.cleanup, 'function');
  assert.equal(harness.registeredPages.get('mixtape'), discoverModule);
});
