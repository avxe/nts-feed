(function () {
  'use strict';

  const SEARCH_TYPES = 'show,episode,track,artist,genre';
  const MIN_QUERY_LENGTH = 2;
  const DEBOUNCE_MS = 180;
  const MAX_ITEMS_PER_GROUP = 4;

  let initialized = false;
  let debounceTimer = null;
  let abortController = null;
  let activeIndex = -1;

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[char]));
  }

  function formatDate(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function buildEpisodeHref(showUrl, episodeUrl, query) {
    if (!showUrl || !episodeUrl) return `/search?q=${encodeURIComponent(query || '')}`;
    return `/show/${encodeURIComponent(showUrl)}#ep=${encodeURIComponent(episodeUrl)}`;
  }

  function buildTrackHref(track) {
    const artists = Array.isArray(track.artists) ? track.artists.filter(Boolean) : [];
    const query = `${artists.join(' ')} ${track.title || ''}`.trim();
    const primaryEpisode = Array.isArray(track.episodes)
      ? track.episodes.find((episode) => episode && episode.show_url && episode.episode_url)
      : null;

    if (!primaryEpisode) {
      return `/search?q=${encodeURIComponent(query)}&types=tracks`;
    }

    const hashParts = [`ep=${encodeURIComponent(primaryEpisode.episode_url)}`];
    if (track.title) hashParts.push(`track=${encodeURIComponent(track.title)}`);
    if (artists.length) hashParts.push(`artist=${encodeURIComponent(artists[0])}`);
    if (query) hashParts.push(`q=${encodeURIComponent(query)}`);

    return `/show/${encodeURIComponent(primaryEpisode.show_url)}#${hashParts.join('&')}`;
  }

  function buildFullSearchHref(query) {
    return `/search?q=${encodeURIComponent(query || '')}`;
  }

  function buildDropdown(searchBox) {
    let dropdown = searchBox.querySelector('.global-search-dropdown');
    if (dropdown) return dropdown;

    dropdown = document.createElement('div');
    dropdown.className = 'global-search-dropdown';
    dropdown.setAttribute('role', 'listbox');
    dropdown.setAttribute('aria-label', 'Search suggestions');
    searchBox.appendChild(dropdown);
    return dropdown;
  }

  function getContext() {
    const input = document.getElementById('globalSearch');
    if (!input) return null;
    const searchBox = input.closest('.search-box');
    if (!searchBox) return null;
    const dropdown = buildDropdown(searchBox);
    const clearButton = searchBox.querySelector('.search-clear');
    return { input, searchBox, dropdown, clearButton };
  }

  function setHasValue(searchBox, value) {
    searchBox.classList.toggle('has-value', Boolean(value && value.trim()));
  }

  function hideDropdown(dropdown) {
    dropdown.style.display = 'none';
    dropdown.innerHTML = '';
    const searchBox = dropdown.closest('.search-box');
    if (searchBox) searchBox.classList.remove('search-box--open');
    activeIndex = -1;
  }

  function showDropdown(dropdown) {
    dropdown.style.display = 'block';
    const searchBox = dropdown.closest('.search-box');
    if (searchBox) searchBox.classList.add('search-box--open');
  }

  function navigateTo(href) {
    if (!href) return;
    if (window.SPARouter && typeof window.SPARouter.navigate === 'function') {
      window.SPARouter.navigate(href);
    } else {
      window.location.href = href;
    }
  }

  function itemTemplate(item) {
    return `
      <button type="button" class="global-search-item" role="option" data-href="${escapeHtml(item.href)}">
        <span class="global-search-item-copy">
          <span class="global-search-item-title">${escapeHtml(item.title)}</span>
          ${item.meta ? `<span class="global-search-item-meta">${escapeHtml(item.meta)}</span>` : ''}
        </span>
      </button>
    `;
  }

  function buildGroups(query, payload) {
    const groups = [];

    const shows = (payload.shows || []).slice(0, MAX_ITEMS_PER_GROUP).map((show) => ({
      title: show.title || 'Untitled Show',
      href: show.url ? `/show/${encodeURIComponent(show.url)}` : `/search?q=${encodeURIComponent(query)}&types=shows`,
    }));
    if (shows.length) groups.push({ title: 'Shows', items: shows });

    const episodes = (payload.episodes || []).slice(0, MAX_ITEMS_PER_GROUP).map((episode) => ({
      title: episode.title || 'Untitled Episode',
      meta: [episode.show_title, formatDate(episode.date)].filter(Boolean).join(' · '),
      href: buildEpisodeHref(episode.show_url, episode.url, query),
    }));
    if (episodes.length) groups.push({ title: 'Episodes', items: episodes });

    const tracks = (payload.tracks || []).slice(0, MAX_ITEMS_PER_GROUP).map((track) => ({
      title: track.title || 'Untitled Track',
      meta: Array.isArray(track.artists) && track.artists.length ? track.artists.join(', ') : '',
      href: buildTrackHref(track),
    }));
    if (tracks.length) groups.push({ title: 'Tracks', items: tracks });

    const artists = (payload.artists || []).slice(0, MAX_ITEMS_PER_GROUP).map((artist) => ({
      title: artist.name || 'Unknown Artist',
      meta: `${artist.track_count || 0} tracks`,
      href: `/search?q=${encodeURIComponent(artist.name || '')}&types=artists,tracks,episodes`,
    }));
    if (artists.length) groups.push({ title: 'Artists', items: artists });

    const genres = (payload.genres || []).slice(0, MAX_ITEMS_PER_GROUP).map((genre) => ({
      title: genre.name || 'Unknown Genre',
      meta: `${genre.episode_count || 0} episodes`,
      href: `/search?q=${encodeURIComponent(genre.name || '')}&types=genres,episodes,tracks`,
    }));
    if (genres.length) groups.push({ title: 'Genres', items: genres });

    return groups;
  }

  function renderDropdown(dropdown, query, payload) {
    const groups = buildGroups(query, payload);
    if (!groups.length) {
      dropdown.innerHTML = `
        <div class="global-search-section global-search-section--empty">
          <div class="global-search-section-title">No quick matches</div>
          <a class="global-search-view-all" href="${buildFullSearchHref(query)}">Open full search</a>
        </div>
      `;
      showDropdown(dropdown);
      bindDropdown(dropdown);
      return;
    }

    dropdown.innerHTML = groups.map((group) => `
      <section class="global-search-section">
        <div class="global-search-section-title">${escapeHtml(group.title)}</div>
        ${group.items.map(itemTemplate).join('')}
      </section>
    `).join('') + `
      <div class="global-search-footer">
        <a class="global-search-view-all" href="${buildFullSearchHref(query)}">View all results →</a>
      </div>
    `;

    showDropdown(dropdown);
    bindDropdown(dropdown);
  }

  function bindDropdown(dropdown) {
    dropdown.querySelectorAll('.global-search-item, .global-search-view-all').forEach((element) => {
      if (element.dataset.bound === 'true') return;
      element.dataset.bound = 'true';
      element.addEventListener('click', (event) => {
        event.preventDefault();
        const href = element.getAttribute('data-href') || element.getAttribute('href');
        if (href) navigateTo(href);
      });
    });
  }

  async function fetchSuggestions(query, dropdown) {
    if (abortController) {
      try { abortController.abort(); } catch (_) {}
    }

    abortController = new AbortController();
    try {
      const response = await fetch(`/api/search?q=${encodeURIComponent(query)}&types=${encodeURIComponent(SEARCH_TYPES)}&limit=6`, {
        signal: abortController.signal,
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      renderDropdown(dropdown, query, payload);
    } catch (error) {
      if (error && error.name === 'AbortError') return;
      dropdown.innerHTML = `
        <div class="global-search-section global-search-section--empty">
          <div class="global-search-section-title">Search unavailable</div>
          <a class="global-search-view-all" href="${buildFullSearchHref(query)}">Open full search</a>
        </div>
      `;
      showDropdown(dropdown);
      bindDropdown(dropdown);
    }
  }

  function syncActiveOption(dropdown) {
    const options = dropdown.querySelectorAll('.global-search-item, .global-search-view-all');
    options.forEach((option, index) => {
      option.classList.toggle('active', index === activeIndex);
    });
  }

  function handleKeyboard(event, context) {
    const options = context.dropdown.querySelectorAll('.global-search-item, .global-search-view-all');
    if (event.key === 'ArrowDown') {
      if (!options.length) return;
      event.preventDefault();
      activeIndex = Math.min(activeIndex + 1, options.length - 1);
      syncActiveOption(context.dropdown);
      return;
    }

    if (event.key === 'ArrowUp') {
      if (!options.length) return;
      event.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      syncActiveOption(context.dropdown);
      return;
    }

    if (event.key === 'Escape') {
      hideDropdown(context.dropdown);
      return;
    }

    if (event.key === 'Enter') {
      const query = context.input.value.trim();
      if (!query) return;
      event.preventDefault();
      if (activeIndex >= 0 && options[activeIndex]) {
        const href = options[activeIndex].getAttribute('data-href') || options[activeIndex].getAttribute('href');
        if (href) {
          navigateTo(href);
          return;
        }
      }
      navigateTo(buildFullSearchHref(query));
    }
  }

  function initGlobalSearch() {
    const context = getContext();
    if (!context || initialized) return;

    context.input.addEventListener('input', () => {
      const query = context.input.value.trim();
      setHasValue(context.searchBox, query);
      activeIndex = -1;

      if (debounceTimer) clearTimeout(debounceTimer);
      if (query.length < MIN_QUERY_LENGTH) {
        hideDropdown(context.dropdown);
        return;
      }

      debounceTimer = setTimeout(() => {
        fetchSuggestions(query, context.dropdown);
      }, DEBOUNCE_MS);
    });

    context.input.addEventListener('keydown', (event) => handleKeyboard(event, context));

    context.input.addEventListener('focus', () => {
      const query = context.input.value.trim();
      if (query.length >= MIN_QUERY_LENGTH && context.dropdown.innerHTML.trim()) {
        showDropdown(context.dropdown);
      }
    });

    if (context.clearButton) {
      context.clearButton.addEventListener('click', () => {
        context.input.value = '';
        setHasValue(context.searchBox, '');
        hideDropdown(context.dropdown);
        context.input.focus();
      });
    }

    document.addEventListener('click', (event) => {
      if (!context.searchBox.contains(event.target)) {
        hideDropdown(context.dropdown);
      }
    });

    initialized = true;
  }

  function reinitGlobalSearch() {
    initGlobalSearch();
  }

  window.GlobalSearch = {
    init: initGlobalSearch,
  };
  window.initGlobalSearchHandlers = reinitGlobalSearch;
  document.addEventListener('DOMContentLoaded', initGlobalSearch);
})();
