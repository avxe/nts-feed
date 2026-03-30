/* Tracks page logic: server-side pagination, infinite scroll, optimized rendering */
(function(){
  'use strict';

  let activeController = null;

  function destroyActiveController() {
    if (!activeController) return;
    var controller = activeController;
    activeController = null;
    if (controller && typeof controller.cleanup === 'function') {
      controller.cleanup();
    }
  }

  // Main initialization function
  function initStatsPage() {
    var root = document.getElementById('statsPageRoot');
    if (!root || !document.getElementById('statsTable')) return;
    destroyActiveController();
    activeController = initStatsPageCore(root);
    window._statsPageCleanup = destroyActiveController;
  }

  window.initStatsPageHandlers = initStatsPage;

  if (window.NTSPageModules && typeof window.NTSPageModules.register === 'function') {
    window.NTSPageModules.register('stats', {
      init: initStatsPage,
      cleanup: destroyActiveController,
    });
  }

  function initStatsPageCore(root) {
  var cleanups = [];
  var destroyed = false;

  function registerCleanup(fn){
    cleanups.push(fn);
    return fn;
  }

  function bind(target, eventName, handler, options){
    if (!target) return handler;
    target.addEventListener(eventName, handler, options);
    registerCleanup(function(){
      target.removeEventListener(eventName, handler, options);
    });
    return handler;
  }

  function cleanup(){
    if (destroyed) return;
    destroyed = true;
    if (window._statsPageCleanup === destroyActiveController) {
      window._statsPageCleanup = null;
    }
    while (cleanups.length) {
      try {
        cleanups.pop()();
      } catch (_) {}
    }
  }

  const state = {
    page: 1,
    perPage: 40,
    sortBy: 'play_count',
    sortDir: 'desc',
    titleFilter: '',
    artistFilter: '',
    genres: '',
    total: 0,
    loading: false,
    rows: [], // cache of fetched rows
    showFilter: '',
    showUrl: '',      // exact show URL when selected from suggestions
    episodeFilter: '',
    episodeId: '',     // exact episode ID when selected from suggestions
  };
  var currentlyPlayingRow = null;

  function setPlayingRow(row, options){
    var shouldScroll = !!(options && options.scroll);
    if (currentlyPlayingRow && currentlyPlayingRow !== row) {
      currentlyPlayingRow.classList.remove('playing');
    }
    currentlyPlayingRow = row || null;
    if (currentlyPlayingRow) {
      currentlyPlayingRow.classList.add('playing');
      if (shouldScroll) {
        try { currentlyPlayingRow.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (_) {}
      }
    }
  }

  function clearPlayingRow(){
    if (!currentlyPlayingRow) return;
    currentlyPlayingRow.classList.remove('playing');
    currentlyPlayingRow = null;
  }

  function normalizeTrackMatchValue(value){
    return String(value || '').trim().toLowerCase();
  }

  function findPlayingRowFromPlayerState(playerState){
    var currentTrack = playerState && playerState.currentTrack;
    if (!currentTrack) return null;

    if (currentTrack.element && document.contains(currentTrack.element)) {
      return currentTrack.element.closest('.stats-row');
    }

    var trackId = currentTrack.trackId || currentTrack.track_id || '';
    if (trackId) {
      var byId = root.querySelector('.track-youtube-btn[data-track-id="' + String(trackId) + '"]');
      if (byId) return byId.closest('.stats-row');
    }

    var artist = normalizeTrackMatchValue(currentTrack.artist);
    var title = normalizeTrackMatchValue(currentTrack.title);
    if (!artist && !title) return null;

    var buttons = root.querySelectorAll('#statsRows .track-youtube-btn');
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      if (
        normalizeTrackMatchValue(btn.dataset.artist) === artist
        && normalizeTrackMatchValue(btn.dataset.title) === title
      ) {
        return btn.closest('.stats-row');
      }
    }

    return null;
  }

  function syncPlayingRowWithPlayer(options){
    var playerApi = window.YouTubePlayerGlobal;
    if (!playerApi || typeof playerApi.getState !== 'function' || typeof playerApi.isVisible !== 'function' || !playerApi.isVisible()) {
      clearPlayingRow();
      return;
    }

    var row = findPlayingRowFromPlayerState(playerApi.getState());
    if (!row) {
      clearPlayingRow();
      return;
    }

    setPlayingRow(row, options);
  }

  // ------------------------------------------------------------------
  // Column width management
  // ------------------------------------------------------------------
  const defaultWidths = {
    '-1': 40,  // Select column
    0: 300,    // Track title
    1: 250,    // Artists
    2: 80,     // Plays
    3: 80,     // Shows
    4: 200,    // Genres
    5: 500     // Episodes
  };

  function getColumnWidths(){
    try{
      const stored = localStorage.getItem('stats_column_widths');
      if (stored) return { ...defaultWidths, ...JSON.parse(stored) };
      return { ...defaultWidths };
    }catch(_){ return { ...defaultWidths }; }
  }

  function setColumnWidths(widths, persist){
    if (persist) {
      try{ localStorage.setItem('stats_column_widths', JSON.stringify(widths)); }catch(_){}
    }
    const table = $('statsTable');
    const colgroup = $('statsColGroup');
    if (!table || !colgroup) return;

    // Update <col> elements - table-layout:fixed propagates to all rows
    const cols = colgroup.querySelectorAll('col');
    cols.forEach(function(col){
      var dc = col.getAttribute('data-col');
      if (dc && widths[dc] !== undefined) col.style.width = widths[dc] + 'px';
    });

    // Update header cells for sticky-header accuracy
    var headerCells = table.querySelectorAll('thead th');
    headerCells.forEach(function(th){
      var resizer = th.querySelector('.col-resizer');
      var dc = resizer ? resizer.getAttribute('data-col') : null;
      if (dc && widths[dc] !== undefined) {
        th.style.width = widths[dc] + 'px';
        th.style.minWidth = widths[dc] + 'px';
        th.style.maxWidth = widths[dc] + 'px';
      }
    });
  }

  function $(id){ return document.getElementById(id); }
  function formatNum(n){ return typeof n === 'number' ? n.toLocaleString() : (n || 0); }

  // ------------------------------------------------------------------
  // HTML escaping - cached lookup table for performance
  // ------------------------------------------------------------------
  var _escapeMap = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
  var _escapeRe = /[&<>"']/g;
  function escapeHtml(str){
    return String(str || '').replace(_escapeRe, function(ch){ return _escapeMap[ch]; });
  }

  // ------------------------------------------------------------------
  // Event delegation for YouTube play buttons
  // ------------------------------------------------------------------

  // Handle YouTube button click
  async function handleYouTubeClick(e) {
    var btn = e.target.closest('.track-youtube-btn');
    if (!btn) return;

    e.stopPropagation();
    if (btn.classList.contains('searching')) return;

    var artist = btn.dataset.artist;
    var title = btn.dataset.title;
    if (!artist && !title) return;

    try {
      btn.classList.add('searching');
      var resp = await fetch('/search_youtube', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artist: artist, title: title })
      });

      if (!resp.ok) throw new Error('Failed to search YouTube');
      var data = await resp.json();

      if (data.success) {
        if (data.search_only) {
          if (window.showNotification) showNotification('Searching YouTube for "' + artist + ' - ' + title + '"', 'info');
          window.open(data.video_url, '_blank');
        } else {
          setPlayingRow(btn.closest('.stats-row'), { scroll: false });
          if (typeof window.resetYouTubeTrackList === 'function') window.resetYouTubeTrackList();
          if (typeof window.showYouTubePlayer === 'function') {
            window.showYouTubePlayer(data, artist, title, null, null, window.location.pathname);
          } else {
            window.open(data.video_url || (data.video_id ? 'https://www.youtube.com/watch?v=' + data.video_id : '#'), '_blank');
          }
        }
      } else {
        if (data.quota_exceeded) {
          if (window.showNotification) showNotification('YouTube API daily quota exceeded. Please try again tomorrow.', 'error', 10000);
          document.querySelectorAll('.track-youtube-btn').forEach(function(b){
            b.disabled = true;
            b.classList.add('disabled');
            b.title = 'YouTube API quota exceeded. Please try again tomorrow.';
          });
        } else {
          if (window.showNotification) showNotification(data.message || 'Failed to search on YouTube', 'error');
        }
      }
    } catch (err) {
      if (window.showNotification) showNotification('Failed to search on YouTube', 'error');
    } finally {
      btn.classList.remove('searching');
    }
  }

  // ------------------------------------------------------------------
  // Controls & API
  // ------------------------------------------------------------------
  function readControls(){
    var per = parseInt(($('perPage') || {}).value, 10);
    state.perPage = isNaN(per) ? 50 : per;
    state.titleFilter = (($('titleFilter') || {}).value || '').trim();
    state.artistFilter = (($('artistFilter') || {}).value || '').trim();
    state.showFilter = (($('showFilter') || {}).value || '').trim();
    state.genres = (($('genresInput') || {}).value || '').trim();
    state.episodeFilter = (($('episodeFilterInput') || {}).value || '').trim();
    // Clear exact IDs if the user has changed the text from what was selected
    // (the exact IDs are set only when a suggestion is picked)
  }

  function buildUrl(){
    var u = new URL('/api/tracks', window.location.origin);
    u.searchParams.set('page', String(state.page));
    u.searchParams.set('per_page', String(state.perPage));
    u.searchParams.set('episodes_limit', String(MAX_RENDERED_EPISODES));
    u.searchParams.set('sort_by', state.sortBy);
    u.searchParams.set('sort_dir', state.sortDir);
    if (state.titleFilter) u.searchParams.set('title_filter', state.titleFilter);
    if (state.artistFilter) u.searchParams.set('artist_filter', state.artistFilter);
    // Prefer exact show URL when a suggestion was selected
    if (state.showUrl) u.searchParams.set('show_url', state.showUrl);
    else if (state.showFilter) u.searchParams.set('show_filter', state.showFilter);
    if (state.genres) u.searchParams.set('genres', state.genres);
    // Prefer exact episode ID when a suggestion was selected
    if (state.episodeId) u.searchParams.set('episode_id', state.episodeId);
    else if (state.episodeFilter) u.searchParams.set('episode_filter', state.episodeFilter);
    return u.toString();
  }

  // ------------------------------------------------------------------
  // Episode deduplication (Unicode-safe)
  // ------------------------------------------------------------------
  function normalizeUnicode(str){
    try { return String(str || '').normalize('NFKC').trim(); }
    catch(_){ return String(str || '').trim(); }
  }

  function episodeKey(ep){
    if (ep && (ep.id !== undefined && ep.id !== null)) return 'id:' + String(ep.id);
    var url = normalizeUnicode(ep && ep.url);
    if (url) return 'url:' + url;
    var show = normalizeUnicode((ep && (ep.show_url || ep.show_title)) || '');
    var date = normalizeUnicode(ep && ep.date);
    var title = normalizeUnicode(ep && ep.title);
    return 'f:' + show + '|' + date + '|' + title;
  }

  function dedupeEpisodes(episodes){
    var seen = new Set();
    var out = [];
    (episodes || []).forEach(function(ep){
      var k = episodeKey(ep);
      if (seen.has(k)) return;
      seen.add(k);
      out.push(ep);
    });
    return out;
  }

  // ------------------------------------------------------------------
  // Row rendering - optimized with DocumentFragment and minimal DOM ops
  // ------------------------------------------------------------------

  var MAX_RENDERED_EPISODES = 6;

  // Pre-built episode HTML renderer - capped to keep payload and DOM lighter
  function renderEpisodesList(episodes){
    if (!episodes || !episodes.length) return '';
    var unique = dedupeEpisodes(episodes);
    var items = '';
    var len = Math.min(unique.length, MAX_RENDERED_EPISODES);

    for (var i = 0; i < len; i++) {
      var ep = unique[i];
      var rawShow = String(ep.show_title || '').trim();
      var rawTitle = String(ep.title || '').trim();
      var showLc = rawShow.toLowerCase();
      var titleLc = rawTitle.toLowerCase();
      var showPart = rawShow;
      var titlePart = rawTitle;

      if (!rawShow) {
        showPart = '';
        titlePart = rawTitle || 'Episode';
      } else if (showLc === titleLc) {
        showPart = '';
        titlePart = rawTitle || rawShow;
      } else if (titleLc.startsWith(showLc)) {
        titlePart = rawTitle.slice(rawShow.length).trim().replace(/^[-–—:|]+\s*/, '');
        if (!titlePart) titlePart = rawTitle;
      }

      items += '<div class="episode-entry"><div class="episode-header">'
        + (showPart ? '<span class="episode-show">' + escapeHtml(showPart) + '</span>' : '')
        + (ep.date ? '<span class="episode-date">' + escapeHtml(ep.date) + '</span>' : '')
        + '</div><a href="/show/' + encodeURIComponent(ep.show_url || '') + '#ep=' + encodeURIComponent(ep.url || '')
        + '" class="episode-title" title="' + escapeHtml(titlePart || 'Episode') + '">'
        + escapeHtml(titlePart || 'Episode') + '</a></div>';
    }

    var moreText = unique.length > MAX_RENDERED_EPISODES ? '<div class="episodes-more">+' + (unique.length - MAX_RENDERED_EPISODES) + ' more</div>' : '';
    return '<div class="episode-list">' + items + moreText + '</div>';
  }

  // Filter out unknown-artist placeholder tracks
  var _unknownSet = new Set(['unknown artist', 'artist unknown', '', '-', '\u2014', '\u2013', 'n/a', 'na', 'unknown']);
  function isUnknownArtistTrack(t){
    var artists = (t && Array.isArray(t.artists)) ? t.artists : [];
    if (!artists.length) return true;
    return artists.every(function(a){ return _unknownSet.has(String(a || '').trim().toLowerCase()); });
  }

  // Render a single row's HTML into a <tr> element
  function buildRow(r){
    var row = document.createElement('tr');
    row.className = 'stats-row';
    row.setAttribute('data-track-id', String(r.id));
    var artistStr = (r.artists || []).join(', ') || 'Unknown Artist';
    var titleStr = r.title || 'Unknown Title';

    row.innerHTML =
      '<td class="col col-title" title="' + escapeHtml(r.title || '') + '"><button class="track-youtube-btn" data-track-id="' + String(r.id) + '" data-artist="' + escapeHtml(artistStr) + '" data-title="' + escapeHtml(titleStr) + '" title="Play on YouTube" aria-label="Play ' + escapeHtml(titleStr) + ' on YouTube"><i class="fab fa-youtube"></i></button><span class="track-name">' + escapeHtml(r.title || '') + '</span></td>'
      + '<td class="col col-artists">' + (r.artists || []).map(function(a){ return '<span class="chip">' + escapeHtml(a) + '</span>'; }).join(' ') + '</td>'
      + '<td class="col col-plays">' + formatNum(r.play_count) + '</td>'
      + '<td class="col col-shows">' + formatNum(r.shows_count) + '</td>'
      + '<td class="col col-genres">' + (r.top_genres || []).map(function(g){ return '<span class="chip">' + escapeHtml(g) + '</span>'; }).join(' ') + '</td>'
      + '<td class="col col-episodes">' + renderEpisodesList(r.all_episodes || []) + '</td>';
    return row;
  }

  // Progressive row rendering: first chunk is synchronous (fills viewport),
  // remaining chunks render during idle frames to keep the UI responsive.
  var FIRST_CHUNK = 30;   // immediate render — fills ~viewport
  var IDLE_CHUNK  = 20;   // rows per idle callback
  var _renderRafId = null;

  function renderRows(startIndex){
    var rowsEl = $('statsRows');
    if (!rowsEl) return;

    // Cancel any in-progress progressive render from a previous batch
    if (_renderRafId) { cancelAnimationFrame(_renderRafId); _renderRafId = null; }

    var end = state.rows.length;
    if (startIndex >= end) return;

    // Synchronous first chunk — paint above-the-fold rows immediately
    var firstEnd = Math.min(startIndex + FIRST_CHUNK, end);
    var frag = document.createDocumentFragment();
    for (var i = startIndex; i < firstEnd; i++) frag.appendChild(buildRow(state.rows[i]));
    rowsEl.appendChild(frag);
    syncPlayingRowWithPlayer({ scroll: false });

    // Remaining rows via requestAnimationFrame chunks
    if (firstEnd < end) {
      var cursor = firstEnd;
      function renderChunk(){
        var chunkEnd = Math.min(cursor + IDLE_CHUNK, end);
        var f = document.createDocumentFragment();
        for (var j = cursor; j < chunkEnd; j++) f.appendChild(buildRow(state.rows[j]));
        rowsEl.appendChild(f);
        syncPlayingRowWithPlayer({ scroll: false });
        cursor = chunkEnd;
        if (cursor < end) {
          _renderRafId = requestAnimationFrame(renderChunk);
        } else {
          _renderRafId = null;
        }
      }
      _renderRafId = requestAnimationFrame(renderChunk);
    }
  }

  // ------------------------------------------------------------------
  // Sorting
  // ------------------------------------------------------------------
  function setSort(sortBy){
    if (!sortBy) return;
    if (state.sortBy === sortBy){
      state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      state.sortBy = sortBy;
      state.sortDir = (sortBy === 'title' || sortBy === 'first_seen') ? 'asc' : 'desc';
    }
    // Sync selects if they exist
    var sb = $('sortBy'), sd = $('sortDir');
    if (sb) sb.value = state.sortBy;
    if (sd) sd.value = state.sortDir;
    // Update header indicators
    var header = $('statsHeader');
    if (header){
      header.querySelectorAll('.sortable').forEach(function(el){
        var ind = el.querySelector('.sort-ind');
        if (!ind) return;
        if (el.getAttribute('data-sort') === state.sortBy){
          ind.textContent = state.sortDir === 'asc' ? '\u25B2' : '\u25BC';
          ind.style.opacity = '1';
          el.setAttribute('aria-sort', state.sortDir === 'asc' ? 'ascending' : 'descending');
        } else {
          ind.textContent = '';
          ind.style.opacity = '.3';
          el.setAttribute('aria-sort', 'none');
        }
      });
    }
    resetAndLoad();
  }

  // ------------------------------------------------------------------
  // Header interactions: sort + column resize
  // ------------------------------------------------------------------
  function setupHeaderInteractions(){
    var header = $('statsHeader');
    if (!header) return;

    bind(header, 'click', function(e){
      var target = e.target.closest('.sortable');
      if (!target) return;
      setSort(target.getAttribute('data-sort'));
    });
    bind(header, 'keydown', function(e){
      if (e.key === 'Enter' || e.key === ' '){
        var target = e.target.closest('.sortable');
        if (!target) return;
        e.preventDefault();
        setSort(target.getAttribute('data-sort'));
      }
    });

    // Column resizing with pointer capture for smooth UX
    var isDragging = false;
    var startX = 0;
    var colIndex = '-1';
    var startWidth = 0;
    var currentWidths = getColumnWidths();

    var columnConstraints = {
      '-1': { min: 40, max: 40 },
      0: { min: 150, max: 500 },
      1: { min: 120, max: 400 },
      2: { min: 60, max: 120 },
      3: { min: 60, max: 120 },
      4: { min: 100, max: 300 },
      5: { min: 300, max: 800 }
    };

    setColumnWidths(currentWidths, false);

    var _raf = null;
    function onPointerMove(e){
      if (!isDragging) return;
      e.preventDefault();
      var dx = e.clientX - startX;
      var constraints = columnConstraints[colIndex] || { min: 60, max: 800 };
      currentWidths[colIndex] = Math.round(Math.max(constraints.min, Math.min(constraints.max, startWidth + dx)));
      if (!_raf){
        _raf = requestAnimationFrame(function(){
          setColumnWidths(currentWidths, false);
          _raf = null;
        });
      }
    }

    function onPointerUp(){
      if (!isDragging) return;
      isDragging = false;
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      document.body.classList.remove('col-resizing');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      var activeCol = header.querySelector('.col-resizing');
      if (activeCol) activeCol.classList.remove('col-resizing');
      setColumnWidths(currentWidths, true);
    }

    bind(header, 'pointerdown', function(e){
      var handle = e.target.closest('.col-resizer');
      if (!handle) return;
      e.preventDefault();
      e.stopPropagation();
      try { e.target.setPointerCapture(e.pointerId); } catch(_){}

      isDragging = true;
      startX = e.clientX;
      colIndex = handle.getAttribute('data-col');
      var col = handle.closest('.col');
      startWidth = col ? Math.round(col.getBoundingClientRect().width) : (currentWidths[colIndex] || 100);

      document.body.classList.add('col-resizing');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      if (col) col.classList.add('col-resizing');
      window.addEventListener('pointermove', onPointerMove);
      window.addEventListener('pointerup', onPointerUp);
    });

    registerCleanup(function(){
      if (_raf) {
        cancelAnimationFrame(_raf);
        _raf = null;
      }
      isDragging = false;
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      document.body.classList.remove('col-resizing');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      var activeCol = header.querySelector('.col-resizing');
      if (activeCol) activeCol.classList.remove('col-resizing');
    });
  }

  // ------------------------------------------------------------------
  // Data fetching with AbortController + version guard
  // ------------------------------------------------------------------
  var _fetchAbort = null;
  var _fetchVersion = 0;

  function setLoadingState(loading) {
    state.loading = loading;
    var wrapper = document.querySelector('.stats-table-wrapper');
    if (wrapper) {
      wrapper.classList.toggle('is-loading', loading);
    }
    var table = $('statsTable');
    if (table) {
      table.setAttribute('aria-busy', loading.toString());
    }
  }

  async function fetchPage(){
    if (state.loading || destroyed) return;
    setLoadingState(true);

    // Cancel previous in-flight request
    if (_fetchAbort) { try { _fetchAbort.abort(); } catch(_){} }
    _fetchAbort = new AbortController();

    var thisVersion = ++_fetchVersion;
    try{
      var url = buildUrl();
      var res = await fetch(url, { signal: _fetchAbort.signal });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var j = await res.json();
      // Discard stale responses
      if (destroyed || thisVersion !== _fetchVersion) return;
      if (!j || !j.success){ setLoadingState(false); return; }
      state.total = j.total || 0;
      var statusEl = $('statsStatus');
      if (statusEl) {
        statusEl.textContent = state.total + ' track' + (state.total !== 1 ? 's' : '') + ' found';
      }
      var startIndex = state.rows.length;
      var incoming = (j.tracks || []).filter(function(t){ return !isUnknownArtistTrack(t); });
      for (var i = 0; i < incoming.length; i++) state.rows.push(incoming[i]);
      renderRows(startIndex);
      // Empty state
      if (state.rows.length === 0) {
        var rowsEl = $('statsRows');
        if (rowsEl) rowsEl.innerHTML = '<tr><td colspan="7" class="stats-empty-state">No tracks found matching your filters.</td></tr>';
      }
      // End-of-list indicator
      if (!j.has_more && state.rows.length > 0) {
        var rowsEl2 = $('statsRows');
        if (rowsEl2) {
          var footer = document.createElement('tr');
          footer.innerHTML = '<td colspan="7" class="stats-empty-state">All ' + state.total + ' tracks loaded</td>';
          rowsEl2.appendChild(footer);
        }
        if (_scrollObserver) { _scrollObserver.disconnect(); _scrollObserver = null; }
      }
    } catch(e){
      if (destroyed) return;
      if (e && e.name === 'AbortError') return;
      // Error state for non-abort errors
      if (window.showNotification) showNotification('Failed to load tracks', 'error');
      var rowsEl = $('statsRows');
      if (rowsEl && state.rows.length === 0) {
        rowsEl.innerHTML = '<tr><td colspan="7" class="stats-error-state">Failed to load tracks. <button class="stats-retry-btn">Retry</button></td></tr>';
        rowsEl.querySelector('.stats-retry-btn')?.addEventListener('click', resetAndLoad);
      }
    }
    finally{
      if (destroyed) return;
      if (thisVersion === _fetchVersion) {
        setLoadingState(false);
        // If observer fired while loading, trigger next page now
        if (_pendingNextPage) {
          _pendingNextPage = false;
          if ((state.page * state.perPage) < state.total) {
            state.page += 1;
            fetchPage();
          }
        }
      }
    }
  }

  function resetAndLoad(){
    // Cancel any in-flight request immediately
    if (_fetchAbort) { try { _fetchAbort.abort(); } catch(_){} }
    _fetchAbort = null;
    // Cancel any in-progress progressive render
    if (_renderRafId) { cancelAnimationFrame(_renderRafId); _renderRafId = null; }
    state.page = 1;
    state.rows = [];
    state.total = 0;
    state.loading = false;
    var rowsEl = $('statsRows');
    if (rowsEl) rowsEl.innerHTML = '';
    fetchPage();
  }

  function onControlsChanged(){
    // Cancel any pending debounced search to avoid redundant reloads
    if (_inputTimer) { clearTimeout(_inputTimer); _inputTimer = null; }
    readControls();
    resetAndLoad();
  }

  // Debounced search input - 250ms for snappy feel
  var _inputTimer = null;
  function onSearchInput(){
    if (_inputTimer) clearTimeout(_inputTimer);
    _inputTimer = setTimeout(onControlsChanged, 250);
  }

  // ------------------------------------------------------------------
  // Infinite scroll via IntersectionObserver
  // ------------------------------------------------------------------
  var _scrollObserver = null;
  var _pendingNextPage = false;
  function setupInfiniteScroll(){
    var sentinel = $('statsLoadMoreTrigger');
    if (!sentinel) return;
    if (_scrollObserver) { _scrollObserver.disconnect(); _scrollObserver = null; }
    _scrollObserver = new IntersectionObserver(function(entries){
      for (var i = 0; i < entries.length; i++){
        if (entries[i].isIntersecting){
          if (state.loading) {
            _pendingNextPage = true;
          } else if ((state.page * state.perPage) < state.total){
            state.page += 1;
            fetchPage();
          }
        }
      }
    }, { root: null, rootMargin: '600px', threshold: 0 });
    _scrollObserver.observe(sentinel);
  }

  // ------------------------------------------------------------------
  // Autocomplete suggestions for Show, Genre, Episode fields
  // ------------------------------------------------------------------
  var _genresCache = null;           // cached genre list from /api/genres
  var _suggestAbort = {};            // per-field AbortControllers
  var _suggestTimers = {};           // per-field debounce timers
  var _suggestFocus = {};            // per-field keyboard focus index
  var _blurTimers = {};              // delayed dropdown hide timers
  var _genreAutocompleteTimer = null;

  function highlightMatch(text, query){
    if (!query) return escapeHtml(text);
    var rawIdx = text.toLowerCase().indexOf(query.toLowerCase());
    if (rawIdx < 0) return escapeHtml(text);
    var before = escapeHtml(text.substring(0, rawIdx));
    var match = escapeHtml(text.substring(rawIdx, rawIdx + query.length));
    var after = escapeHtml(text.substring(rawIdx + query.length));
    return before + '<span class="suggestion-highlight">' + match + '</span>' + after;
  }

  function showSuggestionsDropdown(dropdownId, items, query, onSelect){
    var dd = $(dropdownId);
    if (!dd) return;
    _suggestFocus[dropdownId] = -1;

    if (!items || !items.length){
      dd.innerHTML = '';
      dd.classList.remove('visible');
      return;
    }

    var html = '';
    for (var i = 0; i < items.length; i++){
      var item = items[i];
      html += '<div class="suggestion-item" data-index="' + i + '">';
      if (item.secondary){
        html += '<span class="suggestion-primary">' + highlightMatch(item.label, query) + '</span>';
        html += '<span class="suggestion-secondary">' + escapeHtml(item.secondary) + '</span>';
      } else {
        html += '<span class="suggestion-primary">' + highlightMatch(item.label, query) + '</span>';
      }
      html += '</div>';
    }
    dd.innerHTML = html;
    dd.classList.add('visible');

    // Click handlers on items
    dd.querySelectorAll('.suggestion-item').forEach(function(el){
      el.addEventListener('mousedown', function(e){
        e.preventDefault(); // prevent input blur
        var idx = parseInt(el.getAttribute('data-index'), 10);
        if (items[idx]) onSelect(items[idx]);
        hideSuggestionsDropdown(dropdownId);
      });
    });
  }

  function scheduleHideSuggestions(dropdownId){
    if (_blurTimers[dropdownId]) clearTimeout(_blurTimers[dropdownId]);
    _blurTimers[dropdownId] = setTimeout(function(){
      hideSuggestionsDropdown(dropdownId);
      _blurTimers[dropdownId] = null;
    }, 150);
  }

  function hideSuggestionsDropdown(dropdownId){
    var dd = $(dropdownId);
    if (dd){
      dd.innerHTML = '';
      dd.classList.remove('visible');
    }
    _suggestFocus[dropdownId] = -1;
    // Cancel any pending suggest debounce for this field to prevent it from
    // clearing the exact ID that was just set by the selection callback
    var fieldMap = { showSuggestions: 'show', genreSuggestions: 'genre', episodeSuggestions: 'episode' };
    var field = fieldMap[dropdownId];
    if (field && _suggestTimers[field]){
      clearTimeout(_suggestTimers[field]);
      _suggestTimers[field] = null;
    }
  }

  function navigateSuggestions(dropdownId, direction, items, onSelect){
    var dd = $(dropdownId);
    if (!dd || !dd.classList.contains('visible')) return false;
    var children = dd.querySelectorAll('.suggestion-item');
    if (!children.length) return false;

    var cur = (dropdownId in _suggestFocus) ? _suggestFocus[dropdownId] : -1;
    cur += direction;
    if (cur < 0) cur = children.length - 1;
    if (cur >= children.length) cur = 0;
    _suggestFocus[dropdownId] = cur;

    children.forEach(function(el, i){
      el.classList.toggle('active', i === cur);
      if (i === cur) el.scrollIntoView({ block: 'nearest' });
    });
    return true;
  }

  function selectActiveSuggestion(dropdownId, items, onSelect){
    var idx = _suggestFocus[dropdownId];
    if (idx >= 0 && items && items[idx]){
      onSelect(items[idx]);
      hideSuggestionsDropdown(dropdownId);
      return true;
    }
    return false;
  }

  // --- Show suggestions ---
  var _showSuggestItems = [];
  function fetchShowSuggestions(query){
    if (_suggestAbort.show) { try { _suggestAbort.show.abort(); } catch(_){} }
    if (!query || query.length < 1){
      hideSuggestionsDropdown('showSuggestions');
      _showSuggestItems = [];
      return;
    }
    _suggestAbort.show = new AbortController();
    fetch('/api/shows/search?q=' + encodeURIComponent(query), { signal: _suggestAbort.show.signal })
      .then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function(data){
        if (destroyed) return;
        if (!data || !data.success) return;
        var ql = query.toLowerCase();
        var sorted = (data.shows || []).slice().sort(function(a, b){
          var aTitle = (a.title || '').toLowerCase();
          var bTitle = (b.title || '').toLowerCase();
          var aStarts = aTitle.startsWith(ql) ? 0 : (aTitle.indexOf(ql) >= 0 ? 1 : 2);
          var bStarts = bTitle.startsWith(ql) ? 0 : (bTitle.indexOf(ql) >= 0 ? 1 : 2);
          return aStarts - bStarts;
        });
        _showSuggestItems = sorted.slice(0, 15).map(function(s){
          return { label: s.title, value: s.title, url: s.url, id: s.id };
        });
        showSuggestionsDropdown('showSuggestions', _showSuggestItems, query, function(item){
          var el = $('showFilter');
          if (el) { el.value = item.value; }
          state.showUrl = item.url || '';
          onControlsChanged();
        });
      })
      .catch(function(e){ if (e && e.name !== 'AbortError') _showSuggestItems = []; });
  }

  // --- Genre suggestions (client-side from cached list) ---
  var _genreSuggestItems = [];
  function ensureGenresCache(cb){
    if (_genresCache){
      cb(_genresCache);
      return;
    }
    fetch('/api/genres')
      .then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function(data){
        if (destroyed) return;
        if (data && data.success){
          _genresCache = data.genres || [];
        } else {
          _genresCache = [];
        }
        cb(_genresCache);
      })
      .catch(function(){ _genresCache = []; cb(_genresCache); });
  }

  function filterGenreSuggestions(query){
    if (!query || query.length < 1){
      hideSuggestionsDropdown('genreSuggestions');
      _genreSuggestItems = [];
      return;
    }
    ensureGenresCache(function(genres){
      if (destroyed) return;
      var q = query.toLowerCase();
      // already-entered genres (comma-separated)
      var existing = (($('genresInput') || {}).value || '').split(',').map(function(g){ return g.trim().toLowerCase(); });
      var filtered = genres.filter(function(g){
        return g.toLowerCase().indexOf(q) >= 0 && existing.indexOf(g.toLowerCase()) < 0;
      }).sort(function(a, b){
        // Prioritize starts-with matches
        var aStarts = a.toLowerCase().startsWith(q) ? 0 : 1;
        var bStarts = b.toLowerCase().startsWith(q) ? 0 : 1;
        return aStarts - bStarts;
      }).slice(0, 20);
      _genreSuggestItems = filtered.map(function(g){ return { label: g, value: g }; });
      showSuggestionsDropdown('genreSuggestions', _genreSuggestItems, query, function(item){
        var el = $('genresInput');
        if (el){
          // Replace the current partially-typed genre with the selected one
          var parts = el.value.split(',');
          parts[parts.length - 1] = item.value;
          el.value = parts.join(', ') + ', ';
        }
        onControlsChanged();
        // Re-show suggestions for next genre
        if (_genreAutocompleteTimer) clearTimeout(_genreAutocompleteTimer);
        _genreAutocompleteTimer = setTimeout(function(){ triggerGenreAutocomplete(); }, 50);
      });
    });
  }

  function triggerGenreAutocomplete(){
    var el = $('genresInput');
    if (!el) return;
    // Use the text after the last comma as the search query
    var parts = el.value.split(',');
    var current = (parts[parts.length - 1] || '').trim();
    filterGenreSuggestions(current);
  }

  // --- Episode suggestions ---
  var _episodeSuggestItems = [];
  function fetchEpisodeSuggestions(query){
    if (_suggestAbort.episode) { try { _suggestAbort.episode.abort(); } catch(_){} }
    if (!query || query.length < 2){
      hideSuggestionsDropdown('episodeSuggestions');
      _episodeSuggestItems = [];
      return;
    }
    _suggestAbort.episode = new AbortController();
    fetch('/api/episodes/search?q=' + encodeURIComponent(query), { signal: _suggestAbort.episode.signal })
      .then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function(data){
        if (destroyed) return;
        if (!data || !data.success) return;
        var ql = query.toLowerCase();
        var sorted = (data.episodes || []).slice().sort(function(a, b){
          var aTitle = (a.title || '').toLowerCase();
          var bTitle = (b.title || '').toLowerCase();
          var aStarts = aTitle.startsWith(ql) ? 0 : (aTitle.indexOf(ql) >= 0 ? 1 : 2);
          var bStarts = bTitle.startsWith(ql) ? 0 : (bTitle.indexOf(ql) >= 0 ? 1 : 2);
          return aStarts - bStarts;
        });
        _episodeSuggestItems = sorted.slice(0, 20).map(function(ep){
          return {
            label: ep.title || 'Episode',
            secondary: (ep.show_title ? ep.show_title : '') + (ep.date ? ' \u00B7 ' + ep.date : ''),
            value: ep.title || '',
            id: ep.id,
          };
        });
        showSuggestionsDropdown('episodeSuggestions', _episodeSuggestItems, query, function(item){
          var el = $('episodeFilterInput');
          if (el) { el.value = item.value; }
          state.episodeId = item.id ? String(item.id) : '';
          onControlsChanged();
        });
      })
      .catch(function(e){ if (e && e.name !== 'AbortError') _episodeSuggestItems = []; });
  }

  // Debounced suggestion fetchers
  function debouncedSuggest(field, fn, delay){
    return function(){
      if (_suggestTimers[field]) clearTimeout(_suggestTimers[field]);
      _suggestTimers[field] = setTimeout(fn, delay || 200);
    };
  }

  var onShowSuggestInput = debouncedSuggest('show', function(){
    state.showUrl = ''; // clear exact match; user is typing freely
    var v = (($('showFilter') || {}).value || '').trim();
    fetchShowSuggestions(v);
  }, 200);

  var onGenreSuggestInput = debouncedSuggest('genre', function(){
    triggerGenreAutocomplete();
  }, 150);

  var onEpisodeSuggestInput = debouncedSuggest('episode', function(){
    state.episodeId = ''; // clear exact match; user is typing freely
    var v = (($('episodeFilterInput') || {}).value || '').trim();
    fetchEpisodeSuggestions(v);
  }, 200);

  // Keyboard handler for suggestion fields
  function makeSuggestKeyHandler(dropdownId, getItems, onSelect){
    return function(e){
      if (e.key === 'ArrowDown'){
        if (navigateSuggestions(dropdownId, 1, getItems(), onSelect)){ e.preventDefault(); }
      } else if (e.key === 'ArrowUp'){
        if (navigateSuggestions(dropdownId, -1, getItems(), onSelect)){ e.preventDefault(); }
      } else if (e.key === 'Enter'){
        if (selectActiveSuggestion(dropdownId, getItems(), onSelect)){ e.preventDefault(); }
      } else if (e.key === 'Escape'){
        hideSuggestionsDropdown(dropdownId);
      }
    };
  }

  registerCleanup(function(){
    Object.keys(_suggestAbort).forEach(function(key){
      if (_suggestAbort[key]) {
        try { _suggestAbort[key].abort(); } catch(_) {}
        _suggestAbort[key] = null;
      }
    });
    Object.keys(_suggestTimers).forEach(function(key){
      if (_suggestTimers[key]) {
        clearTimeout(_suggestTimers[key]);
        _suggestTimers[key] = null;
      }
    });
    Object.keys(_blurTimers).forEach(function(key){
      if (_blurTimers[key]) {
        clearTimeout(_blurTimers[key]);
        _blurTimers[key] = null;
      }
    });
    if (_genreAutocompleteTimer) {
      clearTimeout(_genreAutocompleteTimer);
      _genreAutocompleteTimer = null;
    }
    hideSuggestionsDropdown('showSuggestions');
    hideSuggestionsDropdown('genreSuggestions');
    hideSuggestionsDropdown('episodeSuggestions');
  });

  // ------------------------------------------------------------------
  // Wire up controls for the current page instance
  // ------------------------------------------------------------------
  bind(document, 'click', function(e){
    if (!root.contains(e.target) || !e.target.closest('.filter-input-wrap')){
      hideSuggestionsDropdown('showSuggestions');
      hideSuggestionsDropdown('genreSuggestions');
      hideSuggestionsDropdown('episodeSuggestions');
    }
  });

  bind($('statsRows'), 'click', handleYouTubeClick);

  ['perPage', 'showFilter', 'genresInput'].forEach(function(id){
    bind($(id), 'change', onControlsChanged);
  });

  ['titleFilter', 'artistFilter', 'showFilter', 'genresInput', 'episodeFilterInput'].forEach(function(id){
    bind($(id), 'input', onSearchInput);
  });

  var showEl = $('showFilter');
  if (showEl){
    bind(showEl, 'input', onShowSuggestInput);
    bind(showEl, 'keydown', makeSuggestKeyHandler('showSuggestions',
      function(){ return _showSuggestItems; },
      function(item){ showEl.value = item.value; state.showUrl = item.url || ''; onControlsChanged(); }
    ));
    bind(showEl, 'blur', function(){ scheduleHideSuggestions('showSuggestions'); });
  }

  var genreEl = $('genresInput');
  if (genreEl){
    bind(genreEl, 'input', onGenreSuggestInput);
    bind(genreEl, 'keydown', makeSuggestKeyHandler('genreSuggestions',
      function(){ return _genreSuggestItems; },
      function(item){
        var parts = genreEl.value.split(',');
        parts[parts.length - 1] = item.value;
        genreEl.value = parts.join(', ') + ', ';
        onControlsChanged();
        if (_genreAutocompleteTimer) clearTimeout(_genreAutocompleteTimer);
        _genreAutocompleteTimer = setTimeout(function(){ triggerGenreAutocomplete(); }, 50);
      }
    ));
    bind(genreEl, 'blur', function(){ scheduleHideSuggestions('genreSuggestions'); });
    bind(genreEl, 'focus', function(){ ensureGenresCache(function(){}); });
  }

  var epEl = $('episodeFilterInput');
  if (epEl){
    bind(epEl, 'input', onEpisodeSuggestInput);
    bind(epEl, 'keydown', makeSuggestKeyHandler('episodeSuggestions',
      function(){ return _episodeSuggestItems; },
      function(item){ epEl.value = item.value; state.episodeId = item.id ? String(item.id) : ''; onControlsChanged(); }
    ));
    bind(epEl, 'blur', function(){ scheduleHideSuggestions('episodeSuggestions'); });
  }

  root.querySelectorAll('.stats-controls .filter-clear').forEach(function(btn){
    bind(btn, 'click', function(){
      var wrap = btn.closest('.filter-input-wrap');
      var input = wrap && wrap.querySelector('input');
      if (input) {
        input.value = '';
        if (input.id === 'showFilter') state.showUrl = '';
        if (input.id === 'episodeFilterInput') state.episodeId = '';
        var dd = wrap.querySelector('.suggestions-dropdown');
        if (dd){ dd.innerHTML = ''; dd.classList.remove('visible'); }
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus();
        onControlsChanged();
      }
    });
  });

  if (window.MutationObserver) {
    var playerRoot = $('youtube-player-root');
    if (playerRoot) {
      var playerObserver = new MutationObserver(function(){
        syncPlayingRowWithPlayer({ scroll: false });
      });
      playerObserver.observe(playerRoot, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['class']
      });
      registerCleanup(function(){
        playerObserver.disconnect();
      });
    }
  }

  setupHeaderInteractions();

  // Initial data load (run on every SPA navigation)
  readControls();
  setupInfiniteScroll();
  resetAndLoad();
  syncPlayingRowWithPlayer({ scroll: false });

  registerCleanup(function(){
    clearPlayingRow();
    if (_inputTimer) {
      clearTimeout(_inputTimer);
      _inputTimer = null;
    }
    if (_fetchAbort) {
      try { _fetchAbort.abort(); } catch(_) {}
      _fetchAbort = null;
    }
    if (_scrollObserver) {
      _scrollObserver.disconnect();
      _scrollObserver = null;
    }
    if (_renderRafId) {
      cancelAnimationFrame(_renderRafId);
      _renderRafId = null;
    }
    _pendingNextPage = false;
    setLoadingState(false);
  });

  return { cleanup: cleanup };
  } // end initStatsPageCore
})();
