import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/index-page.js'), 'utf8');

function loadIndexPageWithStorage(initialEntries = {}) {
  const storage = new Map(Object.entries(initialEntries));
  const localStorage = {
    getItem(key) {
      return storage.has(key) ? storage.get(key) : null;
    },
    setItem(key, value) {
      storage.set(key, String(value));
    },
    removeItem(key) {
      storage.delete(key);
    },
  };

  const context = {
    window: {
      location: { pathname: '/' },
      localStorage,
      NTSPageModules: { register() {} },
    },
    localStorage,
    console,
    setInterval() {},
  };

  vm.createContext(context);
  vm.runInContext(source, context);
  return { api: context.window.NTSIndexPage, localStorage };
}

test('index page migrates the legacy saved sort key into nts-feed storage', () => {
  const legacyKey = ['nts', 'tracker', 'sort'].join('-');
  const { api, localStorage } = loadIndexPageWithStorage({ [legacyKey]: 'alphabetical' });

  const savedSort = api.getSavedSortPreference(localStorage);

  assert.equal(savedSort, 'alphabetical');
  assert.equal(localStorage.getItem('nts-feed-sort'), 'alphabetical');
  assert.equal(localStorage.getItem(legacyKey), null);
});

test('index page prefers the new nts-feed sort key when it exists', () => {
  const legacyKey = ['nts', 'tracker', 'sort'].join('-');
  const { api, localStorage } = loadIndexPageWithStorage({
    'nts-feed-sort': 'updated',
    [legacyKey]: 'alphabetical',
  });

  const savedSort = api.getSavedSortPreference(localStorage);

  assert.equal(savedSort, 'updated');
  assert.equal(localStorage.getItem('nts-feed-sort'), 'updated');
  assert.equal(localStorage.getItem(legacyKey), 'alphabetical');
});
