/**
 * Admin Dashboard JavaScript
 * Loads collection stats for the admin overview.
 */

(function () {
  'use strict';

  function initAdminPage() {
    if (!document.querySelector('.admin-page')) return;
    loadStats();
  }

  async function loadStats() {
    try {
      const response = await fetch('/api/admin/stats');
      const data = await response.json();

      if (!data.success || !data.stats) return;

      const stats = data.stats;
      setText('statTotalShows', formatNumber(stats.total_shows));
      setText('statTotalEpisodes', formatNumber(stats.total_episodes));
      setText('statTotalTracks', formatNumber(stats.total_tracks));
      setText('statTotalArtists', formatNumber(stats.total_artists));
      renderListeningSummary(data.listening_summary || {});
    } catch (error) {
      console.error('Failed to load admin stats:', error);
    }
  }

  function renderListeningSummary(summary) {
    setText('statListeningEpisodes', formatNumber(summary.episode_listens ?? 0));
    setText('statListeningTracks', formatNumber(summary.track_listens ?? 0));
    renderChipList('adminListeningTopShows', summary.top_shows || [], 'No listening history yet.');
    renderChipList('adminListeningTopArtists', summary.top_artists || [], 'No listening history yet.');
    renderChipList('adminListeningTopGenres', summary.top_genres || [], 'No listening history yet.');
  }

  function renderChipList(id, items, emptyLabel) {
    const container = document.getElementById(id);
    if (!container) return;

    container.innerHTML = '';
    const normalizedItems = (items || [])
      .map((item) => (typeof item === 'string' ? { name: item } : item))
      .filter((item) => item && item.name);

    if (!normalizedItems.length) {
      const empty = document.createElement('span');
      empty.className = 'admin-empty-note';
      empty.textContent = emptyLabel;
      container.appendChild(empty);
      return;
    }

    normalizedItems.slice(0, 5).forEach((item) => {
      const chip = document.createElement('span');
      chip.className = 'chip chip--outlined';
      chip.textContent = item.count ? `${item.name} (${formatNumber(item.count)})` : item.name;
      container.appendChild(chip);
    });
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function formatNumber(num) {
    if (num === undefined || num === null) return '--';
    return Number(num).toLocaleString();
  }

  window.AdminPage = {
    init: initAdminPage,
  };

  if (window.NTSPageModules && typeof window.NTSPageModules.register === 'function') {
    window.NTSPageModules.register('admin', {
      init: initAdminPage,
      cleanup() {},
    });
  }
})();
