import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const statsCss = fs.readFileSync(path.resolve(__dirname, '../../static/css/components/stats.css'), 'utf8');
const statsJs = fs.readFileSync(path.resolve(__dirname, '../../static/js/stats.js'), 'utf8');

test('tracks table youtube button restores pointer events for real clicks', () => {
  const statsRuleMatch = statsCss.match(/#statsTable\s+\.stats-row\s+\.col-title\s+\.track-youtube-btn\s*\{([\s\S]*?)\}/);
  assert.ok(statsRuleMatch, 'expected stats-specific youtube button rule to exist');
  assert.match(
    statsRuleMatch[1],
    /pointer-events\s*:\s*auto\s*;/,
    'expected tracks-table youtube buttons to explicitly restore pointer events'
  );
});

test('tracks table genres column wraps chips instead of truncating the row', () => {
  const genresRuleMatch = statsCss.match(/\.stats-row\s+\.col-genres\s*\{([\s\S]*?)\}/);
  assert.ok(genresRuleMatch, 'expected stats-specific genres column rule to exist');
  assert.match(
    genresRuleMatch[1],
    /white-space\s*:\s*normal\s*;/,
    'expected genres cells to allow wrapped chip rows'
  );
  assert.match(
    genresRuleMatch[1],
    /text-overflow\s*:\s*clip\s*;/,
    'expected genres cells to disable ellipsis clipping'
  );
});

test('tracks page syncs a playing row highlight from YouTube player state', () => {
  assert.match(
    statsJs,
    /YouTubePlayerGlobal/,
    'expected tracks page to reference the global YouTube player'
  );
  assert.match(
    statsJs,
    /\.getState\(\)/,
    'expected tracks page to inspect current playback state'
  );
  assert.match(
    statsJs,
    /classList\.add\('playing'\)/,
    'expected tracks page to mark the active row with a playing class'
  );
});

test('tracks table defines a visible playing-row state', () => {
  assert.match(
    statsCss,
    /\.stats-row\.playing\s+\.col\s*\{/,
    'expected tracks table to style playing rows'
  );
});
