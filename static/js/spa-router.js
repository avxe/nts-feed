/**
 * SPA Router Module
 * Keeps persistent global UI mounted while swapping page content.
 */

(function () {
    'use strict';

    const config = {
        contentSelector: '#page-content',
        loadingBarSelector: '#spaLoadingBar',
        spaRoutes: ['/', '/shows', '/show/', '/likes', '/stats', '/mixtape', '/discover', '/admin', '/search'],
        ignoreSelectors: [
            '[target="_blank"]',
            '[download]',
            '[data-no-spa]',
            '.download-button',
            'a[href^="http"]',
            'a[href^="//"]',
            'a[href^="mailto:"]',
            'a[href^="tel:"]',
            'a[href^="#"]',
            'a[href*="/download_episode"]',
            'a[href*="/api/"]',
        ],
    };

    const pageAssets = {
        feed: {
            styles: ['css/pages/feed.css'],
            scripts: ['js/nts-audio-player.js', 'js/downloads.js', 'js/show-page.js', 'js/feed-page.js'],
        },
        shows: {
            styles: ['css/pages/shows.css'],
            scripts: ['js/index-page.js'],
        },
        show: {
            styles: ['css/pages/show.css'],
            scripts: ['js/nts-audio-player.js', 'js/downloads.js', 'js/genre-search.js', 'js/show-page.js'],
        },
        discover: {
            styles: ['css/pages/discover.css'],
            scripts: ['js/nts-audio-player.js', 'js/discover-page.js'],
        },
        likes: {
            styles: ['css/pages/likes.css'],
            scripts: ['js/nts-audio-player.js', 'js/likes.js'],
        },
        stats: {
            styles: ['css/pages/stats.css'],
            scripts: ['js/stats.js'],
        },
        search: {
            styles: [],
            scripts: ['js/search-page.js'],
        },
        admin: {
            styles: ['css/pages/admin.css'],
            scripts: ['js/admin.js'],
        },
    };

    let initialized = false;
    let isNavigating = false;
    let currentPath = window.location.pathname;
    let activePageKey = null;
    let activePageModule = null;

    function init() {
        if (initialized) return;

        document.addEventListener('click', handleLinkClick);
        window.addEventListener('popstate', handlePopState);
        history.replaceState({ path: currentPath, scrollY: 0 }, '', currentPath);

        initialized = true;
        console.log('[SPARouter] Initialized');
    }

    function shouldUseSPA(href) {
        try {
            const url = new URL(href, window.location.origin);
            if (url.origin !== window.location.origin) return false;

            return config.spaRoutes.some((route) => (
                route.endsWith('/') ? url.pathname.startsWith(route) : url.pathname === route
            ));
        } catch (_) {
            return false;
        }
    }

    function handleLinkClick(event) {
        const link = event.target.closest('a');
        if (!link) return;

        const href = link.getAttribute('href');
        if (!href) return;

        for (const selector of config.ignoreSelectors) {
            if (link.matches(selector)) return;
        }

        if (!shouldUseSPA(href)) return;

        event.preventDefault();
        navigateTo(href);
    }

    function handlePopState(event) {
        if (event.state && event.state.path) {
            navigateTo(event.state.path, { pushState: false, scrollY: event.state.scrollY || 0 });
        }
    }

    async function navigateTo(href, options = {}) {
        const { pushState = true, scrollY = 0 } = options;
        if (isNavigating) return;

        isNavigating = true;

        const contentEl = document.querySelector(config.contentSelector);
        const loadingBar = document.querySelector(config.loadingBarSelector);

        try {
            const url = new URL(href, window.location.origin);
            const targetPath = url.pathname + url.search + url.hash;

            showLoading(contentEl, loadingBar);

            const fetchUrl = new URL(url.pathname + url.search, window.location.origin);
            fetchUrl.searchParams.set('partial', '1');

            const response = await fetch(fetchUrl.toString(), {
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-SPA-Request': '1',
                },
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const html = await response.text();

            if (pushState) {
                const currentState = history.state || {};
                history.replaceState({ ...currentState, scrollY: window.scrollY }, '', currentPath);
                history.pushState({ path: targetPath, scrollY: 0 }, '', targetPath);
            }

            currentPath = targetPath;
            cleanupPreviousPage();

            contentEl.innerHTML = html;
            executeScripts(contentEl);

            await initPage(url.pathname);

            if (window.YouTubePlayerGlobal) {
                window.YouTubePlayerGlobal.rebuildTrackList();
            }

            hideLoading(contentEl, loadingBar);
            handleScrollAfterNavigation(url, scrollY);

            document.dispatchEvent(new CustomEvent('spa:pagechange', {
                detail: { path: targetPath },
            }));
        } catch (error) {
            console.error('[SPARouter] Navigation error:', error);
            hideLoading(contentEl, loadingBar);
            window.location.href = href;
        } finally {
            isNavigating = false;
        }
    }

    function handleScrollAfterNavigation(url, scrollY) {
        if (url.hash) {
            const isValidIdSelector = /^#[a-zA-Z_][a-zA-Z0-9_-]*$/.test(url.hash);
            if (isValidIdSelector) {
                const hashTarget = document.querySelector(url.hash);
                if (hashTarget) {
                    setTimeout(() => hashTarget.scrollIntoView({ behavior: 'smooth' }), 100);
                }
            }
            return;
        }

        if (scrollY > 0) {
            window.scrollTo(0, scrollY);
            return;
        }

        window.scrollTo(0, 0);
    }

    function showLoading(contentEl, loadingBar) {
        if (contentEl) {
            contentEl.classList.add('loading');
        }
        if (loadingBar) {
            loadingBar.style.width = '0%';
            loadingBar.classList.add('active');
            setTimeout(() => { loadingBar.style.width = '70%'; }, 10);
        }
    }

    function hideLoading(contentEl, loadingBar) {
        if (contentEl) {
            contentEl.classList.remove('loading');
        }
        if (loadingBar) {
            loadingBar.style.width = '100%';
            setTimeout(() => {
                loadingBar.classList.remove('active');
                loadingBar.style.width = '0%';
            }, 200);
        }
    }

    function cleanupPreviousPage() {
        if (activePageModule && typeof activePageModule.cleanup === 'function') {
            try {
                activePageModule.cleanup();
            } catch (_) {}
        }

        activePageKey = null;
        activePageModule = null;
    }

    function executeScripts(container) {
        const scripts = container.querySelectorAll('script');
        scripts.forEach((oldScript) => {
            if (oldScript.src) return;

            const newScript = document.createElement('script');
            newScript.textContent = oldScript.textContent;
            Array.from(oldScript.attributes).forEach((attr) => {
                newScript.setAttribute(attr.name, attr.value);
            });
            oldScript.parentNode.replaceChild(newScript, oldScript);
        });
    }

    async function initPage(pathname) {
        const pageKey = resolvePageKey(pathname);
        if (pageKey) {
            await ensurePageAssets(pageKey);
            const module = window.NTSPageModules && typeof window.NTSPageModules.get === 'function'
                ? window.NTSPageModules.get(pageKey)
                : null;

            if (module && typeof module.init === 'function') {
                module.init();
                activePageKey = pageKey;
                activePageModule = module;
            }
        }

        initCommon();
    }

    function initCommon() {
        if (typeof updateRelativeTimes === 'function') {
            updateRelativeTimes();
        }

        if (!document.getElementById('notifications')) {
            const notif = document.createElement('div');
            notif.id = 'notifications';
            document.body.appendChild(notif);
        }
    }

    function resolvePageKey(pathname) {
        if (pathname === '/' || pathname === '/index.html') return 'feed';
        if (pathname.startsWith('/show/')) return 'show';
        if (pathname === '/shows' || pathname === '/shows/') return 'shows';
        if (pathname === '/likes' || pathname === '/likes/') return 'likes';
        if (pathname === '/stats' || pathname === '/stats/') return 'stats';
        if (pathname === '/discover' || pathname === '/discover/' || pathname === '/mixtape' || pathname === '/mixtape/') return 'discover';
        if (pathname === '/admin' || pathname === '/admin/') return 'admin';
        if (pathname === '/search' || pathname === '/search/') return 'search';
        return null;
    }

    function ensureStyle(assetPath) {
        if (document.querySelector(`link[data-page-asset="${assetPath}"]`)) {
            return Promise.resolve();
        }

        return new Promise((resolve, reject) => {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = `/static/${assetPath}`;
            link.dataset.pageAsset = assetPath;
            link.onload = () => resolve();
            link.onerror = () => reject(new Error(`Failed to load ${assetPath}`));
            document.head.appendChild(link);
        });
    }

    function ensureScript(assetPath) {
        if (document.querySelector(`script[data-page-asset="${assetPath}"]`)) {
            return Promise.resolve();
        }

        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = `/static/${assetPath}`;
            script.async = false;
            script.dataset.pageAsset = assetPath;
            script.onload = () => resolve();
            script.onerror = () => reject(new Error(`Failed to load ${assetPath}`));
            document.body.appendChild(script);
        });
    }

    async function ensurePageAssets(pageKey) {
        const assets = pageAssets[pageKey];
        if (!assets) return;

        await Promise.all((assets.styles || []).map(ensureStyle));
        for (const assetPath of assets.scripts || []) {
            await ensureScript(assetPath);
        }
    }

    function navigate(href) {
        if (shouldUseSPA(href)) {
            navigateTo(href);
        } else {
            window.location.href = href;
        }
    }

    window.SPARouter = {
        init,
        initCurrentPage: () => initPage(window.location.pathname),
        navigate,
        shouldUseSPA,
        getCurrentPath: () => currentPath,
        getActivePageKey: () => activePageKey,
        isNavigating: () => isNavigating,
    };
})();
