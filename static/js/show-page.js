const DOWNLOAD_STATE_KEY = 'activeDownloadState';
const TARGET_HIGHLIGHT_DURATION_MS = 2500;
const LARGE_SCROLL_DISTANCE_PX = 1800;

/**
 * Request deduplication - prevents duplicate concurrent requests to the same endpoint.
 * If a request to the same URL is already in-flight, returns the existing promise.
 */
const pendingRequests = new Map();

/**
 * Track which show URLs have had mark_read called to prevent duplicates.
 * This is module-scoped to persist across re-initializations.
 */
const markedAsReadShows = new Set();

/**
 * Track resources that need cleanup during SPA navigation.
 * Prevents memory leaks from observers, event listeners, and timers.
 */
const showPageCleanupResources = {
    tracklistObserver: null,
    infiniteScrollObserver: null,
    scrollHandler: null,
    resizeHandler: null,
};

function normalizeEpisodeUrl(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    try {
        const parsed = new URL(text, window.location.origin);
        const pathname = parsed.pathname.replace(/\/+$/, '') || '/';
        return `${parsed.protocol}//${parsed.host}${pathname}`;
    } catch (_) {
        return text.replace(/[?#].*$/, '').replace(/\/+$/, '');
    }
}

function normalizeSearchText(value) {
    return String(value || '')
        .normalize('NFKD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/&/g, ' and ')
        .replace(/[^a-z0-9]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function tokenizeSearchText(value) {
    const normalized = normalizeSearchText(value);
    return normalized ? normalized.split(' ') : [];
}

function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, (char) => (
        {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            '\'': '&#39;',
        }[char] || char
    ));
}

function escapeRegExp(value) {
    return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function episodeItemKey(episodeItem) {
    return normalizeEpisodeUrl(
        episodeItem?.dataset?.episodeUrl
        || episodeItem?.querySelector('.episode-title')?.href
        || '',
    );
}

function findEpisodeItemByUrl(root, episodeUrl) {
    const normalizedTarget = normalizeEpisodeUrl(episodeUrl);
    if (!root || !normalizedTarget) return null;
    return Array.from(root.querySelectorAll('.episode-item')).find((item) => (
        episodeItemKey(item) === normalizedTarget
    )) || null;
}

function getThumbnailSrc(imageUrl) {
    if (!imageUrl) return '';
    return `/thumbnail?url=${encodeURIComponent(imageUrl)}`;
}

function renderEpisodeItem(episode, options = {}) {
    const { temporary = false } = options;
    const item = document.createElement('div');
    const episodeSlug = episode?.url ? String(episode.url).split('/').filter(Boolean).pop() : '';
    const classes = ['episode-item'];
    if (episode?.is_new) classes.push('new');
    if (episode?.is_downloaded) classes.push('downloaded');
    item.className = classes.join(' ');
    if (episodeSlug) item.id = `episode-${episodeSlug}`;
    if (episode?.url) item.dataset.episodeUrl = normalizeEpisodeUrl(episode.url);
    if (temporary) item.dataset.episodeTemporary = 'true';

    const imageSrc = getThumbnailSrc(episode?.image_url);
    const tracklist = Array.isArray(episode?.tracklist) ? episode.tracklist : [];
    item.innerHTML = `
        <div class="episode-left-column">
            ${imageSrc ? `<div class="episode-thumbnail"><img src="${imageSrc}" alt="${escapeHtml(episode?.title)}" loading="lazy"></div>` : ''}
            <div class="episode-download-section">
                ${episode?.is_downloaded ? `
                    <button class="download-button downloaded" disabled>
                        <span class="button-text">Downloaded</span>
                    </button>` : `
                    <a href="/download_episode/${encodeURIComponent(episode?.audio_url || '')}" class="download-button">
                        <span class="button-text">Download mix</span>
                    </a>`}
                <div class="download-progress">
                    <div class="progress-bar"></div>
                    <div class="progress-info"></div>
                    <button class="cancel-download">Cancel Download</button>
                </div>
            </div>
        </div>
        <div class="episode-info">
            <div class="episode-title-container">
                <a href="${escapeHtml(episode?.url || '')}" class="episode-title" target="_blank">${escapeHtml(episode?.title || '')}</a>
                ${episode?.is_new ? '<span class="new-badge">NEW</span>' : ''}
            </div>
            <span class="episode-date">${escapeHtml(episode?.date || '')}</span>
            ${Array.isArray(episode?.genres) && episode.genres.length ? `
                <div class="episode-genres">
                    ${episode.genres.map((genre) => `<span class="genre-tag">${escapeHtml(genre)}</span>`).join('')}
                </div>` : ''}
            ${tracklist.length ? `
                <div class="episode-tracklist">
                    <div class="tracklist-header"><h3>Tracklist</h3></div>
                    <ul class="tracks-list">
                        ${tracklist.map((track) => `
                            <li class="track-item">
                                <button class="track-like-btn" data-artist="${escapeHtml(track?.artist || '')}" data-title="${escapeHtml(track?.name || '')}" title="Like track">
                                    <i class="far fa-heart"></i>
                                </button>
                                <button class="track-download-btn" data-artist="${escapeHtml(track?.artist || '')}" data-title="${escapeHtml(track?.name || '')}">
                                    <span class="loading-spinner"></span>
                                    <svg class="button-icon" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none">
                                        <path d="M1.7422 11.982c0-5.6682 4.61-10.2782 10.2758-10.2782 1.8238 0 3.5372.48 5.0251 1.3175l.8135-1.4879C16.1768.588 14.2474.036 12.1908.0024h-.1944C5.4091.0144.072 5.3107 0 11.886v.1152c.0072 3.4389 1.4567 6.5345 3.7748 8.7207l1.1855-1.2814c-1.9798-1.8743-3.218-4.526-3.218-7.4585zM20.362 3.4053l-1.1543 1.2406c1.903 1.867 3.0885 4.4636 3.0885 7.3361 0 5.6658-4.61 10.2758-10.2758 10.2758-1.783 0-3.4605-.456-4.922-1.2575l-.8542 1.5214c1.7086.9384 3.6692 1.4735 5.7546 1.4759C18.6245 23.9976 24 18.6246 24 11.9988c-.0048-3.3717-1.399-6.4146-3.638-8.5935zM1.963 11.982c0 2.8701 1.2119 5.4619 3.146 7.2953l1.1808-1.2767c-1.591-1.5166-2.587-3.6524-2.587-6.0186 0-4.586 3.7293-8.3152 8.3152-8.3152 1.483 0 2.875.3912 4.082 1.0751l.8351-1.5262C15.481 2.395 13.8034 1.927 12.018 1.927 6.4746 1.9246 1.963 6.4362 1.963 11.982zm18.3702 0c0 4.586-3.7293 8.3152-8.3152 8.3152-1.4327 0-2.7837-.3648-3.962-1.0055l-.852 1.5166c1.4303.7823 3.0718 1.2287 4.814 1.2287 5.5434 0 10.055-4.5116 10.055-10.055 0-2.8077-1.1567-5.3467-3.0165-7.1729l-1.183 1.2743c1.519 1.507 2.4597 3.5924 2.4597 5.8986zm-1.9486 0c0 3.5109-2.8558 6.3642-6.3642 6.3642a6.3286 6.3286 0 01-3.0069-.756l-.8471 1.507c1.147.624 2.4597.9768 3.854.9768 4.4634 0 8.0944-3.6308 8.0944-8.0944 0-2.239-.9143-4.2692-2.3902-5.7378l-1.1783 1.267c1.1351 1.152 1.8383 2.731 1.8383 4.4732zm-14.4586 0c0 2.3014.9671 4.382 2.515 5.8578l1.1734-1.2695c-1.207-1.159-1.9606-2.786-1.9606-4.5883 0-3.5108 2.8557-6.3642 6.3642-6.3642 1.1423 0 2.215.3048 3.1437.8352l.8303-1.5167c-1.1759-.6647-2.5317-1.0487-3.974-1.0487-4.4612 0-8.092 3.6308-8.092 8.0944zm12.5292 0c0 2.4502-1.987 4.4372-4.4372 4.4372a4.4192 4.4192 0 01-2.0614-.5088l-.8351 1.4879a6.1135 6.1135 0 002.8965.727c3.3885 0 6.1434-2.7548 6.1434-6.1433 0-1.6774-.6767-3.1989-1.7686-4.3076l-1.1615 1.2503c.7559.7967 1.2239 1.8718 1.2239 3.0573zm-10.5806 0c0 1.7374.7247 3.3069 1.8886 4.4252L8.92 15.1569l.0144.0144c-.8351-.8063-1.3559-1.9366-1.3559-3.1869 0-2.4502 1.9846-4.4372 4.4372-4.4372.8087 0 1.5646.2184 2.2174.5976l.8207-1.4975a6.097 6.097 0 00-3.0381-.8063c-3.3837-.0048-6.141 2.7525-6.141 6.141zm6.681 0c0 .2952-.2424.5351-.5376.5351-.2952 0-.5375-.24-.5375-.5351 0-.2976.24-.5375.5375-.5375.2952 0 .5375.24.5375.5375zm-3.9405 0c0-1.879 1.5239-3.4029 3.4005-3.4029 1.879 0 3.4005 1.5215 3.4005 3.4029 0 1.879-1.5239 3.4005-3.4005 3.4005S8.6151 13.861 8.6151 11.982zm.1488 0c.0048 1.7974 1.4567 3.2493 3.2517 3.2517 1.795 0 3.254-1.4567 3.254-3.2517-.0023-1.7974-1.4566-3.2517-3.254-3.254-1.795 0-3.2517 1.4566-3.2517 3.254Z"/>
                                    </svg>
                                </button>
                                <button class="track-youtube-btn" data-artist="${escapeHtml(track?.artist || '')}" data-title="${escapeHtml(track?.name || '')}">
                                    <span class="loading-spinner"></span>
                                    <svg class="button-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <path d="M22.54 6.42a2.78 2.78 0 0 0-1.94-2C18.88 4 12 4 12 4s-6.88 0-8.6.46a2.78 2.78 0 0 0-1.94 2A29 29 0 0 0 1 11.75a29 29 0 0 0 .46 5.33A2.78 2.78 0 0 0 3.4 19c1.72.46 8.6.46 8.6.46s6.88 0 8.6-.46a2.78 2.78 0 0 0 1.94-2 29 29 0 0 0 .46-5.25 29 29 0 0 0-.46-5.33z"></path>
                                        <polygon points="9.75 15.02 15.5 11.75 9.75 8.48 9.75 15.02" fill="currentColor"></polygon>
                                    </svg>
                                </button>
                                <span class="track-artist">${escapeHtml(track?.artist || '')}</span>
                                <span class="track-title">${escapeHtml(track?.name || '')}</span>
                                ${track?.timestamp ? `<span class="track-timestamp" title="Starts at">${escapeHtml(track.timestamp)}</span>` : ''}
                                <div class="download-progress">
                                    <div class="progress-bar"></div>
                                    <div class="progress-info"></div>
                                    <button class="cancel-download">Cancel Download</button>
                                </div>
                            </li>
                        `).join('')}
                    </ul>
                </div>` : ''}
        </div>`;

    return item;
}

function bindEpisodesListContent(episodesList) {
    if (!episodesList) return;
    if (typeof window.bindEpisodeEventHandlers === 'function') {
        window.bindEpisodeEventHandlers(episodesList);
    }
    if (typeof window.checkLikedStatus === 'function') {
        window.checkLikedStatus(episodesList);
    }
}

function appendEpisodesToList(episodesList, episodes) {
    if (!episodesList || !Array.isArray(episodes) || !episodes.length) return 0;

    const fragment = document.createDocumentFragment();
    let appendedCount = 0;
    episodes.forEach((episode) => {
        const existingItem = findEpisodeItemByUrl(episodesList, episode?.url);
        if (existingItem) {
            if (existingItem.dataset.episodeTemporary === 'true') {
                existingItem.remove();
            } else {
                return;
            }
        }
        fragment.appendChild(renderEpisodeItem(episode));
        appendedCount += 1;
    });

    if (appendedCount) {
        episodesList.appendChild(fragment);
        bindEpisodesListContent(episodesList);
    }

    return appendedCount;
}

function insertResolvedEpisode(episodesList, episode) {
    if (!episodesList || !episode?.url) return null;

    const existingItem = findEpisodeItemByUrl(episodesList, episode.url);
    if (existingItem) return existingItem;

    const item = renderEpisodeItem(episode, { temporary: true });
    episodesList.insertBefore(item, episodesList.firstChild);
    bindEpisodesListContent(episodesList);
    return item;
}

function deferFrame(callback) {
    if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(callback);
        return;
    }
    setTimeout(callback, 0);
}

function smartScrollIntoView(element, options = {}) {
    if (!element?.scrollIntoView || !element?.getBoundingClientRect) return;

    const { block = 'start' } = options;
    const rect = element.getBoundingClientRect();
    const viewportHeight = window.innerHeight || document.documentElement?.clientHeight || 0;
    const currentY = window.scrollY || window.pageYOffset || 0;
    const blockOffset = block === 'center'
        ? Math.max(0, (viewportHeight - rect.height) / 2)
        : 96;
    const targetY = Math.max(0, currentY + rect.top - blockOffset);

    if (Math.abs(targetY - currentY) > LARGE_SCROLL_DISTANCE_PX && typeof window.scrollTo === 'function') {
        window.scrollTo({ top: targetY, behavior: 'auto' });
        deferFrame(() => element.scrollIntoView({ behavior: 'smooth', block }));
        return;
    }

    element.scrollIntoView({ behavior: 'smooth', block });
}

function getEpisodeShowContext(episodeItem) {
    const episodesList = episodeItem?.closest('.episodes-list');
    const showLink = episodeItem?.querySelector('.feed-show-link');
    const showTitleEl = document.querySelector('.page-title');
    return {
        show_url: episodesList?.dataset.showUrl || showLink?.href || '',
        show_title: showLink?.textContent || showTitleEl?.textContent || '',
    };
}

function buildEpisodePlaybackContext(episodeItem, episodeData = {}) {
    const showContext = getEpisodeShowContext(episodeItem);
    return {
        kind: 'episode',
        player: 'nts_audio',
        source_page: window.location.pathname || '',
        source_url: window.location.href || '',
        episode_url: episodeData.url || episodeItem?.querySelector('.episode-title')?.href || '',
        episode_title: episodeData.title || episodeItem?.querySelector('.episode-title')?.textContent || '',
        episode_date: episodeData.date || episodeItem?.querySelector('.episode-date')?.textContent || '',
        episode_image: episodeData.image || episodeItem?.querySelector('.episode-thumbnail img')?.src || '',
        ...showContext,
    };
}

function buildTrackPlaybackContext(button) {
    const episodeItem = button?.closest('.episode-item');
    const showContext = getEpisodeShowContext(episodeItem);
    const episodeTitleEl = episodeItem?.querySelector('.episode-title');
    const episodeDateEl = episodeItem?.querySelector('.episode-date');
    return {
        kind: 'track',
        player: 'youtube',
        source_page: window.location.pathname || '',
        source_url: window.location.href || '',
        episode_url: episodeTitleEl?.href || '',
        episode_title: episodeTitleEl?.textContent || '',
        episode_date: episodeDateEl?.textContent || '',
        track_artist: button?.dataset.artist || '',
        track_title: button?.dataset.title || '',
        artist: button?.dataset.artist || '',
        title: button?.dataset.title || '',
        ...showContext,
    };
}

async function dedupedFetch(url, options = {}) {
    const method = options.method || 'GET';
    const key = `${method}:${url}`;
    
    // For GET requests, return existing promise if request is in-flight
    if (method === 'GET' && pendingRequests.has(key)) {
        return pendingRequests.get(key);
    }
    
    // Cache parsed JSON data instead of Response to allow multiple consumers
    const promise = (async () => {
        const response = await fetch(url, options);
        const data = await response.json();
        // Return a Response-like object with reusable json()
        return {
            ok: response.ok,
            status: response.status,
            json: () => Promise.resolve(data)
        };
    })();
    
    if (method === 'GET') {
        pendingRequests.set(key, promise);
    }
    
    promise.finally(() => pendingRequests.delete(key));
    return promise;
}

/**
 * Initialize show page handlers
 * Called by the router when a show page becomes active
 */
function initShowPageHandlers() {
    // Only run if we're on a show page
    if (!window.location.pathname.startsWith('/show/')) return;
    
    // Clean up previous initialization if needed
    if (window._showPageCleanup) {
        try {
            window._showPageCleanup();
        } catch(_) {}
    }

    initShowPageCore();

    if (typeof window.initGenreSearchHandlers === 'function') {
        window.initGenreSearchHandlers();
    }
}

/**
 * Initialize track/episode handlers only (for feed page, search results, etc.)
 * This is a lighter version that just binds track button handlers without show-specific logic.
 */
function initTrackHandlers() {
    console.log('[ShowPage] Initializing track handlers for non-show page');
    initShowPageCore({ trackHandlersOnly: true });
}

// Expose for SPA router
window.initShowPageHandlers = initShowPageHandlers;
window.initTrackHandlers = initTrackHandlers;

if (window.NTSPageModules && typeof window.NTSPageModules.register === 'function') {
    window.NTSPageModules.register('show', {
        init: initShowPageHandlers,
        cleanup() {
            if (typeof window._showPageCleanup === 'function') {
                window._showPageCleanup();
            }
        },
    });
}

async function initShowPageCore(options = {}) {
    const { trackHandlersOnly = false } = options;
    
    // Skip show-specific initialization if only track handlers are needed
    if (!trackHandlersOnly) {
        // Floating back link on scroll
        try {
            const backLink = document.querySelector('.back-link');
            const topControls = document.querySelector('.top-controls');
            if (backLink) {
                const getThreshold = () => {
                    try {
                        return Math.max(0, (topControls?.offsetHeight || 120));
                    } catch (_) {
                        return 120;
                    }
                };
                let threshold = getThreshold();

                const onScroll = () => {
                    if (window.scrollY > threshold) backLink.classList.add('floating');
                    else backLink.classList.remove('floating');
                };
                const onResize = () => { threshold = getThreshold(); onScroll(); };
                
                // Store references for cleanup
                showPageCleanupResources.scrollHandler = onScroll;
                showPageCleanupResources.resizeHandler = onResize;
                
                window.addEventListener('scroll', onScroll, { passive: true });
                window.addEventListener('resize', onResize);
                onScroll();
            }
        } catch (_) { /* noop */ }
    }
    
    const stopAllButton = document.getElementById('stopAllButton');
    const downloadButtons = document.querySelectorAll('.download-button');
    const cancelButtons = document.querySelectorAll('.cancel-download');
    const episodeThumbnails = document.querySelectorAll('.episode-thumbnail');

    // Get the current show URL (may be undefined on non-show pages)
    const showPathPart = window.location.pathname.split('/show/')[1];
    const showUrlEncoded = showPathPart ? encodeURIComponent(showPathPart) : '';

    // Show-specific initialization (download state, batch downloads)
    let downloadState = {};
    let isDownloadingAll = false;
    let currentBatchId = null;
    
    if (!trackHandlersOnly && showUrlEncoded) {
        // Load saved download state
        downloadState = JSON.parse(localStorage.getItem(DOWNLOAD_STATE_KEY) || '{}');
        isDownloadingAll = downloadState.isDownloadingAll || false;
        currentBatchId = downloadState.currentBatchId || null;

        // Immediately update UI if we have a saved download state
        if (isDownloadingAll && currentBatchId) {
            updateDownloadUI(true);
        }
    }

    // Initialize NTS Audio Player (needed for track playback on any page)
    if (!window.ntsAudioPlayer) {
        const ntsAudioPlayer = new NTSAudioPlayer();
        // Make it globally accessible for error fallback buttons
        window.ntsAudioPlayer = ntsAudioPlayer;
    }

    /**
     * Lazy-load tracklist timestamps using IntersectionObserver.
     * Only fetches API data when an episode scrolls into view.
     */
    const tracklistObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const episodeItem = entry.target;
                tracklistObserver.unobserve(episodeItem); // Only fetch once
                enrichEpisodeTimestamps(episodeItem);
            }
        });
    }, { rootMargin: '100px 0px' }); // Prefetch 100px before visible
    
    // Store reference for cleanup
    showPageCleanupResources.tracklistObserver = tracklistObserver;

    async function enrichEpisodeTimestamps(episodeItem) {
        try {
            // Skip if already enriched
            if (episodeItem.dataset.timestampsLoaded) return;
            episodeItem.dataset.timestampsLoaded = 'true';
            
            const episodeTitle = episodeItem.querySelector('.episode-title');
            const episodeUrl = episodeTitle ? episodeTitle.href : '';
            if (!episodeUrl) return;
            const res = await dedupedFetch(`/api/episode_tracklist?episode_url=${encodeURIComponent(episodeUrl)}`);
            const data = await res.json();
            if (!data.success || !Array.isArray(data.tracklist)) return;

            // Build a lookup by normalized artist+title to be robust to commas/spacing and Unicode variants
            const normalize = (s) => (String(s || '')
                .toLowerCase()
                .replace(/[\u00A0\s]+/g, ' ')
                .replace(/[，、]/g, ',')
                .replace(/[–—]/g, '-')
                .replace(/[＋]/g, '+')
                .replace(/[＆]/g, '&')
                .replace(/[\s]*[,;:.]+$/g, '')
                .trim());
            const artistKey = (artist) => {
                const base = normalize(artist);
                const parts = base.split(/\s*(?:,|&|and)\s*/).filter(Boolean).sort();
                return parts.join(',');
            };
            const keyFor = (artist, title) => `${artistKey(artist)}|||${normalize(title)}`;
            const tsByKey = new Map();
            data.tracklist.forEach(t => {
                if (!t) return;
                const k = keyFor(t.artist, t.name || t.title);
                if (t.timestamp && !tsByKey.has(k)) tsByKey.set(k, t.timestamp);
                // Also store timestamp for title-only to help with multi-artist formatting differences
                const kTitleOnly = `*|||${normalize(t.name || t.title)}`;
                if (t.timestamp && !tsByKey.has(kTitleOnly)) tsByKey.set(kTitleOnly, t.timestamp);
            });

            const items = episodeItem.querySelectorAll('.tracks-list .track-item');
            items.forEach((li, idx) => {
                try {
                    if (li.querySelector('.track-timestamp')) return; // already set
                    const artistEl = li.querySelector('.track-artist');
                    const titleEl = li.querySelector('.track-title');
                    const key = keyFor(artistEl?.textContent, titleEl?.textContent);
                    const fallback = (data.tracklist[idx] && data.tracklist[idx].timestamp) || null;
                    const ts = tsByKey.get(key) || fallback;
                    if (!ts) return;
                    const span = document.createElement('span');
                    span.className = 'track-timestamp';
                    span.title = 'Starts at';
                    span.textContent = ts;
                    if (titleEl && titleEl.nextSibling) {
                        titleEl.parentNode.insertBefore(span, titleEl.nextSibling.nextSibling || null);
                    } else if (titleEl) {
                        titleEl.after(span);
                    } else {
                        li.appendChild(span);
                    }
                } catch (_) { /* ignore per-item */ }
            });
        } catch (_) {
            // ignore
        }
    }

    function bindEpisodeEventHandlers(root = document) {
        // Use IntersectionObserver for lazy-loading tracklist timestamps
        root.querySelectorAll('.episode-item').forEach(episodeItem => {
            if (!episodeItem.dataset.timestampsLoaded) {
                tracklistObserver.observe(episodeItem);
            }
        });
        // Click event on episode thumbnails to open audio player
        root.querySelectorAll('.episode-thumbnail').forEach(thumbnail => {
            if (thumbnail.__bound) return; // avoid rebinding
            thumbnail.__bound = true;
            thumbnail.addEventListener('click', function () {
                const episodeItem = thumbnail.closest('.episode-item');
                const episodeTitle = episodeItem.querySelector('.episode-title');
                const episodeDate = episodeItem.querySelector('.episode-date');
                const episodeImage = thumbnail.querySelector('img');
                const playbackContext = buildEpisodePlaybackContext(episodeItem, {
                    url: episodeTitle ? episodeTitle.href : '',
                    title: episodeTitle ? episodeTitle.textContent : '',
                    date: episodeDate ? episodeDate.textContent : '',
                    image: episodeImage ? episodeImage.src : ''
                });
                const episodeData = {
                    url: playbackContext.episode_url,
                    title: playbackContext.episode_title,
                    date: playbackContext.episode_date,
                    image: playbackContext.episode_image,
                    show_url: playbackContext.show_url,
                    show_title: playbackContext.show_title,
                    playback_context: playbackContext,
                };
                if (episodeData.url) ntsAudioPlayer.showPlayer(episodeData);
            });
        });

        // Click on track timestamp to seek playback
        root.querySelectorAll('.tracks-list .track-item .track-timestamp').forEach(ts => {
            if (ts.__bound) return;
            ts.__bound = true;
            ts.style.cursor = 'pointer';
            ts.title = ts.title || 'Starts at';
            ts.addEventListener('click', function (e) {
                e.stopPropagation();
                try {
                    const li = ts.closest('.track-item');
                    const episodeItem = ts.closest('.episode-item');
                    const episodeTitle = episodeItem ? episodeItem.querySelector('.episode-title') : null;
                    const episodeDate = episodeItem ? episodeItem.querySelector('.episode-date') : null;
                    const episodeImage = episodeItem ? episodeItem.querySelector('.episode-thumbnail img') : null;
                    const episodeUrl = episodeTitle ? episodeTitle.href : '';
                    const playbackContext = buildEpisodePlaybackContext(episodeItem, {
                        url: episodeUrl,
                        title: episodeTitle ? episodeTitle.textContent : '',
                        date: episodeDate ? episodeDate.textContent : '',
                        image: episodeImage ? episodeImage.src : ''
                    });
                    const tsText = (ts.textContent || '').trim();

                    if (!window.ntsAudioPlayer) return;

                    // If player isn't showing this episode yet, show it first
                    const episodeData = {
                        url: playbackContext.episode_url,
                        title: playbackContext.episode_title,
                        date: playbackContext.episode_date,
                        image: playbackContext.episode_image,
                        show_url: playbackContext.show_url,
                        show_title: playbackContext.show_title,
                        playback_context: playbackContext,
                    };

                    const seconds = (function parseTS(t) {
                        const parts = t.split(':').map(p => p.trim());
                        if (parts.length < 2) return 0;
                        const nums = parts.map(p => parseInt(p, 10));
                        if (nums.some(n => isNaN(n))) return 0;
                        if (nums.length === 3) return nums[0] * 3600 + nums[1] * 60 + nums[2];
                        return nums[0] * 60 + nums[1];
                    })(tsText);

                    window.ntsAudioPlayer.seekTo(seconds, episodeUrl, episodeData);
                } catch (_) { /* ignore */ }
            });
        });

        // Default click on track item -> seek to its timestamp (resolve on-demand if missing)
        root.querySelectorAll('.tracks-list .track-item').forEach(item => {
            if (item.__seekBound) return;
            item.__seekBound = true;
            item.addEventListener('click', function (e) {
                // Ignore if clicking control buttons
                if (e.target.closest('.track-download-btn') || e.target.closest('.track-youtube-btn') || e.target.closest('.track-info-btn') || e.target.closest('.track-like-btn')) return;
                let ts = item.querySelector('.track-timestamp');
                e.preventDefault();
                try {
                    const episodeItem = item.closest('.episode-item');
                    const episodeTitle = episodeItem ? episodeItem.querySelector('.episode-title') : null;
                    const episodeDate = episodeItem ? episodeItem.querySelector('.episode-date') : null;
                    const episodeImage = episodeItem ? episodeItem.querySelector('.episode-thumbnail img') : null;
                    const episodeUrl = episodeTitle ? episodeTitle.href : '';
                    const playbackContext = buildEpisodePlaybackContext(episodeItem, {
                        url: episodeUrl,
                        title: episodeTitle ? episodeTitle.textContent : '',
                        date: episodeDate ? episodeDate.textContent : '',
                        image: episodeImage ? episodeImage.src : ''
                    });
                    const seekWithSeconds = async (seconds) => {
                        if (!window.ntsAudioPlayer) return;
                        const episodeData = {
                            url: playbackContext.episode_url,
                            title: playbackContext.episode_title,
                            date: playbackContext.episode_date,
                            image: playbackContext.episode_image,
                            show_url: playbackContext.show_url,
                            show_title: playbackContext.show_title,
                            playback_context: playbackContext,
                        };
                        window.ntsAudioPlayer.seekTo(seconds, episodeUrl, episodeData);
                    };
                    const parseTS = (t) => {
                        const parts = String(t || '').split(':').map(p => p.trim());
                        if (parts.length < 2) return 0;
                        const nums = parts.map(p => parseInt(p, 10));
                        if (nums.some(n => isNaN(n))) return 0;
                        if (nums.length === 3) return nums[0] * 3600 + nums[1] * 60 + nums[2];
                        return nums[0] * 60 + nums[1];
                    };

                    if (ts) {
                        const seconds = parseTS((ts.textContent || '').trim());
                        seekWithSeconds(seconds);
                        return;
                    }

                    (async () => {
                        // On-demand resolution
                        const res = await dedupedFetch(`/api/episode_tracklist?episode_url=${encodeURIComponent(episodeUrl)}`);
                        const data = await res.json();
                        const tracklist = Array.isArray(data.tracklist) ? data.tracklist : [];
                        const normalize = (s) => (String(s || '')
                            .toLowerCase()
                            .replace(/[\u00A0\s]+/g, ' ')
                            .replace(/[，、]/g, ',')
                            .replace(/[–—]/g, '-')
                            .replace(/[＋]/g, '+')
                            .replace(/[＆]/g, '&')
                            .replace(/[\s]*[,;:.]+$/g, '')
                            .trim());
                        const artistKey = (a) => {
                            const base = normalize(a);
                            const parts = base.split(/\s*(?:,|&|and)\s*/).filter(Boolean).sort();
                            return parts.join(',');
                        };
                        const keyFor = (a, t) => `${artistKey(a)}|||${normalize(t)}`;
                        const tsByKey = new Map();
                        tracklist.forEach(t => {
                            const k = keyFor(t?.artist, t?.name || t?.title);
                            if (t?.timestamp && !tsByKey.has(k)) tsByKey.set(k, t.timestamp);
                        });
                        const artist = item.querySelector('.track-artist')?.textContent || '';
                        const title = item.querySelector('.track-title')?.textContent || '';
                        const key = keyFor(artist, title);
                        let resolved = tsByKey.get(key) || null;
                        if (!resolved) {
                            // Title-only fallback; prefer closest index
                            const idx = Array.from(item.parentNode?.children || []).indexOf(item);
                            let closest = null;
                            tracklist.forEach((t, i) => {
                                if (normalize(t?.name || t?.title) === normalize(title) && t?.timestamp) {
                                    const dist = Math.abs((idx ?? i) - i);
                                    if (!closest || dist < closest.dist) closest = { ts: t.timestamp, dist };
                                }
                            });
                            resolved = tsByKey.get(`*|||${normalize(title)}`) || closest?.ts || (tracklist[idx]?.timestamp || null);
                        }
                        if (resolved) {
                            // Insert timestamp into DOM for future interactions
                            const titleEl = item.querySelector('.track-title');
                            const span = document.createElement('span');
                            span.className = 'track-timestamp';
                            span.title = 'Starts at';
                            span.textContent = resolved;
                            if (titleEl && titleEl.nextSibling) {
                                titleEl.parentNode.insertBefore(span, titleEl.nextSibling.nextSibling || null);
                            } else if (titleEl) {
                                titleEl.after(span);
                            } else {
                                item.appendChild(span);
                            }
                            const seconds = parseTS(resolved);
                            seekWithSeconds(seconds);
                            // Rebuild highlighting boundaries now that timestamp exists
                            try { window.ntsAudioPlayer?.buildCurrentEpisodeTrackMap?.(); window.ntsAudioPlayer?.updateActiveTrackHighlight?.(); } catch (_) { }
                        }
                    })();
                } catch (_) { /* ignore */ }
            });
        });

        // Info button click handled in track-info.js via delegation on the li

        // Individual download buttons
        root.querySelectorAll('.download-button').forEach(button => {
            if (button.classList.contains('downloaded') || button.__bound) return;
            button.__bound = true;
            button.addEventListener('click', async function (e) {
                e.preventDefault();
                await DownloadManager.handleSingleDownload(button, button.href);
            });
        });

        // Cancel buttons
        root.querySelectorAll('.cancel-download').forEach(button => {
            if (button.__bound) return;
            button.__bound = true;
            button.addEventListener('click', async function () {
                const episodeItem = button.closest('.episode-item');
                const downloadButton = episodeItem.querySelector('.download-button');
                if (downloadButton.classList.contains('loading')) {
                    try {
                        const progressDiv = episodeItem.querySelector('.download-progress');
                        const eventSourceUrl = progressDiv.dataset.eventSource;
                        const downloadId = eventSourceUrl.split('/').pop();
                        const response = await fetch(`/cancel_download/${downloadId}`, { method: 'POST' });
                        if (!response.ok) throw new Error('Failed to cancel download');
                        downloadButton.classList.remove('loading');
                        downloadButton.querySelector('.button-text').textContent = 'Download';
                        progressDiv.style.display = 'none';
                    } catch (error) {
                        console.error('Error canceling download:', error);
                    }
                }
            });
        });

        // Track download buttons (Discogs)
        root.querySelectorAll('.track-download-btn').forEach(button => {
            if (button.__bound) return;
            button.__bound = true;
            button.addEventListener('click', async function () {
                if (button.classList.contains('searching')) return;
                const artist = button.dataset.artist;
                const title = button.dataset.title;
                try {
                    button.classList.add('searching');
                    const response = await fetch('/download_track', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ artist, title })
                    });
                    if (response.ok) {
                        const isJson = (response.headers.get('content-type') || '').includes('application/json');
                        const data = isJson ? await response.json() : {};
                        if (data.success && data.url) {
                            window.open(data.url, '_blank');
                        } else {
                            // Fallback to direct Discogs search
                            window.open(`https://www.discogs.com/search/?q=${encodeURIComponent(artist + ' ' + title)}&type=release`, '_blank');
                        }
                    } else {
                        // Fallback to direct Discogs search on 404/500
                        window.open(`https://www.discogs.com/search/?q=${encodeURIComponent(artist + ' ' + title)}&type=release`, '_blank');
                    }
                    button.classList.remove('searching');
                } catch (error) {
                    console.error('Discogs search error:', error);
                    button.classList.remove('searching');
                    showNotification(`Failed to search on Discogs: ${error.message}`, 'error');
                }
            });
        });

        // YouTube buttons
        root.querySelectorAll('.track-youtube-btn').forEach(button => {
            if (button.__bound) return;
            button.__bound = true;
            button.addEventListener('click', async function () {
                if (button.classList.contains('searching')) return;
                const artist = button.dataset.artist;
                const title = button.dataset.title;
                try {
                    const response = await fetch('/search_youtube', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ artist, title })
                    });
                    if (response.ok) {
                        const isJson = (response.headers.get('content-type') || '').includes('application/json');
                        const data = isJson ? await response.json() : {};
                        if (data.success) {
                            if (data.search_only) {
                                showNotification(`Searching YouTube for "${artist} - ${title}"`, 'info');
                                window.open(data.video_url, '_blank');
                            } else {
                                showYouTubePlayer(data, artist, title, null, null, window.location.pathname, null, buildTrackPlaybackContext(button));
                            }
                        } else if (data.quota_exceeded) {
                            showNotification('YouTube API daily quota exceeded. Please try again tomorrow.', 'error', 10000);
                            disableYouTubeButtons();
                        } else {
                            // Fallback to YouTube search
                            window.open(`https://www.youtube.com/results?search_query=${encodeURIComponent(artist + ' ' + title)}`, '_blank');
                        }
                    } else {
                        // Fallback to YouTube search on 404/500
                        window.open(`https://www.youtube.com/results?search_query=${encodeURIComponent(artist + ' ' + title)}`, '_blank');
                    }
                } catch (error) {
                    console.error('YouTube search error:', error);
                    showNotification(`Failed to search on YouTube: ${error.message}`, 'error');
                }
            });
        });

        // Track like buttons
        root.querySelectorAll('.track-like-btn').forEach(button => {
            if (button.__bound) return;
            button.__bound = true;
            button.addEventListener('click', async function (e) {
                e.stopPropagation();
                if (button.classList.contains('processing')) return;
                
                const artist = button.dataset.artist;
                const title = button.dataset.title;
                const isLiked = button.classList.contains('liked');
                const likeId = button.dataset.likeId;

                button.classList.add('processing');

                try {
                    if (isLiked && likeId) {
                        // Unlike
                        const res = await fetch(`/api/likes/${likeId}`, { method: 'DELETE' });
                        const data = await res.json();
                        if (data.success) {
                            button.classList.remove('liked');
                            button.dataset.likeId = '';
                            button.querySelector('i').className = 'far fa-heart';
                            button.title = 'Like track';
                            showNotification('Removed from likes', 'info');
                        }
                    } else {
                        // Like
                        const episodeItem = button.closest('.episode-item');
                        const episodeTitle = episodeItem?.querySelector('.episode-title');
                        const episodeUrl = episodeTitle?.href || '';
                        const episodeTitleText = episodeTitle?.textContent || '';
                        const showTitle = document.querySelector('.header-container h1')?.textContent || '';

                        const res = await fetch('/api/likes', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                artist,
                                title,
                                episode_url: episodeUrl,
                                episode_title: episodeTitleText,
                                show_title: showTitle
                            })
                        });
                        const data = await res.json();
                        if (data.success) {
                            button.classList.add('liked');
                            button.dataset.likeId = data.id;
                            button.querySelector('i').className = 'fas fa-heart';
                            button.title = 'Unlike track';
                            if (!data.already_liked) {
                                showNotification('Added to likes', 'success');
                            }
                        }
                    }
                } catch (err) {
                    console.error('Like error:', err);
                    showNotification('Failed to update like', 'error');
                } finally {
                    button.classList.remove('processing');
                }
            });
        });
    }

    // Bind initial set
    bindEpisodeEventHandlers(document);
    // Expose binder for dynamic content
    window.bindEpisodeEventHandlers = bindEpisodeEventHandlers;

    // Initialize show menu dropdown
    function initializeShowMenu() {
        const menuContainer = document.querySelector('.show-menu-container');
        if (!menuContainer) return;
        
        const trigger = menuContainer.querySelector('.show-menu-trigger');
        const dropdown = menuContainer.querySelector('.show-menu-dropdown');
        if (!trigger || !dropdown) return;
        
        const menuItems = dropdown.querySelectorAll('.show-menu-item');
        
        // Toggle menu on click
        trigger.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            menuContainer.classList.toggle('active');
        });
        
        // Close menu when clicking outside
        document.addEventListener('click', function(e) {
            if (!e.target.closest('.show-menu-container')) {
                menuContainer.classList.remove('active');
            }
        });
        
        // Close menu on escape key
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                menuContainer.classList.remove('active');
            }
        });
        
        // Handle menu item clicks
        menuItems.forEach(menuItem => {
            menuItem.addEventListener('click', async function(e) {
                e.preventDefault();
                e.stopPropagation();
                
                const action = menuItem.dataset.action;
                
                if (menuItem.classList.contains('loading')) return;
                
                menuContainer.classList.remove('active');
                
                if (action === 'update-show') {
                    await handleUpdateShow(menuItem);
                } else if (action === 'download-all') {
                    await handleDownloadAll(menuItem);
                } else if (action === 'toggle-auto-download') {
                    await handleToggleAutoDownload(menuItem);
                } else if (action === 'delete-show') {
                    await handleDeleteShow(menuItem);
                }
            });
        });
    }
    
    async function handleDeleteShow(menuItem) {
        const showUrl = menuItem.dataset.showUrl;
        if (!showUrl) return;

        const title = document.querySelector('.page-title')?.textContent || 'this show';
        if (!confirm(`Delete "${title}"? This will remove the show and all its episodes.`)) return;

        try {
            const response = await fetch(`/delete/${encodeURIComponent(showUrl)}`, { method: 'POST', redirect: 'follow' });
            if (response.ok || response.redirected) {
                window.location.href = '/';
            } else {
                showNotification('Failed to delete show', 'error');
            }
        } catch (e) {
            showNotification('Failed to delete show', 'error');
        }
    }

    async function handleUpdateShow(menuItem) {
        try {
            menuItem.classList.add('loading');
            
            const showUrl = window.location.pathname.split('/show/')[1];
            const response = await fetch(`/update_show/${encodeURIComponent(showUrl)}`, {
                method: 'POST'
            });
            
            const data = await response.json();
            
            if (data.success) {
                const message = `Update complete: ${data.new_episodes} new episode${data.new_episodes !== 1 ? 's' : ''} found`;
                showNotification(message, 'success');
                
                // Reload the page to show new episodes
                if (data.new_episodes > 0) {
                    setTimeout(() => window.location.reload(), 1500);
                }
            } else {
                throw new Error(data.message || 'Update failed');
            }
        } catch (error) {
            console.error('Error updating show:', error);
            showNotification(`Error: ${error.message}`, 'error');
        } finally {
            setTimeout(() => {
                menuItem.classList.remove('loading');
            }, 500);
        }
    }
    
    async function handleDownloadAll(menuItem) {
        try {
            menuItem.classList.add('loading');
            
            isDownloadingAll = true;
            updateDownloadUI(true);
            
            const response = await fetch(`/download_all/${encodeURIComponent(window.location.pathname.split('/show/')[1])}`);
            if (!response.ok) throw new Error('Failed to start batch download');
            
            const data = await response.json();
            currentBatchId = data.batch_id;
            saveDownloadState();
            
            setupProgressTracking(data.batch_id);
        } catch (error) {
            console.error('Batch download error:', error);
            clearDownloadState();
            updateDownloadUI(false);
            showNotification(`Download error: ${error.message}`, 'error');
        } finally {
            menuItem.classList.remove('loading');
        }
    }
    
    async function handleToggleAutoDownload(menuItem) {
        try {
            menuItem.classList.add('loading');
            
            const response = await fetch(`/toggle_auto_download/${encodeURIComponent(window.location.pathname.split('/show/')[1])}`, {
                method: 'POST'
            });
            
            if (!response.ok) throw new Error('Failed to toggle auto-download');
            
            const data = await response.json();
            
            if (data.success) {
                // Update the menu item
                menuItem.dataset.enabled = data.auto_download ? 'true' : 'false';
                const statusSpan = menuItem.querySelector('.auto-status');
                if (statusSpan) {
                    statusSpan.textContent = data.auto_download ? 'On' : 'Off';
                }
                
                // Update the trigger button
                const trigger = menuItem.closest('.show-menu-container').querySelector('.show-menu-trigger');
                if (trigger) {
                    trigger.dataset.autoDownload = data.auto_download ? 'true' : 'false';
                }
                
                showNotification(data.auto_download ? 'Auto-download enabled' : 'Auto-download disabled', 
                    data.auto_download ? 'success' : 'info');
            } else {
                throw new Error(data.message || 'Failed to toggle auto-download');
            }
        } catch (error) {
            console.error('Error toggling auto-download:', error);
            showNotification(`Error: ${error.message}`, 'error');
        } finally {
            menuItem.classList.remove('loading');
        }
    }
    
    initializeShowMenu();

    function saveDownloadState() {
        localStorage.setItem(DOWNLOAD_STATE_KEY, JSON.stringify({
            isDownloadingAll,
            currentBatchId,
            showUrl: window.location.pathname
        }));
    }

    function clearDownloadState() {
        localStorage.removeItem(DOWNLOAD_STATE_KEY);
        isDownloadingAll = false;
        currentBatchId = null;
    }

    // Check for active downloads on page load
    async function checkActiveDownloads() {
        try {
            const response = await dedupedFetch(`/check_active_downloads?show_url=${showUrlEncoded}`);
            const data = await response.json();

            if (Object.keys(data.active_downloads).length > 0) {
                const batchId = Object.keys(data.active_downloads)[0];
                currentBatchId = batchId;
                isDownloadingAll = true;
                saveDownloadState();

                // Update UI before setting up progress tracking
                updateDownloadUI(true);
                setupProgressTracking(batchId);
            } else if (isDownloadingAll) {
                // No active downloads but state indicates we were downloading
                clearDownloadState();
                updateDownloadUI(false);
            }
        } catch (error) {
            console.error('Error checking active downloads:', error);
            // On error, trust the local state
            if (isDownloadingAll && currentBatchId) {
                updateDownloadUI(true);
            }
        }
    }

    function updateDownloadUI(isDownloading) {
        if (!stopAllButton) return;

        // Update stop button visibility
        stopAllButton.style.display = isDownloading ? 'block' : 'none';
        
        // Update download all menu item if it exists
        const downloadAllMenuItem = document.querySelector('.show-menu-item[data-action="download-all"]');
        if (downloadAllMenuItem) {
            const itemText = downloadAllMenuItem.querySelector('span:not(.auto-status)');
            if (itemText) {
                itemText.textContent = isDownloading ? 'Downloading episodes...' : 'Download All Episodes';
            }
            if (isDownloading) {
                downloadAllMenuItem.classList.add('loading');
            } else {
                downloadAllMenuItem.classList.remove('loading');
            }
        }

        // Also update any in-progress episode buttons
        downloadButtons.forEach(button => {
            const episodeItem = button.closest('.episode-item');
            const progressDiv = episodeItem.querySelector('.download-progress');
            if (isDownloading && !button.classList.contains('downloaded')) {
                button.classList.add('loading');
                button.querySelector('.button-text').textContent = 'Downloading...';
                if (progressDiv) progressDiv.style.display = 'block';
            }
        });
    }

    function setupProgressTracking(batchId) {
        const eventSource = new EventSource(`/progress/${batchId}`);

        eventSource.onmessage = function (event) {
            const progress = JSON.parse(event.data);

            if (progress === null) {
                eventSource.close();
                clearDownloadState();
                updateDownloadUI(false);
                return;
            }

            handleBatchProgress(progress, downloadButtons);
        };

        eventSource.onerror = function () {
            eventSource.close();
            checkActiveDownloads(); // Recheck state on error
        };
    }

    // Download all button handler removed - now handled by dropdown menu

    // Modify stop all button handler
    stopAllButton?.addEventListener('click', async function () {
        if (isDownloadingAll && currentBatchId) {
            try {
                const response = await fetch(`/cancel_download/${currentBatchId}`, {
                    method: 'POST'
                });
                if (!response.ok) throw new Error('Failed to cancel download');

                isDownloadingAll = false;
                currentBatchId = null;
                updateDownloadUI(false);

                // Reset all loading buttons
                downloadButtons.forEach(button => {
                    if (button.classList.contains('loading')) {
                        button.classList.remove('loading');
                        button.querySelector('.button-text').textContent = 'Download';

                        const episodeItem = button.closest('.episode-item');
                        const progressDiv = episodeItem.querySelector('.download-progress');
                        progressDiv.style.display = 'none';
                    }
                });
            } catch (error) {
                console.error('Error canceling download:', error);
            }
        }
    });

    // Check active downloads on page load
    await checkActiveDownloads();

    // Add page unload handler
    window.addEventListener('beforeunload', function (e) {
        clearDownloadState();
    });

    // Note: individual handlers are attached via bindEpisodeEventHandlers

    // Handle cancel buttons
    cancelButtons.forEach(button => {
        button.addEventListener('click', async function () {
            const episodeItem = button.closest('.episode-item');
            const downloadButton = episodeItem.querySelector('.download-button');

            if (downloadButton.classList.contains('loading')) {
                try {
                    const progressDiv = episodeItem.querySelector('.download-progress');
                    const eventSourceUrl = progressDiv.dataset.eventSource;
                    const downloadId = eventSourceUrl.split('/').pop();

                    const response = await fetch(`/cancel_download/${downloadId}`, {
                        method: 'POST'
                    });

                    if (!response.ok) throw new Error('Failed to cancel download');

                    // Reset UI
                    downloadButton.classList.remove('loading');
                    downloadButton.querySelector('.button-text').textContent = 'Download';
                    progressDiv.style.display = 'none';

                } catch (error) {
                    console.error('Error canceling download:', error);
                }
            }
        });
    });

    // Auto-download and update show button handlers removed - now handled by dropdown menu

    // Add this new section for track downloads
    const trackDownloadButtons = document.querySelectorAll('.track-download-btn');

    // Add automatic mark as seen functionality
    function autoMarkAsRead() {
        const showUrl = window.location.pathname.split('/show/')[1];
        
        // Prevent duplicate calls for the same show (module-level tracking)
        if (markedAsReadShows.has(showUrl)) return;
        
        // Check if there are any new episodes
        const newEpisodes = document.querySelectorAll('.episode-item.new');
        if (newEpisodes.length > 0) {
            markedAsReadShows.add(showUrl);
            // Wait 3 seconds before marking episodes as read
            setTimeout(async () => {
                try {
                    const response = await fetch(`/mark_read/${encodeURIComponent(showUrl)}`, {
                        method: 'POST'
                    });

                    if (response.ok) {
                        console.log('Episodes automatically marked as read');
                        // We don't remove the visual indicators until page refresh
                    } else {
                        throw new Error('Failed to mark episodes as read');
                    }
                } catch (error) {
                    console.error('Error marking episodes as read:', error);
                    // On error, allow retry
                    markedAsReadShows.delete(showUrl);
                }
            }, 3000); // 3 seconds delay
        }
    }

    // Call the auto mark as read function when the page loads
    autoMarkAsRead();

    // Check liked status for all tracks on page load
    // Track in-flight likes check to prevent duplicate calls
    let likesCheckInProgress = false;
    
    async function checkLikedStatus(root = document) {
        const likeButtons = root.querySelectorAll('.track-like-btn');
        if (!likeButtons.length) return;
        
        // Prevent duplicate concurrent checks for the same scope
        if (root === document && likesCheckInProgress) return;
        if (root === document) likesCheckInProgress = true;

        // Collect all track info (only unchecked buttons)
        const tracks = [];
        likeButtons.forEach(btn => {
            // Skip if already checked
            if (btn.dataset.likesChecked === 'true') return;
            btn.dataset.likesChecked = 'true';
            tracks.push({
                artist: btn.dataset.artist || '',
                title: btn.dataset.title || ''
            });
        });
        
        if (!tracks.length) {
            if (root === document) likesCheckInProgress = false;
            return;
        }

        // Backend limits to 200 tracks per request, so chunk into batches
        const BATCH_SIZE = 200;
        const allLiked = {};

        try {
            // Process tracks in batches
            for (let i = 0; i < tracks.length; i += BATCH_SIZE) {
                const batch = tracks.slice(i, i + BATCH_SIZE);
                const res = await fetch('/api/likes/check', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tracks: batch })
                });
                const data = await res.json();
                if (data.success && data.liked) {
                    Object.assign(allLiked, data.liked);
                }
            }

            // Apply liked status to buttons
            likeButtons.forEach(btn => {
                const artist = (btn.dataset.artist || '').trim().toLowerCase();
                const title = (btn.dataset.title || '').trim().toLowerCase();
                const key = `${artist}|||${title}`;
                const info = allLiked[key];
                if (info && info.liked) {
                    btn.classList.add('liked');
                    btn.dataset.likeId = info.id;
                    btn.querySelector('i').className = 'fas fa-heart';
                    btn.title = 'Unlike track';
                }
            });
        } catch (err) {
            console.error('Failed to check liked status:', err);
        } finally {
            if (root === document) likesCheckInProgress = false;
        }
    }

    // Check liked status on page load
    checkLikedStatus();

    // Expose for dynamic content
    window.checkLikedStatus = checkLikedStatus;

    // Handle #episode-<slug> format from likes page
    try {
        const hash = window.location.hash || '';
        if (hash.startsWith('#episode-')) {
            const targetId = hash.substring(1); // Remove the #
            const targetElement = document.getElementById(targetId);
            if (targetElement) {
                setTimeout(() => {
                    targetElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    targetElement.classList.add('highlight');
                    setTimeout(() => targetElement.classList.remove('highlight'), 2500);
                }, 100);
            }
        }
    } catch (_) { /* ignore */ }

    // Note: #ep= fragment handling moved to after initEpisodesInfiniteScroll() 
    // so the IntersectionObserver is already set up when we need to trigger it

    trackDownloadButtons.forEach(button => {
        if (button.__bound) return;
        button.__bound = true;
        button.addEventListener('click', async function () {
            if (button.classList.contains('searching')) return;

            const artist = button.dataset.artist;
            const title = button.dataset.title;

            try {
                button.classList.add('searching');
                const originalHTML = button.innerHTML;

                const response = await fetch('/download_track', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ artist, title })
                });

                if (!response.ok) throw new Error('Failed to create Discogs search');

                const data = await response.json();

                if (data.success && data.url) {
                    // Show notification based on the message
                    if (data.message.includes('Found specific release')) {
                        // Removed notification
                        // showNotification(`Found release for "${artist} - ${title}" on Discogs`, 'success');
                    } else {
                        // Removed notification
                        // showNotification(`Searching Discogs for "${artist} - ${title}"`, 'info');
                    }

                    // Open Discogs search in a new tab
                    window.open(data.url, '_blank');
                } else {
                    throw new Error(data.message || 'Failed to create Discogs search');
                }

                button.classList.remove('searching');

            } catch (error) {
                console.error('Discogs search error:', error);
                button.classList.remove('searching');
                // Keep error notification for better user experience
                showNotification(`Failed to search on Discogs: ${error.message}`, 'error');
            }
        });
    });

    // Add YouTube search functionality
    const trackYoutubeButtons = document.querySelectorAll('.track-youtube-btn');

    trackYoutubeButtons.forEach(button => {
        if (button.__bound) return;
        button.__bound = true;
        button.addEventListener('click', async function () {
            if (button.classList.contains('searching')) return;

            const artist = button.dataset.artist;
            const title = button.dataset.title;

            try {
                const response = await fetch('/search_youtube', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ artist, title })
                });

                if (!response.ok) throw new Error('Failed to create YouTube search');

                const data = await response.json();

                if (data.success) {
                    if (data.search_only) {
                        // If we only have a search URL, open it in a new tab
                        showNotification(`Searching YouTube for "${artist} - ${title}"`, 'info');
                        window.open(data.video_url, '_blank');
                    } else {
                        // Show the video in the sticky player
                        showYouTubePlayer(data, artist, title, null, null, window.location.pathname, null, buildTrackPlaybackContext(button));
                    }
                } else {
                    // Check for quota exceeded error
                    if (data.quota_exceeded) {
                        showNotification('YouTube API daily quota exceeded. Please try again tomorrow.', 'error', 10000);
                        // Disable all YouTube buttons to prevent further API calls
                        disableYouTubeButtons();
                    } else {
                        throw new Error(data.message || 'Failed to create YouTube search');
                    }
                }

            } catch (error) {
                console.error('YouTube search error:', error);
                showNotification(`Failed to search on YouTube: ${error.message}`, 'error');
            }
        });
    });

    // Function to disable all YouTube buttons when quota is exceeded
    function disableYouTubeButtons() {
        const buttons = document.querySelectorAll('.track-youtube-btn');
        buttons.forEach(button => {
            button.disabled = true;
            button.classList.add('disabled');
            button.title = 'YouTube API quota exceeded. Please try again tomorrow.';
        });
    }

    // YouTube Player is now handled by the global youtube-player-global.js module
    // The window.showYouTubePlayer function is provided by that module

    // Initialize infinite scrolling for episodes list
    const infiniteScrollController = initEpisodesInfiniteScroll();
    showPageCleanupResources.infiniteScrollObserver = infiniteScrollController?.observer || null;


    // Handle #ep=<encoded episode url> fragment - MUST be after initEpisodesInfiniteScroll
    // so the IntersectionObserver is already set up when we try to trigger it
    (async () => {
        try {
            const hash = window.location.hash || '';
            const params = new URLSearchParams(hash.replace(/^#/, ''));
            const epParam = params.get('ep');
            const trackParam = params.get('track');
            const artistParam = params.get('artist');
            const qParam = params.get('q');
            if (!epParam) return;
            const targetUrl = normalizeEpisodeUrl(decodeURIComponent(epParam));
            const requestedTrack = normalizeSearchText(trackParam);
            const requestedArtist = normalizeSearchText(artistParam);
            const requestedTrackTokens = tokenizeSearchText(trackParam);
            const requestedArtistTokens = tokenizeSearchText(artistParam);
            const queryTokens = tokenizeSearchText(qParam);
            const highlightTerms = Array.from(new Set(
                [qParam, artistParam, trackParam]
                    .filter(Boolean)
                    .flatMap((value) => String(value).split(/\s+/))
                    .map((value) => value.trim())
                    .filter(Boolean)
            ));
            const episodesList = document.getElementById('episodesList');
            const showUrl = episodesList?.dataset.showUrl || '';
            const perPage = parseInt(episodesList?.dataset.perPage || episodesList?.dataset.per_page || '20', 10);
            const loadMoreEpisodes = infiniteScrollController && typeof infiniteScrollController.loadNextPage === 'function'
                ? infiniteScrollController.loadNextPage
                : null;

            const highlightNode = (el) => {
                if (!el || !highlightTerms.length) return;
                const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
                const textNodes = [];
                while (walker.nextNode()) textNodes.push(walker.currentNode);
                textNodes.forEach(node => {
                    const original = node.nodeValue || '';
                    const lc = original.toLowerCase();
                    if (highlightTerms.some(term => lc.includes(term.toLowerCase()))) {
                        const span = document.createElement('span');
                        span.innerHTML = original.replace(
                            new RegExp(`(${highlightTerms.map(escapeRegExp).join('|')})`, 'ig'),
                            '<mark>$1</mark>',
                        );
                        node.parentNode.replaceChild(span, node);
                    }
                });
            };

            const findTargetTrackItem = (item) => {
                if (!requestedTrack && !requestedArtist && !queryTokens.length) return null;
                const trackItems = Array.from(item.querySelectorAll('.track-item'));
                let bestMatch = null;
                let bestScore = -1;

                trackItems.forEach((trackItem) => {
                    const titleText = trackItem.querySelector('.track-title')?.textContent || '';
                    const artistText = trackItem.querySelector('.track-artist')?.textContent || '';
                    const title = normalizeSearchText(titleText);
                    const artist = normalizeSearchText(artistText);
                    const titleTokens = tokenizeSearchText(titleText);
                    const artistTokens = tokenizeSearchText(artistText);
                    const combinedTokens = new Set([...titleTokens, ...artistTokens]);

                    const matchesTrack = !requestedTrack
                        || title === requestedTrack
                        || title.includes(requestedTrack)
                        || requestedTrack.includes(title)
                        || requestedTrackTokens.every((token) => titleTokens.includes(token));
                    const matchesArtist = !requestedArtist
                        || artist === requestedArtist
                        || artist.includes(requestedArtist)
                        || requestedArtist.includes(artist)
                        || requestedArtistTokens.every((token) => artistTokens.includes(token));
                    const matchedQueryTokens = queryTokens.filter((token) => combinedTokens.has(token));
                    const matchesQuery = !queryTokens.length || matchedQueryTokens.length > 0;

                    if (!matchesTrack || !matchesArtist || !matchesQuery) return;

                    let score = 0;
                    if (requestedTrack) {
                        if (title === requestedTrack) score += 100;
                        else if (title.includes(requestedTrack) || requestedTrack.includes(title)) score += 70;
                        score += requestedTrackTokens.filter((token) => titleTokens.includes(token)).length * 10;
                    }
                    if (requestedArtist) {
                        if (artist === requestedArtist) score += 60;
                        else if (artist.includes(requestedArtist) || requestedArtist.includes(artist)) score += 40;
                        score += requestedArtistTokens.filter((token) => artistTokens.includes(token)).length * 8;
                    }
                    score += matchedQueryTokens.length * 4;

                    if (score > bestScore) {
                        bestScore = score;
                        bestMatch = trackItem;
                    }
                });

                return bestMatch;
            };

            const scrollAndHighlight = async (item) => {
                smartScrollIntoView(item, { block: 'start' });
                item.classList.add('highlight');
                setTimeout(() => item.classList.remove('highlight'), TARGET_HIGHLIGHT_DURATION_MS);

                const targetTrackItem = findTargetTrackItem(item);
                if (targetTrackItem) {
                    await new Promise((resolve) => setTimeout(resolve, 120));
                    smartScrollIntoView(targetTrackItem, { block: 'center' });
                    targetTrackItem.classList.add('highlight');
                    setTimeout(() => targetTrackItem.classList.remove('highlight'), TARGET_HIGHLIGHT_DURATION_MS);
                    targetTrackItem.querySelectorAll('.track-artist, .track-title').forEach(el => highlightNode(el));
                    return;
                }

                item.querySelectorAll('.track-artist, .track-title').forEach(el => highlightNode(el));
            };

            const resolveTargetEpisode = async () => {
                let item = findEpisodeItemByUrl(document, targetUrl);
                if (item) return item;

                if (episodesList && showUrl) {
                    try {
                        const res = await dedupedFetch(
                            `/api/show/${encodeURIComponent(showUrl)}/episode?episode_url=${encodeURIComponent(targetUrl)}&per_page=${perPage}`,
                        );
                        const data = await res.json();
                        if (res.ok && data.success && data.episode) {
                            item = insertResolvedEpisode(episodesList, data.episode);
                            if (item) return item;
                        }
                    } catch (error) {
                        console.warn('[ShowPage] Exact episode lookup failed', error);
                    }
                }

                let attempts = 0;
                const MAX_ATTEMPTS = 40;
                while (attempts < MAX_ATTEMPTS) {
                    item = findEpisodeItemByUrl(document, targetUrl);
                    if (item) return item;
                    if (!loadMoreEpisodes) break;
                    const loaded = await loadMoreEpisodes();
                    if (!loaded) break;
                    attempts += 1;
                    if (episodesList) {
                        const page = parseInt(episodesList.dataset.page || '1', 10);
                        const perPage = parseInt(episodesList.dataset.perPage || episodesList.dataset.per_page || '20', 10);
                        const total = parseInt(episodesList.dataset.total || '0', 10);
                        if (page * perPage >= total) break;
                    }
                }
                return findEpisodeItemByUrl(document, targetUrl);
            };

            const targetItem = await resolveTargetEpisode();
            if (targetItem) await scrollAndHighlight(targetItem);
        } catch (_) { /* ignore */ }
    })();

    // Set up cleanup function for SPA navigation - prevents memory leaks
    window._showPageCleanup = () => {
        // Disconnect observers
        if (showPageCleanupResources.tracklistObserver) {
            showPageCleanupResources.tracklistObserver.disconnect();
            showPageCleanupResources.tracklistObserver = null;
        }
        if (showPageCleanupResources.infiniteScrollObserver) {
            showPageCleanupResources.infiniteScrollObserver.disconnect();
            showPageCleanupResources.infiniteScrollObserver = null;
        }
        
        // Remove event listeners
        if (showPageCleanupResources.scrollHandler) {
            window.removeEventListener('scroll', showPageCleanupResources.scrollHandler);
            showPageCleanupResources.scrollHandler = null;
        }
        if (showPageCleanupResources.resizeHandler) {
            window.removeEventListener('resize', showPageCleanupResources.resizeHandler);
            showPageCleanupResources.resizeHandler = null;
        }
    };
}

function handleBatchProgress(progress, downloadButtons) {
    const downloadAllMenuItem = document.querySelector('.show-menu-item[data-action="download-all"]');
    const itemText = downloadAllMenuItem?.querySelector('span:not(.auto-status)');
    
    if (progress.status === 'init') {
        if (itemText) {
            itemText.textContent = `Preparing to download ${progress.total} episodes...`;
        }
    }
    else if (progress.status === 'starting') {
        if (itemText) {
            itemText.textContent = `Downloading ${progress.current}/${progress.total} episodes`;
        }

        const episodeButton = Array.from(downloadButtons)
            .find(button => button.href.includes(progress.episode_url));
        if (episodeButton) {
            const episodeItem = episodeButton.closest('.episode-item');
            const progressDiv = episodeItem.querySelector('.download-progress');
            const progressBar = progressDiv.querySelector('.progress-bar');
            const progressInfo = progressDiv.querySelector('.progress-info');

            progressDiv.style.display = 'block';
            progressBar.style.display = 'block';
            progressBar.style.width = '0%';
            episodeButton.classList.add('loading');
            episodeButton.querySelector('.button-text').textContent = 'Downloading...';
            progressInfo.textContent = 'Starting download...';
        }
    }
    else if (progress.status === 'progress' && progress.episode_url) {
        const episodeButton = Array.from(downloadButtons)
            .find(button => button.href.includes(progress.episode_url));
        if (episodeButton) {
            const episodeItem = episodeButton.closest('.episode-item');
            const progressDiv = episodeItem.querySelector('.download-progress');
            const progressBar = progressDiv.querySelector('.progress-bar');
            const progressInfo = progressDiv.querySelector('.progress-info');

            progressDiv.style.display = 'block';
            progressBar.style.display = 'block';

            if (progress.percent !== undefined) {
                if (progress.percent >= 90) {
                    progressBar.style.display = 'none';
                    progressInfo.textContent = 'Converting...';
                } else {
                    progressBar.style.width = `${progress.percent}%`;
                    let statusText = `${progress.percent.toFixed(1)}%`;
                    if (progress.speed) statusText += ` - ${formatSpeed(progress.speed)}`;
                    if (progress.eta) statusText += ` - ${formatETA(progress.eta)}`;
                    progressInfo.textContent = statusText;
                }
            }
            if (progress.message) {
                progressInfo.textContent = progress.message;
            }
        }
    }
}

// Infinite scrolling for episodes list - now a function called from initShowPageCore
function initEpisodesInfiniteScroll() {
    const episodesList = document.getElementById('episodesList');
    const sentinel = document.getElementById('loadMoreTrigger');
    if (!episodesList || !sentinel) return null;

    let isLoadingMore = false;
    let currentLoadPromise = null;
    let page = parseInt(episodesList.dataset.page || '1', 10);
    const perPage = parseInt(episodesList.dataset.perPage || episodesList.dataset.per_page || '20', 10);
    const total = parseInt(episodesList.dataset.total || '0', 10);
    const showUrl = episodesList.dataset.showUrl;

    let observer = null;
    const loadNextPage = async () => {
        const loadedCount = page * perPage;
        if (loadedCount >= total) {
            if (observer) observer.disconnect();
            return false;
        }

        if (currentLoadPromise) return currentLoadPromise;

        currentLoadPromise = (async () => {
            isLoadingMore = true;
            try {
                const nextPage = page + 1;
                const res = await fetch(`/api/show/${encodeURIComponent(showUrl)}/episodes?page=${nextPage}&per_page=${perPage}`);
                const data = await res.json();
                if (!data.success) throw new Error(data.message || 'Failed to load episodes');
                appendEpisodesToList(episodesList, data.episodes);
                page = data.page;
                episodesList.dataset.page = String(page);
                if (!data.has_more && observer) observer.disconnect();
                return true;
            } catch (e) {
                console.error('Failed to load more episodes', e);
                return false;
            } finally {
                isLoadingMore = false;
                currentLoadPromise = null;
            }
        })();

        return currentLoadPromise;
    };

    observer = new IntersectionObserver(async (entries) => {
        const entry = entries[0];
        if (!entry.isIntersecting || isLoadingMore) return;
        await loadNextPage();
    }, { rootMargin: '600px 0px' });

    observer.observe(sentinel);
    
    // Return observer for cleanup
    return { observer, loadNextPage };
}
