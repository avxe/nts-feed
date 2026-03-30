/**
 * Search Page - grouped SQL-backed search experience.
 */

(function () {
  'use strict';

  let currentQuery = '';
  let currentTab = 'all';
  let currentTypes = '';
  let searchResults = {
    shows: [],
    episodes: [],
    tracks: [],
    artists: [],
    genres: [],
  };

  const sectionMap = {
    shows: 'sectionShows',
    episodes: 'sectionEpisodes',
    tracks: 'sectionTracks',
    artists: 'sectionArtists',
    genres: 'sectionGenres',
  };

  function init() {
    const container = document.querySelector('#page-content .search-container') ||
      document.querySelector('.container.search-container');
    if (!container) return;

    const params = new URLSearchParams(window.location.search);
    currentQuery = (container.dataset.searchQuery || params.get('q') || '').trim();
    currentTypes = (container.dataset.searchTypes || params.get('types') || '').trim();
    currentTab = 'all';

    syncGlobalSearchInput(currentQuery);
    setupTabs();

    if (currentQuery) {
      fetchResults(currentQuery);
    } else {
      showEmptyState();
    }
  }

  function setupTabs() {
    const tabsContainer = document.getElementById('searchTabs');
    if (!tabsContainer || tabsContainer.dataset.bound === 'true') return;

    tabsContainer.dataset.bound = 'true';
    tabsContainer.addEventListener('click', (event) => {
      const tab = event.target.closest('.search-tab');
      if (!tab) return;
      setActiveTab(tab.dataset.type || 'all');
    });
  }

  function syncGlobalSearchInput(query) {
    const searchInput = document.getElementById('globalSearch');
    if (!searchInput) return;

    searchInput.value = query;
    const searchBox = searchInput.closest('.search-box');
    if (searchBox) {
      searchBox.classList.toggle('has-value', Boolean(query));
    }
  }

  async function fetchResults(query) {
    if (!query) {
      showEmptyState();
      return;
    }

    showLoading();

    try {
      const start = performance.now();
      const searchUrl = new URL('/api/search', window.location.origin);
      searchUrl.searchParams.set('q', query);
      if (currentTypes) {
        searchUrl.searchParams.set('types', currentTypes);
      }

      const response = await fetch(searchUrl.toString());
      const data = await response.json();
      const elapsed = Math.round(performance.now() - start);

      if (!data.success) {
        showEmptyState();
        return;
      }

      searchResults = {
        shows: data.shows || [],
        episodes: data.episodes || [],
        tracks: data.tracks || [],
        artists: data.artists || [],
        genres: data.genres || [],
      };

      const totalCount = getTotalCount();
      if (!totalCount) {
        showEmptyState();
        return;
      }

      renderResults();
      updateCounts();

      const meta = document.getElementById('searchMeta');
      if (meta) {
        meta.textContent = `${totalCount} results in ${elapsed}ms`;
      }

      hideLoading();
      setActiveTab(currentTab);
    } catch (error) {
      console.error('[SearchPage] fetch failed', error);
      showEmptyState();
    }
  }

  function renderResults() {
    renderShows();
    renderEpisodes();
    renderTracks();
    renderArtists();
    renderGenres();
  }

  function renderShows() {
    const container = document.getElementById('resultsShows');
    if (!container) return;

    container.innerHTML = searchResults.shows.map((show) => {
      const showUrl = show.url ? `/show/${encodeURIComponent(show.url)}` : '#';
      const thumbnailUrl = show.thumbnail ? `/thumbnail?url=${encodeURIComponent(show.thumbnail)}` : '';

      return `
        <a href="${showUrl}" class="search-show-card">
          <div class="show-card-thumbnail">
            ${thumbnailUrl ? `<img src="${thumbnailUrl}" alt="" loading="lazy">` : ''}
          </div>
          <div class="show-card-info">
            <h3 class="show-card-title">${escapeHtml(show.title || 'Untitled Show')}</h3>
            ${show.description ? `<p class="show-card-description">${escapeHtml(show.description)}</p>` : ''}
          </div>
        </a>
      `;
    }).join('');
  }

  function renderEpisodes() {
    const container = document.getElementById('resultsEpisodes');
    if (!container) return;

    container.innerHTML = searchResults.episodes.map((episode) => {
      const episodeUrl = buildEpisodeUrl({
        show_url: episode.show_url,
        episode_url: episode.url,
      });
      const thumbnailUrl = episode.image_url ? `/thumbnail?url=${encodeURIComponent(episode.image_url)}` : '';
      const genres = (episode.matched_genres || []).slice(0, 3);

      return `
        <div class="search-episode-card">
          <a href="${episodeUrl}" class="episode-card-thumbnail">
            ${thumbnailUrl ? `<img src="${thumbnailUrl}" alt="" loading="lazy">` : ''}
          </a>
          <div class="episode-card-info">
            <a href="${episodeUrl}" class="episode-card-show">${escapeHtml(episode.show_title || '')}</a>
            <a href="${episodeUrl}" class="episode-card-title">${escapeHtml(episode.title || 'Untitled Episode')}</a>
            <div class="episode-card-meta">
              ${formatDate(episode.date) ? `<span class="episode-card-date">${formatDate(episode.date)}</span>` : ''}
              ${genres.length ? `
                <div class="episode-card-genres">
                  ${genres.map((genre) => `<span class="chip">${escapeHtml(genre)}</span>`).join('')}
                </div>
              ` : ''}
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  function renderTracks() {
    const container = document.getElementById('resultsTracks');
    if (!container) return;

    container.innerHTML = searchResults.tracks.map((track) => {
      const artists = (track.artists || []).join(', ') || 'Unknown Artist';
      const episodes = (track.episodes || []).map((episode) => {
        const href = buildEpisodeUrl(episode);
        return `
          <a href="${href}" class="track-episode-link" title="${escapeHtml(episode.episode_title || '')}">
            <i class="fas fa-podcast track-episode-icon"></i>
            <span class="track-episode-show">${escapeHtml(episode.show_title || '')}</span>
            <span class="track-episode-sep">/</span>
            <span class="track-episode-title">${escapeHtml(episode.episode_title || '')}</span>
          </a>
        `;
      }).join('');

      return `
        <div class="search-track-card ${episodes ? 'has-episodes' : ''}" data-track-id="${track.id}">
          <div class="track-card-main">
            <div class="track-card-info">
              <span class="track-card-artist">${escapeHtml(artists)}</span>
              <span class="track-card-title">${escapeHtml(track.title || 'Untitled Track')}</span>
            </div>
          </div>
          ${episodes ? `<div class="track-card-episodes">${episodes}</div>` : ''}
        </div>
      `;
    }).join('');
  }

  function renderArtists() {
    const container = document.getElementById('resultsArtists');
    if (!container) return;

    container.innerHTML = searchResults.artists.map((artist) => {
      const href = `/search?q=${encodeURIComponent(artist.name)}&types=tracks,episodes,shows`;
      return `
        <a href="${href}" class="search-episode-card">
          <div class="episode-card-info">
            <span class="episode-card-show">Artist</span>
            <span class="episode-card-title">${escapeHtml(artist.name || 'Unknown Artist')}</span>
            <div class="episode-card-meta">
              <span class="episode-card-date">${artist.track_count || 0} tracks in your library</span>
            </div>
          </div>
        </a>
      `;
    }).join('');
  }

  function renderGenres() {
    const container = document.getElementById('resultsGenres');
    if (!container) return;

    container.innerHTML = searchResults.genres.map((genre) => {
      const href = `/search?q=${encodeURIComponent(genre.name)}&types=episodes,tracks,shows`;
      return `
        <a href="${href}" class="search-episode-card">
          <div class="episode-card-info">
            <span class="episode-card-show">Genre</span>
            <span class="episode-card-title">${escapeHtml(genre.name || 'Unknown Genre')}</span>
            <div class="episode-card-meta">
              <span class="episode-card-date">${genre.episode_count || 0} matching episodes</span>
            </div>
          </div>
        </a>
      `;
    }).join('');
  }

  function updateCounts() {
    const counts = {
      all: getTotalCount(),
      shows: searchResults.shows.length,
      episodes: searchResults.episodes.length,
      tracks: searchResults.tracks.length,
      artists: searchResults.artists.length,
      genres: searchResults.genres.length,
    };

    Object.entries(counts).forEach(([type, value]) => {
      const id = `count${type.charAt(0).toUpperCase()}${type.slice(1)}`;
      const node = document.getElementById(id);
      if (node) node.textContent = value;
    });
  }

  function setActiveTab(type) {
    currentTab = type || 'all';

    document.querySelectorAll('.search-tab').forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.type === currentTab);
    });

    Object.entries(sectionMap).forEach(([resultType, sectionId]) => {
      const section = document.getElementById(sectionId);
      if (!section) return;

      const hasResults = (searchResults[resultType] || []).length > 0;
      const shouldShow = currentTab === 'all' ? hasResults : currentTab === resultType && hasResults;
      section.classList.toggle('hidden', !shouldShow);
    });
  }

  function getTotalCount() {
    return Object.keys(sectionMap).reduce((total, key) => total + (searchResults[key] || []).length, 0);
  }

  function buildEpisodeUrl(episode) {
    if (!episode || !episode.show_url) return '#';
    let url = `/show/${encodeURIComponent(episode.show_url)}`;
    const episodeUrl = episode.episode_url || episode.url;
    if (episodeUrl) {
      url += `#ep=${encodeURIComponent(episodeUrl)}`;
    }
    return url;
  }

  function formatDate(dateStr) {
    if (!dateStr) return '';
    return dateStr;
  }

  function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value || '';
    return div.innerHTML;
  }

  function showLoading() {
    const loading = document.getElementById('searchLoading');
    const empty = document.getElementById('searchEmpty');
    const results = document.getElementById('searchResults');
    if (loading) loading.classList.remove('hidden');
    if (empty) empty.classList.add('hidden');
    if (results) results.classList.add('hidden');
  }

  function hideLoading() {
    const loading = document.getElementById('searchLoading');
    const results = document.getElementById('searchResults');
    if (loading) loading.classList.add('hidden');
    if (results) results.classList.remove('hidden');
  }

  function showEmptyState() {
    const loading = document.getElementById('searchLoading');
    const empty = document.getElementById('searchEmpty');
    const results = document.getElementById('searchResults');
    const meta = document.getElementById('searchMeta');

    if (loading) loading.classList.add('hidden');
    if (results) results.classList.add('hidden');
    if (empty) empty.classList.remove('hidden');
    if (meta) meta.textContent = currentQuery ? '0 results' : 'Enter a query to search your library.';
  }

  function reinit() {
    searchResults = {
      shows: [],
      episodes: [],
      tracks: [],
      artists: [],
      genres: [],
    };
    init();
  }

  if (window.NTSPageModules && typeof window.NTSPageModules.register === 'function') {
    window.NTSPageModules.register('search', {
      init: reinit,
      cleanup() {},
    });
  }

  window.initSearchPageHandlers = reinit;
})();
