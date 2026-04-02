/**
 * App Initialization Script
 * Moved from inline script in base.html for CSP compliance
 * This runs after all page scripts are loaded
 */
(function() {
    'use strict';
    
    // Ensure progress UI exists in the modal
    function ensureSubscribeProgressUI(modal) {
        if (!modal) return;
        if (modal.querySelector('#subscribeProgress')) return;
        const body = modal.querySelector('.subscribe-modal-body');
        if (!body) return;
        const container = document.createElement('div');
        container.id = 'subscribeProgress';
        container.style.display = 'none';
        container.style.marginTop = '10px';
        container.innerHTML = `
            <div class="progress-row" style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;">
                <div class="status" style="font-size:0.9rem; color:var(--color-text-secondary)">Preparing…</div>
                <div class="count" style="font-size:0.9rem; color:var(--color-text-secondary)"></div>
            </div>
            <div class="bar" style="height:6px; background:var(--color-surface-alt); border-radius:4px; overflow:hidden;">
                <div class="bar-fill" style="height:100%; width:0%; background:var(--color-text-primary); transition:width .25s ease;"></div>
            </div>
        `;
        body.appendChild(container);
    }
    
    // Track subscribe progress via SSE
    function trackSubscribeProgress(subscribeId, handlers) {
        return new Promise((resolve, reject) => {
            const evt = new EventSource(`/subscribe_progress/${subscribeId}`);
            let finished = false;
            evt.onmessage = (e) => {
                try {
                    const data = JSON.parse(e.data);
                    if (data.type === 'started') {
                        handlers.onStarted?.(data.total_episodes, data.show_title);
                    } else if (data.type === 'progress') {
                        handlers.onProgress?.(data.current, data.total, data.episode_title);
                    } else if (data.type === 'saved') {
                        handlers.onSaved?.(data.total);
                    } else if (data.type === 'sync_status' || data.type === 'sync_started') {
                        const sync = data.sync || {};
                        const syncStatus = sync.status || data.status;
                        if (syncStatus === 'failed' || syncStatus === 'timed_out') {
                            finished = true;
                            handlers.onError?.(
                                data.message
                                || sync.error
                                || 'Database sync failed',
                            );
                            evt.close();
                            reject(new Error(sync.error || 'Database sync failed'));
                        } else {
                            handlers.onSyncStarted?.(sync.sync_job_id || data.sync_job_id, syncStatus);
                        }
                    } else if (data.type === 'completed') {
                        finished = true;
                        handlers.onCompleted?.(data.total);
                        evt.close();
                        resolve();
                    } else if (data.already_exists) {
                        finished = true;
                        handlers.onAlreadyExists?.();
                        evt.close();
                        resolve();
                    } else if (data.type === 'error') {
                        finished = true;
                        handlers.onError?.(data.message);
                        evt.close();
                        reject(new Error(data.message || 'Subscribe failed'));
                    }
                } catch (err) {
                    console.error('subscribe progress parse error', err);
                }
            };
            evt.onerror = () => {
                if (!finished) {
                    evt.close();
                    handlers.onError?.('Connection lost');
                    reject(new Error('Connection lost'));
                }
            };
            window.addEventListener('beforeunload', () => evt.close());
        });
    }
    
    // Handle subscribe form submission with progress
    async function handleSubscribeFormSubmit(form) {
        const button = form.querySelector('button');
        const buttonText = button?.querySelector('.button-text');
        const spinner = button?.querySelector('.loading-spinner');
        const originalButtonContent = buttonText?.innerHTML || 'Subscribe';
        const modal = form.closest('.subscribe-modal');

        try {
            form.classList.add('loading');
            if (button) button.disabled = true;
            if (spinner) spinner.style.display = 'block';

            // Kick off async subscribe for progressive feedback
            const formData = new FormData(form);
            const startResp = await fetch('/subscribe_async', { method: 'POST', body: formData });
            const startData = await startResp.json();
            if (!startData.success) throw new Error(startData.message || 'Failed to start subscription');

            // Build progress UI in modal
            ensureSubscribeProgressUI(modal);
            const progress = modal?.querySelector('#subscribeProgress');
            const statusEl = progress?.querySelector('.status');
            const barFill = progress?.querySelector('.bar-fill');
            const countEl = progress?.querySelector('.count');

            if (buttonText) buttonText.innerHTML = 'Starting...';

            await trackSubscribeProgress(startData.subscribe_id, {
                onStarted: (total, title) => {
                    if (statusEl) statusEl.textContent = `Fetching episodes for ${title}...`;
                    if (countEl) countEl.textContent = `0 / ${total}`;
                    if (barFill) barFill.style.width = '0%';
                    if (progress) { progress.classList.remove('hidden'); progress.style.display = 'block'; }
                },
                onProgress: (current, total) => {
                    const pct = total ? Math.floor((current / total) * 100) : 0;
                    if (barFill) barFill.style.width = `${pct}%`;
                    if (statusEl) statusEl.textContent = `Adding episode ${current} of ${total}`;
                    if (countEl) countEl.textContent = `${current} / ${total}`;
                },
                onSaved: (total) => {
                    if (statusEl) statusEl.textContent = `Saved ${total} episodes`;
                    if (barFill) barFill.style.width = '100%';
                    if (countEl) countEl.textContent = `${total} / ${total}`;
                },
                onSyncStarted: (_jobId, syncStatus) => {
                    if (!statusEl) return;
                    if (syncStatus === 'completed') {
                        statusEl.textContent = 'Database sync complete';
                    } else {
                        statusEl.textContent = 'Syncing database...';
                    }
                },
                onCompleted: (total) => {
                    if (statusEl) statusEl.textContent = `Done. Added ${total} episodes`;
                    if (barFill) barFill.style.width = '100%';
                    setTimeout(() => window.location.reload(), 800);
                },
                onAlreadyExists: () => {
                    if (statusEl) statusEl.textContent = 'Show already exists';
                    if (barFill) barFill.style.width = '100%';
                    setTimeout(() => window.location.reload(), 600);
                },
                onError: (message) => {
                    if (typeof showNotification === 'function') {
                        showNotification(message || 'Subscription failed', 'error');
                    }
                    if (buttonText) buttonText.innerHTML = originalButtonContent;
                    resetFormState();
                }
            });
        } catch (error) {
            if (typeof showNotification === 'function') {
                showNotification(error.message || 'An error occurred', 'error');
            }
            if (buttonText) buttonText.innerHTML = originalButtonContent;
            resetFormState();
        }

        function resetFormState() {
            setTimeout(() => {
                form.classList.remove('loading');
                if (button) button.disabled = false;
                if (spinner) spinner.style.display = 'none';
                if (buttonText) buttonText.innerHTML = originalButtonContent;
            }, 500);
        }
    }
    
    // Global subscribe modal handlers (works on all pages)
    function initGlobalSubscribeModal() {
        const subscribeModal = document.getElementById('subscribeModal');
        const subscribeForm = document.getElementById('subscribeForm');
        const openSubscribeModalButton = document.getElementById('openSubscribeModalButton');
        const closeSubscribeModalBtn = document.getElementById('closeSubscribeModal');
        
        if (!subscribeModal) return;
        
        function openModal() {
            subscribeModal.classList.add('show');
            subscribeModal.setAttribute('aria-hidden', 'false');
            setTimeout(() => document.getElementById('subscribeUrl')?.focus(), 50);
        }
        
        function closeModal() {
            subscribeModal.classList.remove('show');
            subscribeModal.setAttribute('aria-hidden', 'true');
        }
        
        // Bind header button (only if not already bound)
        if (openSubscribeModalButton && !openSubscribeModalButton.__globalBound) {
            openSubscribeModalButton.__globalBound = true;
            openSubscribeModalButton.addEventListener('click', openModal);
        }
        
        // Bind close button (only if not already bound)
        if (closeSubscribeModalBtn && !closeSubscribeModalBtn.__globalBound) {
            closeSubscribeModalBtn.__globalBound = true;
            closeSubscribeModalBtn.addEventListener('click', closeModal);
        }
        
        // Bind backdrop click (only if not already bound)
        if (!subscribeModal.__globalBound) {
            subscribeModal.__globalBound = true;
            subscribeModal.addEventListener('click', (e) => {
                if (e.target === subscribeModal) closeModal();
            });
        }
        
        // Global escape key handler (only bind once)
        if (!window.__subscribeModalEscapeBound) {
            window.__subscribeModalEscapeBound = true;
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && subscribeModal.classList.contains('show')) {
                    closeModal();
                }
            });
        }
        
        // Bind subscribe form submission (only if not already bound)
        if (subscribeForm && !subscribeForm.__globalBound) {
            subscribeForm.__globalBound = true;
            subscribeForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                await handleSubscribeFormSubmit(subscribeForm);
            });
        }
        
        // Expose for other scripts
        window.openSubscribeModal = openModal;
        window.closeSubscribeModal = closeModal;
    }
    
    function initApp() {
        // Initialize global subscribe modal handlers (works on all pages)
        initGlobalSubscribeModal();

        if (window.GlobalSearch && typeof window.GlobalSearch.init === 'function') {
            window.GlobalSearch.init();
        }
        
        // Initialize global YouTube player
        if (window.YouTubePlayerGlobal) {
            window.YouTubePlayerGlobal.init();
        }
        // Initialize SPA router
        if (window.SPARouter) {
            window.SPARouter.init();
            if (typeof window.SPARouter.initCurrentPage === 'function') {
                window.SPARouter.initCurrentPage();
            }
        }
    }
    
    // Run on DOMContentLoaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initApp);
    } else {
        // DOM already loaded, run immediately
        initApp();
    }
    
    document.addEventListener('spa:pagechange', initGlobalSubscribeModal);
})();
