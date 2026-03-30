let indexPageInitialized = false;
const SORT_STORAGE_KEY = 'nts-feed-sort';
const LEGACY_SORT_STORAGE_KEY = ['nts', 'tracker', 'sort'].join('-');

function getSavedSortPreference(storage = window.localStorage) {
    const savedSort = storage.getItem(SORT_STORAGE_KEY);
    if (savedSort) {
        return savedSort;
    }

    const legacySort = storage.getItem(LEGACY_SORT_STORAGE_KEY);
    if (legacySort) {
        storage.setItem(SORT_STORAGE_KEY, legacySort);
        storage.removeItem(LEGACY_SORT_STORAGE_KEY);
        return legacySort;
    }

    return 'updated';
}

function initIndexPageHandlers() {
    // Only run on shows page (formerly index)
    if (window.location.pathname !== '/shows' && window.location.pathname !== '/shows/') return;

    const updateForm = document.getElementById('updateForm');
    const sortSelect = document.getElementById('sortSelect');
    const showsList = document.getElementById('showsList');

    // Initialize sorting functionality
    initializeSorting();
    
    // Initialize relative time display
    updateRelativeTimes();
    
    // Update relative times every minute - only set once
    if (!indexPageInitialized) {
        setInterval(updateRelativeTimes, 60000);
        indexPageInitialized = true;
    }
    
    // Initialize show menus
    initializeShowMenus();

    // NOTE: Subscribe form handling is now global (in app-init.js)

    async function handleAsyncUpdate() {
        const button = updateForm.querySelector('button');
        const buttonText = button.querySelector('.button-text');
        const spinner = button.querySelector('.loading-spinner');
        const originalButtonContent = buttonText.innerHTML;

        try {
            // Start the async update
            updateForm.classList.add('loading');
            button.disabled = true;
            spinner.style.display = 'block';
            buttonText.innerHTML = '<i class="fas fa-clock"></i> Starting...';

            const response = await fetch('/update_async', { method: 'POST' });
            const isJson = (response.headers.get('content-type') || '').includes('application/json');
            let data;
            try {
                data = isJson ? await response.json() : null;
            } catch (_) {
                data = null;
            }
            // Fallback to legacy synchronous update if async endpoint is unavailable
            if (!response.ok || !data || data.success === undefined) {
                const fallbackResp = await fetch('/update', { method: 'POST' });
                const fbIsJson = (fallbackResp.headers.get('content-type') || '').includes('application/json');
                if (!fallbackResp.ok || !fbIsJson) throw new Error('Failed to start update');
                const fbData = await fallbackResp.json();
                if (!fbData.success) throw new Error(fbData.message || 'Failed to start update');
                await showNotification(`Update complete: ${fbData.new_episodes} new ${fbData.new_episodes !== 1 ? 'episodes' : 'episode'} found`, 'success');
                window.location.reload();
                return;
            }
            
            if (!data.success) {
                throw new Error(data.message || 'Failed to start update');
            }

            // Start progress tracking
            await trackUpdateProgress(data.update_id, button, buttonText, spinner);

        } catch (error) {
            console.error('Update error:', error);
            showNotification(`Update failed: ${error.message}`, 'error');
            
            // Reset button state
            updateForm.classList.remove('loading');
            button.disabled = false;
            spinner.style.display = 'none';
            buttonText.innerHTML = originalButtonContent;
        }
    }

    async function trackUpdateProgress(updateId, button, buttonText, spinner) {
        return new Promise((resolve, reject) => {
            const eventSource = new EventSource(`/update_progress/${updateId}`);
            let isCompleted = false;
            let progressHandle = null;

            eventSource.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    
                    switch (data.type) {
                        case 'started':
                            buttonText.innerHTML = `<i class="fas fa-sync-alt fa-spin"></i> Updating ${data.total_shows} shows...`;
                            // Start a single, persistent progress notification
                            progressHandle = showProgressNotification({
                                title: `Updating ${data.total_shows} shows…`,
                                percent: 0,
                                text: 'Initializing…'
                            });
                            break;
                            
                        case 'progress':
                            const percentage = Math.round(data.progress_percentage);
                            const eta = data.estimated_time_remaining 
                                ? ` (${Math.round(data.estimated_time_remaining)}s remaining)`
                                : '';
                            
                            buttonText.innerHTML = `<i class="fas fa-sync-alt fa-spin"></i> ${percentage}% (${data.completed_shows}/${data.total_shows})${eta}`;
                            // Update the single progress notification
                            if (progressHandle) {
                                const epText = (data.new_episodes_found || 0) > 0
                                    ? `${data.new_episodes_found} new ${(data.new_episodes_found === 1 ? 'episode' : 'episodes')} in ${data.current_show}`
                                    : `Checked ${data.completed_shows}/${data.total_shows}${eta}`;
                                progressHandle.update({ percent: percentage, text: epText });
                            }
                            break;
                            
                        case 'completed':
                            isCompleted = true;
                            eventSource.close();
                            
                            // Reset button state
                            updateForm.classList.remove('loading');
                            button.disabled = false;
                            spinner.style.display = 'none';
                            buttonText.innerHTML = '<i class="fas fa-sync-alt"></i>';
                            
                            // Finalize the single progress notification
                            if (progressHandle) {
                                const baseText = data.total_new_episodes > 0
                                    ? `Found ${data.total_new_episodes} new ${(data.total_new_episodes === 1 ? 'episode' : 'episodes')}`
                                    : 'All shows are up to date';
                                progressHandle.update({ percent: 100, text: `${baseText} • ${Math.round(data.elapsed_time)}s`, type: 'success', title: 'Update complete' });
                                // Dismiss update notification quickly (500ms instead of 2000ms)
                                progressHandle.dismiss(500);
                                
                                // Show separate brief notification about background downloads only if auto-downloads actually happened
                                if (data.total_auto_downloaded > 0) {
                                    setTimeout(() => {
                                        showNotification(`Auto-downloading ${data.total_auto_downloaded} ${data.total_auto_downloaded === 1 ? 'episode' : 'episodes'} in background...`, 'info');
                                    }, 600);
                                }
                            }
                            
                            // Reload page to show new episodes
                            if (data.total_new_episodes > 0) {
                                setTimeout(() => window.location.reload(), 2000);
                            }
                            
                            resolve();
                            break;
                            
                        case 'error':
                            eventSource.close();
                            throw new Error(data.message || 'Update failed');
                            
                        case 'final':
                            if (!isCompleted) {
                                isCompleted = true;
                                eventSource.close();
                                
                                // Reset button state
                                updateForm.classList.remove('loading');
                                button.disabled = false;
                                spinner.style.display = 'none';
                                buttonText.innerHTML = '<i class="fas fa-sync-alt"></i>';
                                
                                if (progressHandle) {
                                    const cancelled = data.status === 'cancelled';
                                    const baseText = cancelled
                                        ? 'Update was cancelled'
                                        : (data.total_new_episodes > 0
                                            ? `Found ${data.total_new_episodes} new episodes`
                                            : 'No new episodes found');
                                    progressHandle.update({ percent: 100, text: baseText, type: cancelled ? 'info' : 'success', title: cancelled ? 'Update cancelled' : 'Update complete' });
                                    progressHandle.dismiss(2000);
                                }
                                resolve();
                            }
                            break;
                            
                        case 'heartbeat':
                            // Keep connection alive, no action needed
                            break;
                            
                        default:
                            console.log('Unknown progress type:', data.type);
                    }
                } catch (error) {
                    console.error('Error parsing progress data:', error);
                }
            };

            eventSource.onerror = function(error) {
                console.error('EventSource error:', error);
                eventSource.close();
                
                if (!isCompleted) {
                    // Reset button state
                    updateForm.classList.remove('loading');
                    button.disabled = false;
                    spinner.style.display = 'none';
                    buttonText.innerHTML = '<i class="fas fa-sync-alt"></i>';
                    // Show error on the single progress notification if present
                    if (progressHandle) {
                        progressHandle.update({ type: 'error', title: 'Update error', text: 'Connection lost during update' });
                        progressHandle.dismiss(2500);
                    }
                    reject(new Error('Connection lost during update'));
                }
            };

            // Cleanup on page unload
            window.addEventListener('beforeunload', () => {
                eventSource.close();
            });
        });
    }

    // NOTE: Subscribe form and modal handlers are now global (in app-init.js)
    // No need to bind them here - they work on all pages

    updateForm?.addEventListener('submit', async (e) => {
        e.preventDefault();
        await handleAsyncUpdate();
    });

    // Initialize sorting functionality
    function initializeSorting() {
        if (!sortSelect || !showsList) return;

        // Load saved sort preference (default to newest episode)
        const savedSort = getSavedSortPreference();
        sortSelect.value = savedSort;
        applySorting(savedSort);

        // Listen for sort changes
        sortSelect.addEventListener('change', function() {
            const sortValue = this.value;
            localStorage.setItem(SORT_STORAGE_KEY, sortValue);
            applySorting(sortValue);
        });
    }

    function parseDateValue(value) {
        if (!value) return 0;
        // Try native parse first
        let d = new Date(value);
        if (!isNaN(d)) return d.getTime();
        // Try replacing space with T (e.g., "2024-06-10 12:34:56")
        d = new Date(String(value).replace(' ', 'T'));
        if (!isNaN(d)) return d.getTime();
        // Try numeric timestamp (ms or seconds)
        const n = Number(value);
        if (!Number.isNaN(n)) {
            return n > 1e12 ? n : n * 1000;
        }
        return 0;
    }

    function applySorting(sortValue) {
        if (!showsList) return;

        const showItems = Array.from(showsList.querySelectorAll('.show-item'));
        
        showItems.sort((a, b) => {
            switch (sortValue) {
                case 'name':
                    // A-Z alphabetical
                    return a.dataset.name.localeCompare(b.dataset.name);
                
                case 'updated':
                    // Most recent episode date first
                    return parseDateValue(b.dataset.latestEpisode) - parseDateValue(a.dataset.latestEpisode);
                
                case 'added':
                    // Most recently added shows first
                    return parseDateValue(b.dataset.added) - parseDateValue(a.dataset.added);
                
                default:
                    return 0;
            }
        });

        // Re-append sorted items
        showItems.forEach(item => showsList.appendChild(item));
        
        // Add visual feedback for sorting
        showsList.style.opacity = '0.7';
        setTimeout(() => {
            showsList.style.opacity = '1';
        }, 150);
    }

    function updateRelativeTimes() {
        const timeElements = document.querySelectorAll('.relative-time');
        
        timeElements.forEach(element => {
            const timestamp = element.dataset.timestamp;
            if (!timestamp) return;

            const relativeTime = getRelativeTime(timestamp);
            element.textContent = relativeTime;
        });
    }

    function getRelativeTime(timestamp) {
        const now = new Date();
        const past = new Date(timestamp);
        const diffMs = now - past;
        
        // Convert to different units
        const diffMinutes = Math.floor(diffMs / (1000 * 60));
        const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
        const diffWeeks = Math.floor(diffDays / 7);
        const diffMonths = Math.floor(diffDays / 30);
        const diffYears = Math.floor(diffDays / 365);

        if (diffMinutes < 1) {
            return 'Just now';
        } else if (diffMinutes < 60) {
            return `${diffMinutes}m ago`;
        } else if (diffHours < 24) {
            return `${diffHours}h ago`;
        } else if (diffDays < 7) {
            return `${diffDays}d ago`;
        } else if (diffWeeks < 4) {
            return `${diffWeeks}w ago`;
        } else if (diffMonths < 12) {
            return `${diffMonths}mo ago`;
        } else {
            return `${diffYears}y ago`;
        }
    }

    // Initialize show menu functionality
    function initializeShowMenus() {
        const showMenuTriggers = document.querySelectorAll('.show-menu-trigger');
        
        showMenuTriggers.forEach(trigger => {
            // Skip if already initialized
            if (trigger.__menuBound) return;
            trigger.__menuBound = true;
            
            const container = trigger.closest('.show-menu-container');
            if (!container) return;
            
            const dropdown = container.querySelector('.show-menu-dropdown');
            if (!dropdown) return;
            
            const menuItems = dropdown.querySelectorAll('.show-menu-item');
            
            // Toggle menu on click
            trigger.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                
                // Close other open menus
                document.querySelectorAll('.show-menu-container.active').forEach(activeContainer => {
                    if (activeContainer !== container) {
                        activeContainer.classList.remove('active');
                    }
                });
                
                // Toggle current menu
                container.classList.toggle('active');
            });
            
            // Handle menu item clicks
            menuItems.forEach(item => {
                if (item.__menuItemBound) return;
                item.__menuItemBound = true;
                
                item.addEventListener('click', async function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    
                    const action = this.dataset.action;
                    const showUrl = trigger.dataset.showUrl;
                    const autoDownloadStr = trigger.dataset.autoDownload;
                    let autoDownload = false;
                    try {
                        autoDownload = JSON.parse(autoDownloadStr);
                    } catch (_) {}
                    
                    // Close menu
                    container.classList.remove('active');
                    
                    // Handle different actions
                    switch (action) {
                        case 'update':
                            await handleUpdateShow(this, showUrl);
                            break;
                        case 'download-all':
                            await handleDownloadAll(this, showUrl);
                            break;
                        case 'toggle-auto-download':
                            await handleToggleAutoDownload(this, showUrl, autoDownload);
                            break;
                        case 'delete':
                            await handleDeleteShow(this, showUrl);
                            break;
                    }
                });
            });
        });
        
        // Close menu when clicking outside - only bind once
        if (!window.__showMenuGlobalBound) {
            window.__showMenuGlobalBound = true;
            
            document.addEventListener('click', function(e) {
                if (!e.target.closest('.show-menu-container')) {
                    document.querySelectorAll('.show-menu-container.active').forEach(container => {
                        container.classList.remove('active');
                    });
                }
            });
            
            // Close menu on escape key
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape') {
                    document.querySelectorAll('.show-menu-container.active').forEach(container => {
                        container.classList.remove('active');
                    });
                }
            });
        }
    }
    
    // Handle update show action
    async function handleUpdateShow(menuItem, showUrl) {
        const originalContent = menuItem.innerHTML;
        
        try {
            // Show loading state
            menuItem.classList.add('loading');
            menuItem.innerHTML = '<div class="loading-spinner"></div><span>Updating...</span>';
            
            const response = await fetch(`/update_show/${encodeURIComponent(showUrl)}`, {
                method: 'POST'
            });
            
            const data = await response.json();
            
            if (data.success) {
                const message = `Update complete: ${data.new_episodes} new episode${data.new_episodes !== 1 ? 's' : ''} found`;
                showNotification(message, 'success');
                
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
            // Reset loading state
            menuItem.classList.remove('loading');
            menuItem.innerHTML = originalContent;
        }
    }
    
    // Handle download all episodes action
    async function handleDownloadAll(menuItem, showUrl) {
        const originalContent = menuItem.innerHTML;
        
        try {
            // Show loading state
            menuItem.classList.add('loading');
            menuItem.innerHTML = '<div class="loading-spinner"></div><span>Starting download...</span>';
            
            const response = await fetch(`/download_all/${encodeURIComponent(showUrl)}`);
            
            if (!response.ok) {
                throw new Error('Failed to start download');
            }
            
            const data = await response.json();
            showNotification('Download started! Check the Downloads page for progress.', 'success');
            
            // Optionally redirect to downloads page or show page
            window.open(`/show/${encodeURIComponent(showUrl)}`, '_blank');
            
        } catch (error) {
            console.error('Error starting download:', error);
            showNotification(`Error: ${error.message}`, 'error');
        } finally {
            // Reset loading state
            menuItem.classList.remove('loading');
            menuItem.innerHTML = originalContent;
        }
    }
    
    // Handle toggle auto-download action
    async function handleToggleAutoDownload(menuItem, showUrl, currentStatus) {
        const originalContent = menuItem.innerHTML;
        const autoStatusSpan = menuItem.querySelector('.auto-status');
        
        try {
            // Show loading state
            menuItem.classList.add('loading');
            menuItem.innerHTML = '<div class="loading-spinner"></div><span>Toggling...</span>';
            
            const response = await fetch(`/toggle_auto_download/${encodeURIComponent(showUrl)}`, {
                method: 'POST'
            });
            
            const data = await response.json();
            
            if (data.success) {
                // Update the trigger data attribute
                const trigger = menuItem.closest('.show-menu-container').querySelector('.show-menu-trigger');
                trigger.dataset.autoDownload = JSON.stringify(data.auto_download);
                
                // Update the menu item status text
                autoStatusSpan.textContent = data.auto_download ? 'On' : 'Off';
                
                // Update auto-download badge in show stats if present
                const showItem = menuItem.closest('.show-item');
                const autoDownloadBadge = showItem.querySelector('.auto-download-badge');
                
                if (data.auto_download && !autoDownloadBadge) {
                    // Add auto-download badge
                    const showStats = showItem.querySelector('.show-stats');
                    const badge = document.createElement('span');
                    badge.className = 'auto-download-badge';
                    badge.title = 'Auto-download enabled';
                    badge.innerHTML = `
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
                        </svg>
                        <span>Auto</span>
                    `;
                    showStats.appendChild(badge);
                } else if (!data.auto_download && autoDownloadBadge) {
                    // Remove auto-download badge
                    autoDownloadBadge.remove();
                }
                
                const statusText = data.auto_download ? 'enabled' : 'disabled';
                showNotification(`Auto-download ${statusText}`, data.auto_download ? 'success' : 'info');
            } else {
                throw new Error(data.message || 'Failed to toggle auto-download');
            }
        } catch (error) {
            console.error('Error toggling auto-download:', error);
            showNotification(`Error: ${error.message}`, 'error');
        } finally {
            // Reset loading state
            menuItem.classList.remove('loading');
            menuItem.innerHTML = originalContent;
        }
    }
    
    // Handle delete show action
    async function handleDeleteShow(menuItem, showUrl) {
        const originalContent = menuItem.innerHTML;
        
        // Show confirmation dialog
        if (!confirm('Are you sure you want to delete this show? This will remove all episodes and cannot be undone.')) {
            return;
        }
        
        try {
            // Show loading state
            menuItem.classList.add('loading');
            menuItem.innerHTML = '<div class="loading-spinner"></div><span>Deleting...</span>';
            
            const response = await fetch(`/delete/${encodeURIComponent(showUrl)}`, {
                method: 'POST'
            });
            
            // The delete endpoint returns a redirect (302), which is considered successful
            if (response.ok || response.redirected) {
                showNotification('Show deleted successfully', 'success');
                
                // Remove the show item from the DOM with animation
                const showItem = menuItem.closest('.show-item');
                showItem.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
                showItem.style.opacity = '0';
                showItem.style.transform = 'translateX(-20px)';
                
                setTimeout(() => {
                    showItem.remove();
                }, 300);
            } else {
                throw new Error('Failed to delete show');
            }
        } catch (error) {
            console.error('Error deleting show:', error);
            showNotification(`Error: ${error.message}`, 'error');
            
            // Reset loading state on error
            menuItem.classList.remove('loading');
            menuItem.innerHTML = originalContent;
        }
    }
}

// Expose for SPA router
window.initIndexPageHandlers = initIndexPageHandlers;
window.NTSIndexPage = Object.assign(window.NTSIndexPage || {}, {
    getSavedSortPreference,
});

if (window.NTSPageModules && typeof window.NTSPageModules.register === 'function') {
    window.NTSPageModules.register('shows', {
        init: initIndexPageHandlers,
        cleanup() {},
    });
}
