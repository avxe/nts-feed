/**
 * Feed page (homepage) functionality
 */

(function () {
    'use strict';

    function initFeedPageHandlers() {
        if (window.location.pathname !== '/' && window.location.pathname !== '/index.html') return;

        console.log('[FeedPage] Initializing feed page handlers');

        const subscribeBtn = document.getElementById('openSubscribeFromFeed');
        if (subscribeBtn && !subscribeBtn.__feedBound) {
            subscribeBtn.__feedBound = true;
            subscribeBtn.addEventListener('click', function () {
                const modal = document.getElementById('subscribeModal');
                if (modal) modal.classList.add('show');
            });
        }

        if (typeof window.initTrackHandlers === 'function') {
            window.initTrackHandlers();
        }
    }

    window.initFeedPageHandlers = initFeedPageHandlers;

    if (window.NTSPageModules && typeof window.NTSPageModules.register === 'function') {
        window.NTSPageModules.register('feed', {
            init: initFeedPageHandlers,
            cleanup() {},
        });
    }
})();
