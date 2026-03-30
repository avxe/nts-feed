/* global showNotification */

/**
 * Discover Page - unified feed with tracklist peek.
 */

(function () {
  'use strict';

  let discoverState = null;
  let lifecycleEventsBound = false;
  let genreShelvesCache = [];
  let originalShelves = [];
  let genreSearchTimer = null;
  let activeGenreName = null;
  let genreExplorerBound = false;

  function initDiscoverPage() {
    const container = document.querySelector('[data-discover-page="true"]');
    if (!container) return;

    discoverState = null;
    genreExplorerBound = false;
    originalShelves = [];
    activeGenreName = null;
    bindEvents(container);
    showLoading();
    loadDiscover();
  }

  function bindEvents(container) {
    const surpriseBtn = document.getElementById('discoverSurpriseBtn');
    const addShowBtn = document.getElementById('addShowBtn');

    if (surpriseBtn && surpriseBtn.dataset.bound !== 'true') {
      surpriseBtn.dataset.bound = 'true';
      surpriseBtn.addEventListener('click', refreshSurpriseEpisode);
    }

    if (addShowBtn && addShowBtn.dataset.bound !== 'true') {
      addShowBtn.dataset.bound = 'true';
      addShowBtn.addEventListener('click', openSubscribeModal);
    }

    bindGenreExplorer();
    bindLifecycleEvents();
    bindDiscoverThumbPlay(container);
  }

  function ensureAudioPlayer() {
    if (!window.ntsAudioPlayer) {
      window.ntsAudioPlayer = new NTSAudioPlayer();
    }
  }

  function bindDiscoverThumbPlay(container) {
    if (container.dataset.thumbPlayBound === 'true') return;
    container.dataset.thumbPlayBound = 'true';

    container.addEventListener('click', (e) => {
      const btn = e.target.closest('.discover-episode-thumb-play');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();

      const url = btn.dataset.episodeUrl || '';
      if (!url) return;

      ensureAudioPlayer();

      const img = btn.querySelector('img');
      const episodeData = {
        url,
        title: btn.dataset.episodeTitle || '',
        date: btn.dataset.episodeDate || '',
        image: img ? img.src : '',
        show_url: btn.dataset.showUrl || '',
        show_title: btn.dataset.showTitle || '',
      };

      window.ntsAudioPlayer.showPlayer(episodeData).catch((err) => {
        console.error('[DiscoverPage] play failed', err);
      });
    });
  }

  function bindLifecycleEvents() {
    if (lifecycleEventsBound) return;
    lifecycleEventsBound = true;

    window.addEventListener('listening:session-finalized', () => {
      if (!isDiscoverPageActive()) return;
      void reloadDiscover();
    });
  }

  function isDiscoverPageActive() {
    return Boolean(document.querySelector('[data-discover-page="true"]'));
  }

  async function loadDiscover() {
    try {
      const response = await fetch('/api/discover');
      const data = await response.json();

      if (!response.ok || !data.success) {
        showEmpty();
        return;
      }

      discoverState = data;
      const sections = data.sections || {};
      const hasContinue = (sections.continue_listening || []).length > 0;
      const hasBecause = (sections.because_you_like || []).length > 0;
      const hasGenre = (sections.genre_spotlight || []).length > 0;

      if (!hasContinue && !hasBecause && !hasGenre) {
        showEmpty();
        return;
      }

      renderAll(sections);
      showContent();
      renderMeta(sections);
    } catch (error) {
      console.error('[DiscoverPage] load failed', error);
      showEmpty();
    }
  }

  async function reloadDiscover() {
    try {
      const response = await fetch('/api/discover');
      const data = await response.json();
      if (!response.ok || !data.success) return;

      discoverState = data;
      const sections = data.sections || {};
      renderAll(sections);
      renderMeta(sections);
    } catch (error) {
      console.error('[DiscoverPage] reload failed', error);
    }
  }

  async function refreshSurpriseEpisode() {
    const button = document.getElementById('discoverSurpriseBtn');
    if (button) {
      button.disabled = true;
      button.classList.add('spinning');
    }

    try {
      const response = await fetch('/api/discover/surprise', { method: 'POST' });
      const data = await response.json();

      if (response.ok && data.success && data.episode) {
        const section = document.getElementById('discoverBecauseSection');
        const container = document.getElementById('discoverBecause');
        if (section && container) {
          const existing = container.innerHTML;
          container.innerHTML = renderEpisodeCards([data.episode]) + existing;
          section.classList.remove('hidden');
        }
      } else if (window.showNotification) {
        window.showNotification(data.message || 'No surprise episode available', 'error');
      }
    } catch (error) {
      console.error('[DiscoverPage] surprise failed', error);
      if (window.showNotification) {
        window.showNotification('Failed to refresh surprise episode', 'error');
      }
    } finally {
      if (button) {
        button.disabled = false;
        button.classList.remove('spinning');
      }
    }
  }

  function renderAll(sections) {
    renderSection('discoverContinueSection', 'discoverContinue', sections.continue_listening || []);
    renderSection('discoverBecauseSection', 'discoverBecause', sections.because_you_like || []);
    renderGenreShelves(sections.genre_spotlight || []);
  }

  function renderSection(sectionId, containerId, episodes) {
    const section = document.getElementById(sectionId);
    const container = document.getElementById(containerId);
    if (!section || !container) return;

    if (!episodes.length) {
      section.classList.add('hidden');
      container.innerHTML = '';
      return;
    }

    section.classList.remove('hidden');
    container.innerHTML = renderEpisodeCards(episodes);
  }

  function renderGenreShelves(shelves) {
    const section = document.getElementById('discoverGenreSection');
    const pillsContainer = document.getElementById('discoverGenrePills');
    const container = document.getElementById('discoverGenres');
    if (!section || !pillsContainer || !container) return;

    section.classList.remove('hidden');

    genreShelvesCache = shelves;
    originalShelves = shelves;
    activeGenreName = null;

    renderGenrePills(shelves, pillsContainer);

    if (pillsContainer.dataset.bound !== 'true') {
      pillsContainer.dataset.bound = 'true';
      pillsContainer.addEventListener('click', handleGenrePillClick);
    }

    if (shelves.length) {
      renderActiveGenreShelf(0, container);
    } else {
      container.innerHTML = '';
    }

    const relatedContainer = document.getElementById('genreRelatedPills');
    if (relatedContainer) relatedContainer.classList.add('hidden');

    bindGenreExplorer();
  }

  function renderGenrePills(shelves, pillsContainer) {
    pillsContainer.innerHTML = shelves.map((shelf, i) =>
      `<button class="discover-genre-pill${i === 0 ? ' active' : ''}" data-genre-index="${i}" data-genre-name="${escapeHtml(shelf.genre)}">${escapeHtml(shelf.genre || 'Genre')}</button>`
    ).join('');
  }

  function handleGenrePillClick(e) {
    const pill = e.target.closest('.discover-genre-pill');
    if (!pill) return;
    const index = parseInt(pill.dataset.genreIndex, 10);
    if (isNaN(index) || index < 0 || index >= genreShelvesCache.length) return;

    const pillsContainer = pill.parentElement;
    const activePill = pillsContainer.querySelector('.discover-genre-pill.active');
    if (activePill) activePill.classList.remove('active');
    pill.classList.add('active');

    const genreName = pill.dataset.genreName || (genreShelvesCache[index] && genreShelvesCache[index].genre);
    activeGenreName = genreName;

    const shelf = genreShelvesCache[index];
    const container = document.getElementById('discoverGenres');

    if (shelf && shelf._isSearch && !shelf.episodes.length) {
      const slug = pill.dataset.genreSlug || slugifyGenre(genreName);
      void loadGenreShelfBySlug(slug, genreName);
    } else if (container) {
      renderActiveGenreShelf(index, container);
    }

    if (genreName) {
      void loadRelatedGenres(genreName);
    }
  }

  function renderActiveGenreShelf(index, container) {
    const shelf = genreShelvesCache[index];
    if (!shelf) { container.innerHTML = ''; return; }
    container.innerHTML = `
      <div class="results-list results-list--episodes discover-episode-grid">
        ${renderEpisodeCards(shelf.episodes || [])}
      </div>`;
  }

  // --- Genre Explorer (search + related genres wormhole) ---

  function bindGenreExplorer() {
    if (genreExplorerBound) return;
    const searchInput = document.getElementById('genreExplorerSearch');
    if (!searchInput) return;
    genreExplorerBound = true;

    searchInput.addEventListener('input', () => {
      clearTimeout(genreSearchTimer);
      const query = searchInput.value.trim();
      if (!query) {
        restoreOriginalPills();
        return;
      }
      genreSearchTimer = setTimeout(() => {
        void searchGenres(query);
      }, 200);
    });

    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        searchInput.value = '';
        restoreOriginalPills();
        searchInput.blur();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const pillsContainer = document.getElementById('discoverGenrePills');
        if (!pillsContainer) return;
        const firstPill = pillsContainer.querySelector('.discover-genre-pill');
        if (firstPill) firstPill.click();
      }
    });
  }

  async function searchGenres(query) {
    try {
      const response = await fetch(`/api/genres/explore?q=${encodeURIComponent(query)}`);
      const data = await response.json();
      if (!response.ok || !data.success) return;

      const matches = data.matching_genres || [];
      if (!matches.length) {
        const pillsContainer = document.getElementById('discoverGenrePills');
        if (pillsContainer) {
          pillsContainer.innerHTML = '<span class="genre-explorer__no-results">No genres found</span>';
        }
        return;
      }

      const searchShelves = matches.map((m) => ({
        genre: m.name,
        slug: slugifyGenre(m.name),
        episodes: [],
        episode_count: m.episode_count,
        _isSearch: true,
      }));

      genreShelvesCache = searchShelves;
      const pillsContainer = document.getElementById('discoverGenrePills');
      if (!pillsContainer) return;

      pillsContainer.innerHTML = searchShelves.map((shelf, i) =>
        `<button class="discover-genre-pill" data-genre-index="${i}" data-genre-name="${escapeHtml(shelf.genre)}" data-genre-slug="${escapeHtml(shelf.slug)}">` +
        `${escapeHtml(shelf.genre)}` +
        `<span class="genre-pill__count">${shelf.episode_count}</span>` +
        `</button>`
      ).join('');
    } catch (error) {
      console.error('[GenreExplorer] search failed', error);
    }
  }

  function restoreOriginalPills() {
    genreShelvesCache = originalShelves;
    activeGenreName = null;
    const pillsContainer = document.getElementById('discoverGenrePills');
    if (!pillsContainer) return;

    if (!originalShelves.length) {
      pillsContainer.innerHTML = '';
      return;
    }

    renderGenrePills(originalShelves, pillsContainer);

    const container = document.getElementById('discoverGenres');
    if (container) renderActiveGenreShelf(0, container);

    const relatedContainer = document.getElementById('genreRelatedPills');
    if (relatedContainer) relatedContainer.classList.add('hidden');
  }

  async function loadRelatedGenres(genreName) {
    const relatedContainer = document.getElementById('genreRelatedPills');
    if (!relatedContainer) return;

    try {
      const response = await fetch(`/api/genres/explore?genre=${encodeURIComponent(genreName)}`);
      const data = await response.json();
      if (!response.ok || !data.success) return;

      const related = data.related_genres || [];
      if (!related.length) {
        relatedContainer.classList.add('hidden');
        return;
      }

      const pills = related.map((r) =>
        `<button class="genre-related-pill" data-genre-name="${escapeHtml(r.name)}" data-genre-slug="${slugifyGenre(r.name)}" title="Similarity: ${Math.round(r.similarity * 100)}%">${escapeHtml(r.name)}</button>`
      ).join('');

      relatedContainer.innerHTML =
        '<span class="genre-explorer__related-label">Related</span>' + pills;
      relatedContainer.classList.remove('hidden');

      if (relatedContainer.dataset.bound !== 'true') {
        relatedContainer.dataset.bound = 'true';
        relatedContainer.addEventListener('click', handleRelatedPillClick);
      }
    } catch (error) {
      console.error('[GenreExplorer] related load failed', error);
      relatedContainer.classList.add('hidden');
    }
  }

  function handleRelatedPillClick(e) {
    const pill = e.target.closest('.genre-related-pill');
    if (!pill) return;

    const genreName = pill.dataset.genreName;
    const slug = pill.dataset.genreSlug;
    if (!genreName || !slug) return;

    activeGenreName = genreName;

    const searchInput = document.getElementById('genreExplorerSearch');
    if (searchInput) searchInput.value = '';

    void loadGenreShelfBySlug(slug, genreName);
    void loadRelatedGenres(genreName);
  }

  async function loadGenreShelfBySlug(slug, genreName) {
    const container = document.getElementById('discoverGenres');
    if (!container) return;

    container.innerHTML = '<div class="genre-explorer__loading">Loading...</div>';

    try {
      const response = await fetch(`/api/discover/genre/${encodeURIComponent(slug)}`);
      const data = await response.json();
      if (!response.ok || !data.success) {
        container.innerHTML = '<div class="genre-explorer__no-results">No episodes found for this genre</div>';
        return;
      }

      const shelf = {
        genre: data.genre || genreName,
        slug: slug,
        episodes: data.episodes || [],
      };

      const existingIndex = genreShelvesCache.findIndex(
        (s) => s.genre.toLowerCase() === genreName.toLowerCase()
      );
      if (existingIndex >= 0) {
        genreShelvesCache[existingIndex] = shelf;
      } else {
        genreShelvesCache.push(shelf);
      }

      const pillsContainer = document.getElementById('discoverGenrePills');
      if (pillsContainer) {
        const activePill = pillsContainer.querySelector('.discover-genre-pill.active');
        if (activePill) activePill.classList.remove('active');

        let targetPill = pillsContainer.querySelector(
          `.discover-genre-pill[data-genre-name="${genreName}"]`
        );
        if (!targetPill) {
          const newIndex = genreShelvesCache.length - 1;
          const newPill = document.createElement('button');
          newPill.className = 'discover-genre-pill active';
          newPill.dataset.genreIndex = String(newIndex);
          newPill.dataset.genreName = genreName;
          newPill.dataset.genreSlug = slug;
          newPill.textContent = genreName;
          pillsContainer.appendChild(newPill);
          newPill.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
        } else {
          targetPill.classList.add('active');
          targetPill.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
        }
      }

      container.innerHTML = `
        <div class="results-list results-list--episodes discover-episode-grid">
          ${renderEpisodeCards(shelf.episodes || [])}
        </div>`;
    } catch (error) {
      console.error('[GenreExplorer] shelf load failed', error);
      container.innerHTML = '<div class="genre-explorer__no-results">Failed to load genre shelf</div>';
    }
  }

  function slugifyGenre(value) {
    return (value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }

  // --- End Genre Explorer ---

  function renderEpisodeCards(episodes) {
    return episodes.map((episode) => {
      const href = buildEpisodeUrl(episode.show_url, episode.episode_url);
      const thumbnailUrl = episode.episode_image_url
        ? `/thumbnail?url=${encodeURIComponent(episode.episode_image_url)}`
        : '';

      const episodeUrl = episode.episode_url || '';

      const metaParts = [];
      if (episode.episode_date) {
        metaParts.push(`<span class="discover-episode-date">${escapeHtml(episode.episode_date)}</span>`);
      }
      if (episode.reason_label) {
        if (metaParts.length) {
          metaParts.push('<span class="discover-episode-meta-sep" aria-hidden="true">·</span>');
        }
        metaParts.push(`<span class="discover-episode-reason">${escapeHtml(episode.reason_label)}</span>`);
      }
      const metaHtml = metaParts.length
        ? `<div class="discover-episode-meta-line">${metaParts.join('')}</div>`
        : '';

      return `
        <div class="search-episode-card discover-episode-card discover-episode-card--tile">
          <button type="button"
            class="discover-episode-thumb-play"
            aria-label="${escapeHtml(`Play ${episode.episode_title || 'episode'}`)}"
            data-episode-url="${escapeHtml(episodeUrl)}"
            data-episode-title="${escapeHtml(episode.episode_title || '')}"
            data-episode-date="${escapeHtml(episode.episode_date || '')}"
            data-show-url="${escapeHtml(episode.show_url || '')}"
            data-show-title="${escapeHtml(episode.show_title || '')}">
            ${thumbnailUrl ? `<img src="${thumbnailUrl}" alt="" loading="lazy">` : ''}
          </button>
          <div class="discover-episode-card__body">
            <a href="${href}" class="discover-episode-card__title">${escapeHtml(episode.episode_title || 'Untitled Episode')}</a>
            ${metaHtml}
          </div>
        </div>
      `;
    }).join('');
  }

  function renderMeta(sections) {
    const meta = document.getElementById('discoverMeta');
    if (!meta) return;

    const continueCount = (sections.continue_listening || []).length;
    const becauseCount = (sections.because_you_like || []).length;
    const genreCount = (sections.genre_spotlight || []).length;
    const parts = [];
    if (continueCount) parts.push(`${continueCount} in progress`);
    if (becauseCount) parts.push(`${becauseCount} picks`);
    if (genreCount) parts.push(`${genreCount} genre shelves`);
    meta.textContent = parts.join(', ') + '.';
  }

  function buildEpisodeUrl(showUrl, episodeUrl) {
    if (!showUrl) return '#';
    let href = `/show/${encodeURIComponent(showUrl)}`;
    if (episodeUrl) {
      href += `#ep=${encodeURIComponent(episodeUrl)}`;
    }
    return href;
  }

  function showLoading() {
    toggleVisibility('discoverLoading', true);
    toggleVisibility('discoverEmpty', false);
    toggleVisibility('discoverContent', false);
  }

  function showEmpty() {
    toggleVisibility('discoverLoading', false);
    toggleVisibility('discoverEmpty', true);
    toggleVisibility('discoverContent', false);
  }

  function showContent() {
    toggleVisibility('discoverLoading', false);
    toggleVisibility('discoverEmpty', false);
    toggleVisibility('discoverContent', true);
  }

  function toggleVisibility(id, visible) {
    const node = document.getElementById(id);
    if (!node) return;
    node.classList.toggle('hidden', !visible);
  }

  function openSubscribeModal() {
    if (window.openSubscribeModal && window.openSubscribeModal !== openSubscribeModal) {
      window.openSubscribeModal();
      return;
    }

    const modal = document.getElementById('subscribeModal');
    if (modal) {
      modal.classList.add('show');
      modal.setAttribute('aria-hidden', 'false');
    }
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function cleanupDiscoverPage() {
    if (genreSearchTimer) {
      clearTimeout(genreSearchTimer);
      genreSearchTimer = null;
    }
  }

  window.initMixtapePageHandlers = initDiscoverPage;
  window.initDiscoverPageHandlers = initDiscoverPage;

  if (window.NTSPageModules && typeof window.NTSPageModules.register === 'function') {
    const discoverModule = {
      init: initDiscoverPage,
      cleanup: cleanupDiscoverPage,
    };
    window.NTSPageModules.register('discover', discoverModule);
    window.NTSPageModules.register('mixtape', discoverModule);
  }
})();
