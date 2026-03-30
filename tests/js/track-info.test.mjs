import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/track-info.js'), 'utf8');

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

  contains(token) {
    return this.tokens.has(token);
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
    this._innerHTML = '';
    this._textContent = '';
    this.id = '';
    this.nodeType = 1;
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
    const sourceText = String(html || '');
    const tokenPattern = /<\/?([a-zA-Z0-9]+)\b([^>]*)>/g;
    const stack = [this];
    let lastIndex = 0;
    let match;

    while ((match = tokenPattern.exec(sourceText))) {
      const [token, tagName, attrSource] = match;
      this.#appendText(stack[stack.length - 1], sourceText.slice(lastIndex, match.index));

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

    this.#appendText(stack[stack.length - 1], sourceText.slice(lastIndex));
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

  hasAttribute(name) {
    return this.getAttribute(name) !== null;
  }

  appendChild(child) {
    child.parentElement = this;
    child.ownerDocument = this.ownerDocument;
    this.children.push(child);
    return child;
  }

  insertBefore(child, beforeChild) {
    child.parentElement = this;
    child.ownerDocument = this.ownerDocument;
    if (!beforeChild) {
      this.children.push(child);
      return child;
    }
    const index = this.children.indexOf(beforeChild);
    if (index === -1) {
      this.children.push(child);
      return child;
    }
    this.children.splice(index, 0, child);
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

  closest(selector) {
    let current = this;
    while (current) {
      if (current.matches(selector)) return current;
      current = current.parentElement;
    }
    return null;
  }

  contains(node) {
    let current = node;
    while (current) {
      if (current === this) return true;
      current = current.parentElement;
    }
    return false;
  }

  matches(selector) {
    const normalized = String(selector || '').trim();
    if (!normalized) return false;
    if (normalized.startsWith('#')) return this.id === normalized.slice(1);
    if (normalized.startsWith('.')) return this.classList.contains(normalized.slice(1));
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
    this.body.ownerDocument = this;
    this.listeners = new Map();
  }

  createElement(tagName) {
    const element = new FakeElement(tagName);
    element.ownerDocument = this;
    return element;
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

  querySelector(selector) {
    return this.body.querySelector(selector);
  }

  querySelectorAll(selector) {
    return this.body.querySelectorAll(selector);
  }
}

function append(parent, tagName, options = {}) {
  const el = parent.ownerDocument ? parent.ownerDocument.createElement(tagName) : new FakeElement(tagName);
  el.ownerDocument = parent.ownerDocument || null;
  if (options.id) el.id = options.id;
  if (options.className) el.className = options.className;
  if (options.textContent) el.textContent = options.textContent;
  parent.appendChild(el);
  return el;
}

test('track info drawer reuses the base sidedrawer shell instead of creating a duplicate', () => {
  const document = new FakeDocument();

  const overlay = append(document.body, 'div', { className: 'sidedrawer-overlay' });
  const drawer = append(document.body, 'div', { className: 'sidedrawer' });
  const header = append(drawer, 'div', { className: 'sidedrawer-header' });
  append(header, 'button', { className: 'sidedrawer-close' });
  append(drawer, 'div', { className: 'sidedrawer-content' });

  const trackItem = append(document.body, 'li', { className: 'track-item' });
  append(trackItem, 'button', { className: 'track-info-btn' });
  append(trackItem, 'span', { className: 'track-artist', textContent: 'Artist' });
  append(trackItem, 'span', { className: 'track-title', textContent: 'Title' });

  const window = {
    document,
    location: {
      pathname: '/show/test-show',
      href: 'http://localhost/show/test-show',
    },
    addEventListener() {},
  };

  const context = vm.createContext({
    window,
    document,
    console,
    fetch: async () => {
      throw new Error('fetch should not run in this test');
    },
    MutationObserver: class MutationObserver {
      constructor(callback) {
        this.callback = callback;
      }

      observe() {}

      disconnect() {}
    },
    Node: {
      TEXT_NODE: 3,
      ELEMENT_NODE: 1,
    },
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
  });

  vm.runInContext(source, context);
  document.dispatchEvent({ type: 'DOMContentLoaded' });

  assert.equal(document.querySelectorAll('.sidedrawer').length, 1);
  assert.equal(document.querySelectorAll('.sidedrawer-overlay').length, 1);
  assert.equal(window.trackInfoDrawer.drawer, drawer);
  assert.equal(window.trackInfoDrawer.overlay, overlay);
});
