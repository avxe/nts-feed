/**
 * Track Info Sidedrawer
 * Displays detailed information about tracks and artists
 */

class TrackInfoDrawer {
    constructor() {
        this.isVisible = false;
        this.currentTrack = null;
        this.currentArtist = null;
        this.isLoading = false;
        this.drawer = null;
        this.overlay = null;
        this.cache = {}; // Cache for API responses
        this.artistCache = {}; // Cache artist info by artist name to avoid refetching
        this.imagePreviewOverlay = null;
        this.isEditing = false;
        this.originalTrackData = null;
        this.loadingTimeouts = []; // Track loading timeouts for cleanup
        this.lastClickTime = 0;
        this.debounceDelay = 300; // 300ms debounce
        this.abortController = null; // For cancelling requests
        
        this.init();
    }
    
    init() {
        // Create sidedrawer elements
        this.createDrawerElements();
        
        // Add event listeners to track items
        this.addTrackItemListeners();
        
        // Add close event listeners
        this.addCloseListeners();
        
        // Create image preview overlay
        this.createImagePreviewOverlay();
        
        // Add a MutationObserver to watch for new track items
        this.setupTrackItemsObserver();
    }
    
    // Create a RateYourMusic-friendly slug from an artist name
    slugifyForRYM(name) {
        try {
            if (!name || typeof name !== 'string') return '';
            return name
                .toLowerCase()
                .normalize('NFD') // split accents from letters
                .replace(/[\u0300-\u036f]/g, '') // remove diacritics
                .replace(/&/g, ' and ') // replace ampersand with 'and'
                .replace(/['`’]/g, '') // remove apostrophes/quotes
                .replace(/[^a-z0-9]+/g, '-') // non-alphanumerics to hyphen
                .replace(/^-+|-+$/g, '') // trim hyphens
                .replace(/-{2,}/g, '-'); // collapse multiple hyphens
        } catch (_) {
            return '';
        }
    }
    
    // Sanitize artist bio HTML while allowing specific safe elements
    sanitizeBio(htmlString) {
        try {
            if (!htmlString || typeof htmlString !== 'string') {
                return 'No biography available.';
            }

            const allowedTags = new Set(['BR', 'A', 'P']);
            const container = document.createElement('div');
            container.innerHTML = htmlString;

            const sanitizeNode = (node) => {
                if (node.nodeType === Node.TEXT_NODE) {
                    return document.createTextNode(node.textContent);
                }

                if (node.nodeType === Node.ELEMENT_NODE) {
                    const tag = node.tagName.toUpperCase();
                    if (allowedTags.has(tag)) {
                        const el = document.createElement(tag.toLowerCase());
                        if (tag === 'A') {
                            const href = node.getAttribute('href') || '';
                            if (/^https?:\/\//i.test(href)) {
                                el.setAttribute('href', href);
                                el.setAttribute('target', '_blank');
                                el.setAttribute('rel', 'noopener noreferrer');
                            } else {
                                return document.createTextNode(node.textContent);
                            }
                        }

                        Array.from(node.childNodes).forEach((child) => {
                            const sanitizedChild = sanitizeNode(child);
                            if (sanitizedChild) el.appendChild(sanitizedChild);
                        });
                        return el;
                    }

                    const fragment = document.createDocumentFragment();
                    Array.from(node.childNodes).forEach((child) => {
                        const sanitizedChild = sanitizeNode(child);
                        if (sanitizedChild) fragment.appendChild(sanitizedChild);
                    });
                    return fragment;
                }

                return null;
            };

            const resultFragment = document.createDocumentFragment();
            Array.from(container.childNodes).forEach((child) => {
                const sanitizedChild = sanitizeNode(child);
                if (sanitizedChild) resultFragment.appendChild(sanitizedChild);
            });

            const resultContainer = document.createElement('div');
            resultContainer.appendChild(resultFragment);
            return resultContainer.innerHTML.trim();
        } catch (e) {
            console.warn('Error sanitizing bio HTML:', e);
            return 'No biography available.';
        }
    }
    
    createDrawerElements() {
        this.overlay = document.querySelector('.sidedrawer-overlay');
        if (!this.overlay) {
            this.overlay = document.createElement('div');
            this.overlay.className = 'sidedrawer-overlay';
            document.body.appendChild(this.overlay);
        }
        
        this.drawer = document.querySelector('.sidedrawer');
        if (!this.drawer) {
            this.drawer = document.createElement('div');
            this.drawer.className = 'sidedrawer';
            document.body.appendChild(this.drawer);
        }

        if (!this.drawer.querySelector('.sidedrawer-header')) {
            const header = document.createElement('div');
            header.className = 'sidedrawer-header';
            header.innerHTML = `
                <button class="sidedrawer-close">
                    <i class="fas fa-times"></i>
                </button>
            `;
            this.drawer.appendChild(header);
        }

        if (!this.drawer.querySelector('.sidedrawer-content')) {
            const content = document.createElement('div');
            content.className = 'sidedrawer-content';
            this.drawer.appendChild(content);
        }

        const content = this.drawer.querySelector('.sidedrawer-content');
        if (content && !content.innerHTML.trim()) {
            content.innerHTML = `
                <div class="loading-indicator">
                    <div class="loading-spinner">
                        <svg width="40" height="40" viewBox="0 0 24 24">
                            <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="4"></circle>
                        </svg>
                    </div>
                    <p>Loading information...</p>
                </div>
            `;
        }
    }
    
    createImagePreviewOverlay() {
        // Create image preview overlay
        this.imagePreviewOverlay = document.createElement('div');
        this.imagePreviewOverlay.className = 'image-preview-overlay';
        this.imagePreviewOverlay.innerHTML = `
            <div class="image-preview-container">
                <button class="image-preview-close">
                    <i class="fas fa-times"></i>
                </button>
                <img src="" alt="Preview" class="image-preview">
                <div class="image-preview-controls">
                    <button class="image-preview-prev">
                        <i class="fas fa-chevron-left"></i>
                    </button>
                    <span class="image-preview-counter">1 / 1</span>
                    <button class="image-preview-next">
                        <i class="fas fa-chevron-right"></i>
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(this.imagePreviewOverlay);
        
        // Add event listeners
        this.imagePreviewOverlay.querySelector('.image-preview-close').addEventListener('click', () => {
            this.hideImagePreview();
        });
        
        this.imagePreviewOverlay.querySelector('.image-preview-prev').addEventListener('click', () => {
            this.showPreviousImage();
        });
        
        this.imagePreviewOverlay.querySelector('.image-preview-next').addEventListener('click', () => {
            this.showNextImage();
        });
        
        // Close on overlay click
        this.imagePreviewOverlay.addEventListener('click', (e) => {
            if (e.target === this.imagePreviewOverlay) {
                this.hideImagePreview();
            }
        });
        
        // Close on escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.imagePreviewOverlay.classList.contains('visible')) {
                this.hideImagePreview();
            } else if (e.key === 'ArrowLeft' && this.imagePreviewOverlay.classList.contains('visible')) {
                this.showPreviousImage();
            } else if (e.key === 'ArrowRight' && this.imagePreviewOverlay.classList.contains('visible')) {
                this.showNextImage();
            }
        });
    }
    
    // Image preview functionality
    currentImageIndex = 0;
    currentImages = [];
    
    showImagePreview(images, index = 0) {
        this.currentImages = images;
        this.currentImageIndex = index;
        
        if (images.length === 0) return;
        
        const img = this.imagePreviewOverlay.querySelector('.image-preview');
        img.src = images[index].uri;
        img.alt = `Image ${index + 1}`;
        
        // Update counter
        this.imagePreviewOverlay.querySelector('.image-preview-counter').textContent = `${index + 1} / ${images.length}`;
        
        // Show overlay
        this.imagePreviewOverlay.classList.add('visible');
        document.body.style.overflow = 'hidden'; // Prevent scrolling
    }
    
    hideImagePreview() {
        this.imagePreviewOverlay.classList.remove('visible');
        if (this.isVisible) {
            // Keep body scroll locked if drawer is still open
            document.body.style.overflow = 'hidden';
        } else {
            document.body.style.overflow = ''; // Restore scrolling
        }
    }
    
    showNextImage() {
        if (this.currentImages.length <= 1) return;
        
        this.currentImageIndex = (this.currentImageIndex + 1) % this.currentImages.length;
        const img = this.imagePreviewOverlay.querySelector('.image-preview');
        img.src = this.currentImages[this.currentImageIndex].uri;
        
        // Update counter
        this.imagePreviewOverlay.querySelector('.image-preview-counter').textContent = 
            `${this.currentImageIndex + 1} / ${this.currentImages.length}`;
    }
    
    showPreviousImage() {
        if (this.currentImages.length <= 1) return;
        
        this.currentImageIndex = (this.currentImageIndex - 1 + this.currentImages.length) % this.currentImages.length;
        const img = this.imagePreviewOverlay.querySelector('.image-preview');
        img.src = this.currentImages[this.currentImageIndex].uri;
        
        // Update counter
        this.imagePreviewOverlay.querySelector('.image-preview-counter').textContent = 
            `${this.currentImageIndex + 1} / ${this.currentImages.length}`;
    }
    
    addTrackItemListeners() {
        console.log('Adding track item listeners');
        const trackItems = document.querySelectorAll('.track-item');
        console.log(`Found ${trackItems.length} track items`);
        
        trackItems.forEach(item => {
            // Skip if already has listener
            if (item.hasAttribute('data-has-sidedrawer-listener')) {
                return;
            }
            
            item.setAttribute('data-has-sidedrawer-listener', 'true');
            item.style.cursor = 'pointer';
            
            item.addEventListener('click', (e) => {
                // Don't trigger if clicking on buttons
                if (e.target.closest('.track-download-btn') || e.target.closest('.track-youtube-btn') || e.target.closest('.track-like-btn')) {
                    return;
                }
                // Don't trigger if clicking on timestamp
                if (e.target.closest('.track-timestamp')) {
                    return;
                }
                // Only open sidedrawer via explicit info button next to controls
                const infoBtn = e.target.closest('.track-info-btn');
                if (infoBtn) {
                    const artist = item.querySelector('.track-artist').textContent;
                    const title = item.querySelector('.track-title').textContent;
                    console.log(`Track info button: ${artist} - ${title}`);
                    this.showTrackInfo(artist, title);
                }
                // Otherwise, do nothing here (seek handled in show-page.js)
            });
        });
    }
    
    addCloseListeners() {
        // Close button
        this.drawer.querySelector('.sidedrawer-close').addEventListener('click', () => {
            this.hideDrawer();
        });
        
        // Overlay click
        this.overlay.addEventListener('click', () => {
            this.hideDrawer();
        });
        
        // Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.isVisible) {
                this.hideDrawer();
            }
        });
    }
    
    showTrackInfo(artist, title) {
        // Debounce rapid clicks
        const currentTime = Date.now();
        if (currentTime - this.lastClickTime < this.debounceDelay) {
            return;
        }
        this.lastClickTime = currentTime;
        
        // Cancel any ongoing requests
        if (this.abortController) {
            this.abortController.abort();
        }
        this.abortController = new AbortController();
        
        // Clear any pending timeouts
        this.loadingTimeouts.forEach(timeout => clearTimeout(timeout));
        this.loadingTimeouts = [];
        
        // If this is a new track, reset the drawer
        if (this.currentArtist !== artist || this.currentTrack !== title) {
            this.currentTrack = title;
            this.currentArtist = artist;
            
            // Show basic info immediately
            this.showBasicInfo(artist, title);
        }
        
        // Show drawer
        this.showDrawer();
        
        // Start progressive loading
        this.startProgressiveLoading(artist, title);
    }
    
    showBasicInfo(artist, title) {
        const content = this.drawer.querySelector('.sidedrawer-content');
        content.innerHTML = `
            <div class="track-info-section">
                <h3><i class="fas fa-music"></i> Track Information</h3>
                <div class="artist-header">
                    <div class="artist-image placeholder">
                        <i class="fas fa-user"></i>
                    </div>
                    <div class="artist-name-container">
                        <h2 class="artist-name">${artist}</h2>
                        <p class="artist-bio">Loading artist information...</p>
                        <div class="tags-container">
                            <h4>Artist Tags:</h4>
                            <div class="tags">
                                <div class="loading-tags">
                                    <span class="tag-skeleton"></span>
                                    <span class="tag-skeleton"></span>
                                    <span class="tag-skeleton"></span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="track-details">
                    <div class="album-cover-container">
                        <div class="album-cover placeholder">
                            <i class="fas fa-music"></i>
                        </div>
                    </div>
                    
                    <div class="track-metadata">
                        <div class="metadata-row">
                            <div class="metadata-label">Track</div>
                            <div class="metadata-value">${title}</div>
                        </div>
                        
                        <div class="metadata-row">
                            <div class="metadata-label">Artist</div>
                            <div class="metadata-value">${artist}</div>
                        </div>
                        
                        <div class="metadata-row">
                            <div class="metadata-label">Album</div>
                            <div class="metadata-value">Loading...</div>
                        </div>
                    </div>
                </div>
                
                <div class="track-links">
                    <h4>More info:</h4>
                    <div class="link-buttons">
                        <a href="https://www.youtube.com/results?search_query=${encodeURIComponent(artist + ' ' + title)}" target="_blank" class="link-button youtube">
                            <i class="fab fa-youtube"></i> YouTube
                        </a>
                        <a href="https://www.last.fm/music/${encodeURIComponent(artist)}/_/${encodeURIComponent(title)}" target="_blank" class="link-button lastfm">
                            <i class="fab fa-lastfm"></i> Last.fm
                        </a>
                        <a href="https://bandcamp.com/search?q=${encodeURIComponent(artist)}" target="_blank" class="link-button bandcamp">
                            <i class="fab fa-bandcamp"></i> Bandcamp
                        </a>
                        <a href="https://www.discogs.com/search/?q=${encodeURIComponent(artist + ' ' + title)}&type=release" target="_blank" class="link-button discogs">
                            <i class="fas fa-compact-disc"></i> Discogs
                        </a>
                        <a href="https://rateyourmusic.com/artist/${this.slugifyForRYM(artist)}" target="_blank" class="link-button rym">
                            <i class="fas fa-star"></i> RateYourMusic
                        </a>
                        <a href="https://en.wikipedia.org/wiki/${encodeURIComponent(artist)}" target="_blank" class="link-button wikipedia">
                            <i class="fab fa-wikipedia-w"></i> Wikipedia
                        </a>
                    </div>
                </div>
            </div>
            
            <div class="similar-artists-section">
                <h3><i class="fas fa-users"></i> Similar Artists</h3>
                <div class="similar-artists-loading">
                    <div class="loading-indicator">
                        <div class="loading-spinner">
                            <svg width="20" height="20" viewBox="0 0 24 24">
                                <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="4"></circle>
                            </svg>
                        </div>
                        <p>Loading similar artists...</p>
                    </div>
                </div>
            </div>
        `;
    }
    
    startProgressiveLoading(artist, title) {
        // Stage 1: Load artist and track info in parallel (immediate)
        this.loadStage1(artist, title);
        
        // Stage 2: Load similar artists (after a short delay)
        const timeout = setTimeout(() => {
            this.loadStage2(artist, title);
        }, 500);
        this.loadingTimeouts.push(timeout);
    }
    
    async loadStage1(artist, title) {
        // Check caches first
        const cacheKey = `${artist}:${title}`;
        const cachedArtistInfo = this.artistCache[artist];
        const cachedTrackData = this.cache[cacheKey];
        if (cachedArtistInfo && cachedTrackData) {
            console.log('Using cached artist and track data for Stage 1:', artist, title);
            this.updateWithStage1Data(cachedArtistInfo, cachedTrackData.trackInfo);
            return;
        }
        
        try {
            // Fetch artist info and track info in parallel, skipping artist if cached
            const artistInfoPromise = cachedArtistInfo
                ? Promise.resolve(new Response(new Blob([JSON.stringify({artist: cachedArtistInfo})], {type: 'application/json'}), {status: 200, statusText: 'OK'}))
                : fetch(`/api/lastfm/artist_info?name=${encodeURIComponent(artist)}`, { signal: this.abortController.signal });
            const trackInfoPromise = fetch(`/api/lastfm/track_info?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}`, { signal: this.abortController.signal });
            const [artistInfoResponse, trackInfoResponse] = await Promise.all([artistInfoPromise, trackInfoPromise]);
            
            const ct1 = (artistInfoResponse.headers.get('content-type') || '');
            const ct2 = (trackInfoResponse.headers.get('content-type') || '');
            const [artistInfoData, trackInfoData] = await Promise.all([
                ct1.includes('application/json') ? artistInfoResponse.json() : Promise.resolve({}),
                ct2.includes('application/json') ? trackInfoResponse.json() : Promise.resolve({})
            ]);
            
            // Combine the data
            const artistInfo = artistInfoData.artist || cachedArtistInfo || this.getFallbackArtistInfo(artist);
            const trackInfo = trackInfoData.track || this.getFallbackTrackInfo(artist, title);
            
            // Update UI if this is still the current track
            if (this.currentArtist === artist && this.currentTrack === title) {
                this.updateWithStage1Data(artistInfo, trackInfo);
                
                // Cache artist and track results
                this.artistCache[artist] = {...artistInfo};
                this.cache[cacheKey] = { artistInfo: {...artistInfo, similar_artists: artistInfo.similar_artists || []}, trackInfo };
            }
        } catch (error) {
            if (error.name === 'AbortError') {
                console.log('Stage 1 request was cancelled');
                return;
            }
            console.error('Error in Stage 1 loading:', error);
            this.showError('Failed to load basic information. Please try again.');
        }
    }
    
    async loadStage2(artist, title) {
        // Skip if user has navigated away
        if (this.currentArtist !== artist || this.currentTrack !== title) {
            return;
        }
        
        try {
            // If we already have similar artists from cached artist info, use them and skip fetch
            const cachedArtist = this.artistCache[artist];
            if (cachedArtist && Array.isArray(cachedArtist.similar_artists) && cachedArtist.similar_artists.length > 0) {
                this.updateWithSimilarArtists(cachedArtist.similar_artists);
                return;
            }

            // Load similar artists if not already available
            const response = await fetch(`/api/lastfm/similar_artists?name=${encodeURIComponent(artist)}`, { signal: this.abortController.signal });
            const isJson = (response.headers.get('content-type') || '').includes('application/json');
            const data = isJson ? await response.json() : {};
            
            // Update UI if this is still the current track
            if (this.currentArtist === artist && this.currentTrack === title) {
                this.updateWithSimilarArtists(data.similar_artists || []);
                
                // Update caches with similar artists
                const cacheKey = `${artist}:${title}`;
                if (this.cache[cacheKey]) {
                    this.cache[cacheKey].artistInfo.similar_artists = data.similar_artists || [];
                }
                if (this.artistCache[artist]) {
                    this.artistCache[artist].similar_artists = data.similar_artists || [];
                }
            }
        } catch (error) {
            if (error.name === 'AbortError') {
                console.log('Stage 2 request was cancelled');
                return;
            }
            console.error('Error in Stage 2 loading:', error);
            // Don't show error for similar artists - just leave the loading indicator
        }
    }
    
    updateWithStage1Data(artistInfo, trackInfo) {
        // Update artist image
        const artistImage = this.drawer.querySelector('.artist-image');
        if (artistInfo.image && artistImage) {
            artistImage.innerHTML = `<img src="${artistInfo.image}" alt="${artistInfo.name}" class="clickable-image" data-image-type="artist">`;
            artistImage.classList.remove('placeholder');
        }
        
        // Update artist bio
        const artistBio = this.drawer.querySelector('.artist-bio');
        if (artistBio) {
            const bioHTML = this.sanitizeBio(artistInfo.bio_summary || artistInfo.bio);
            artistBio.innerHTML = bioHTML;
        }
        
        // Update artist tags
        const tagsContainer = this.drawer.querySelector('.tags');
        if (tagsContainer && artistInfo.tags && artistInfo.tags.length > 0) {
            const artistTags = artistInfo.tags.map(tag => `<span class="tag">${tag}</span>`).join('');
            tagsContainer.innerHTML = artistTags;
        } else if (tagsContainer) {
            tagsContainer.innerHTML = '<span class="tag">No tags available</span>';
        }
        
        // Update album cover
        const albumCover = this.drawer.querySelector('.album-cover');
        if (trackInfo.album && trackInfo.album.image && albumCover) {
            albumCover.innerHTML = `<img src="${trackInfo.album.image}" alt="${trackInfo.album.title}" class="clickable-image" data-image-type="album">`;
            albumCover.classList.remove('placeholder');
        }
        
        // Update metadata
        const metadataRows = this.drawer.querySelectorAll('.metadata-row');
        metadataRows.forEach(row => {
            const label = row.querySelector('.metadata-label').textContent;
            const valueEl = row.querySelector('.metadata-value');
            
            switch (label) {
                case 'Album':
                    valueEl.textContent = trackInfo.album?.title || 'Unknown';
                    break;
                case 'Year':
                    if (trackInfo.album?.year) {
                        valueEl.textContent = trackInfo.album.year;
                    }
                    break;
                case 'Label':
                    if (trackInfo.album?.label) {
                        valueEl.textContent = trackInfo.album.label;
                    }
                    break;
            }
        });
        
        // Add new metadata rows if they don't exist
        const trackMetadata = this.drawer.querySelector('.track-metadata');
        if (trackMetadata) {
            // Add year if not present
            if (!this.drawer.querySelector('.metadata-row .metadata-label[textContent="Year"]') && trackInfo.album?.year) {
                const yearRow = document.createElement('div');
                yearRow.className = 'metadata-row';
                yearRow.innerHTML = `
                    <div class="metadata-label">Year</div>
                    <div class="metadata-value">${trackInfo.album.year}</div>
                `;
                trackMetadata.appendChild(yearRow);
            }
            
            // Add label if not present
            if (!this.drawer.querySelector('.metadata-row .metadata-label[textContent="Label"]') && trackInfo.album?.label) {
                const labelRow = document.createElement('div');
                labelRow.className = 'metadata-row';
                labelRow.innerHTML = `
                    <div class="metadata-label">Label</div>
                    <div class="metadata-value">${trackInfo.album.label}</div>
                `;
                trackMetadata.appendChild(labelRow);
            }
        }
        
        // Store for image click listeners
        this.currentArtistInfo = artistInfo;
        this.currentTrackInfo = trackInfo;
        
        // Add image click listeners
        this.addImageClickListeners();
    }
    
    updateWithSimilarArtists(similarArtists) {
        const similarSection = this.drawer.querySelector('.similar-artists-section');
        if (!similarSection) return;
        
        // Replace loading indicator with artists grid
        const loadingDiv = similarSection.querySelector('.similar-artists-loading');
        if (loadingDiv) {
            loadingDiv.remove();
        }
        
        const artistsGrid = document.createElement('div');
        artistsGrid.className = 'similar-artists-grid';
        artistsGrid.innerHTML = this.renderSimilarArtists(similarArtists);
        
        similarSection.appendChild(artistsGrid);
        
        // Update cached data
        if (this.currentArtistInfo) {
            this.currentArtistInfo.similar_artists = similarArtists;
        }
    }
    
    async fetchTrackInfo(artist, title) {
        // Set loading state
        this.setLoading(true);
        
        // Check cache first
        const cacheKey = `${artist}:${title}`;
        if (this.cache[cacheKey]) {
            console.log('Using cached data for', artist, title);
            const cachedData = this.cache[cacheKey];
            this.renderTrackInfo(cachedData.artistInfo, cachedData.trackInfo);
            this.setLoading(false);
            return;
        }
        
        try {
            // Fetch artist info from Last.fm
            const artistInfoResponse = await fetch(`/api/lastfm/artist_info?name=${encodeURIComponent(artist)}`);
            const artistInfoData = await artistInfoResponse.json();
            
            // Fetch track info from Last.fm
            const trackInfoResponse = await fetch(`/api/lastfm/track_info?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}`);
            const trackInfoData = await trackInfoResponse.json();
            
            // Combine the data
            const artistInfo = artistInfoData.artist || this.getFallbackArtistInfo(artist);
            const trackInfo = trackInfoData.track || this.getFallbackTrackInfo(artist, title);
            
            // Cache the results
            this.cache[cacheKey] = { artistInfo, trackInfo };
            
            // Render content if this is still the current track
            if (this.currentArtist === artist && this.currentTrack === title) {
                this.renderTrackInfo(artistInfo, trackInfo);
                this.setLoading(false);
            }
        } catch (error) {
            console.error('Error fetching track info:', error);
            this.showError('Failed to load information. Please try again.');
            this.setLoading(false);
        }
    }
    
    async fetchArtistInfo(artist) {
        try {
            console.log('Fetching artist info for:', artist);
            
            // Try to fetch from Last.fm API first
            try {
                console.log('Fetching artist info from Last.fm for:', artist);
                const lastfmResponse = await fetch(`/api/lastfm/artist_info?name=${encodeURIComponent(artist)}`);
                
                if (lastfmResponse.ok) {
                    const lastfmData = await lastfmResponse.json();
                    
                    if (lastfmData.success && lastfmData.artist) {
                        console.log('Last.fm artist info found:', lastfmData.artist);
                        
                        // If we already have similar artists from the artist info, use those
                        if (lastfmData.artist.similar && lastfmData.artist.similar.length > 0) {
                            console.log('Using similar artists from artist info');
                            lastfmData.artist.similarArtists = lastfmData.artist.similar;
                        }
                        
                        return lastfmData.artist;
                    }
                }
            } catch (lastfmError) {
                console.warn('Error fetching artist info from Last.fm:', lastfmError);
            }
            
            // Fallback to backend API if Last.fm fails
            const response = await fetch(`/api/artist_info?name=${encodeURIComponent(artist)}`);
            
            if (response.ok) {
                const data = await response.json();
                console.log('Artist info response from backend:', data);
                
                // Fetch similar artists from Last.fm
                try {
                    console.log('Fetching similar artists from Last.fm for:', artist);
                    const lastfmResponse = await fetch(`/api/lastfm/similar_artists?name=${encodeURIComponent(artist)}`);
                    console.log('Last.fm response status:', lastfmResponse.status);
                    
                    if (lastfmResponse.ok) {
                        const lastfmData = await lastfmResponse.json();
                        
                        if (lastfmData.success && lastfmData.similar_artists && lastfmData.similar_artists.length > 0) {
                            console.log('Similar artists found:', lastfmData.similar_artists);
                            data.similarArtists = lastfmData.similar_artists.map(artist => ({
                                name: artist.name,
                                image: artist.image || null,
                                genres: artist.genres || [],
                                url: artist.url || null,
                                match: artist.match || null
                            }));
                        } else {
                            console.log('No similar artists found in Last.fm response');
                            // Check if it's a "no results" response
                            if (lastfmData.no_results) {
                                data.noSimilarArtistsFound = true;
                                data.noResultsMessage = lastfmData.message || 'No similar artists found for this artist.';
                            }
                        }
                    } else {
                        const errorData = await lastfmResponse.json();
                        console.log('Last.fm error response:', errorData);
                        data.lastfmError = true;
                        data.lastfmErrorMessage = errorData.message || 'Error fetching similar artists from Last.fm';
                    }
                } catch (lastfmError) {
                    console.warn('Error fetching Last.fm similar artists:', lastfmError);
                    data.lastfmError = true;
                    data.lastfmErrorMessage = 'Error connecting to Last.fm API';
                }
                
                return data;
            }
            
            // Fallback to simulated data if API fails
            console.warn('API request failed, using fallback data');
            return this.getFallbackArtistInfo(artist);
        } catch (error) {
            console.error('Error fetching artist info:', error);
            return this.getFallbackArtistInfo(artist);
        }
    }
    
    async fetchTrackDetails(artist, title) {
        try {
            // Try to fetch from Last.fm API first
            try {
                console.log('Fetching track info from Last.fm for:', artist, title);
                const lastfmResponse = await fetch(`/api/lastfm/track_info?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}`);
                
                if (lastfmResponse.ok) {
                    const lastfmData = await lastfmResponse.json();
                    
                    if (lastfmData.success && lastfmData.track) {
                        console.log('Last.fm track info found:', lastfmData.track);
                        return lastfmData.track;
                    }
                }
            } catch (lastfmError) {
                console.warn('Error fetching track info from Last.fm:', lastfmError);
            }
            
            // Fallback to backend API if Last.fm fails
            const response = await fetch(`/api/track_info?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}`);
            
            if (response.ok) {
                const data = await response.json();
                return data;
            }
            
            // Fallback to simulated data if API fails
            console.warn('API request failed, using fallback data');
            return this.getFallbackTrackInfo(artist, title);
        } catch (error) {
            console.error('Error fetching track details:', error);
            return this.getFallbackTrackInfo(artist, title);
        }
    }
    
    // Fallback methods for when API is not available
    getFallbackArtistInfo(artist) {
        return {
            name: artist,
            image: null,
            images: [],
            bio: `${artist} is a musical artist. Click the external links below to learn more about them.`,
            genres: this.generateRandomGenres(),
            similarArtists: this.generateSimilarArtists(),
            links: {
                discogs: `https://www.discogs.com/search/?q=${encodeURIComponent(artist)}&type=artist`,
                youtube: `https://www.youtube.com/results?search_query=${encodeURIComponent(artist)}`
            }
        };
    }
    
    getFallbackTrackInfo(artist, title) {
        return {
            title: title,
            artist: artist,
            album: {
                title: 'Unknown Album',
                year: 'Unknown',
                cover: null,
                images: [],
                label: 'Unknown Label',
                genres: [],
                styles: []
            },
            releaseDate: 'Unknown',
            duration: 'Unknown',
            links: {
                youtube: `https://www.youtube.com/results?search_query=${encodeURIComponent(artist + ' ' + title)}`,
                discogs: `https://www.discogs.com/search/?q=${encodeURIComponent(artist + ' ' + title)}&type=release`
            }
        };
    }
    
    renderTrackInfo(artistInfo, trackInfo) {
        const content = this.drawer.querySelector('.sidedrawer-content');
        
        // Format duration if it's in milliseconds
        let formattedDuration = 'Unknown';
        if (trackInfo.duration && typeof trackInfo.duration === 'number') {
            const minutes = Math.floor(trackInfo.duration / 60000);
            const seconds = Math.floor((trackInfo.duration % 60000) / 1000);
            formattedDuration = `${minutes}:${seconds.toString().padStart(2, '0')}`;
        }
        
        // Format tags
        const artistTags = artistInfo.tags && artistInfo.tags.length > 0 
            ? artistInfo.tags.map(tag => `<span class="tag">${tag}</span>`).join('') 
            : '<span class="tag">No tags available</span>';
        
        const trackTags = trackInfo.tags && trackInfo.tags.length > 0 
            ? trackInfo.tags.map(tag => `<span class="tag">${tag}</span>`).join('') 
            : '<span class="tag">No tags available</span>';
        
        const bioHtml = this.sanitizeBio(artistInfo.bio_summary || artistInfo.bio);
        content.innerHTML = `
            <div class="track-info-section">
                <h3><i class="fas fa-music"></i> Track Information</h3>
                <div class="artist-header">
                    ${artistInfo.image ? 
                        `<img src="${artistInfo.image}" alt="${artistInfo.name}" class="artist-image clickable-image" data-image-type="artist">` : 
                        `<div class="artist-image placeholder"><i class="fas fa-user"></i></div>`
                    }
                    <div class="artist-name-container">
                        <h2 class="artist-name">${artistInfo.name}</h2>
                        <p class="artist-bio">${bioHtml}</p>
                        <div class="tags-container">
                            <h4>Artist Tags:</h4>
                            <div class="tags">${artistTags}</div>
                        </div>
                    </div>
                </div>
                
                <div class="track-details">
                    <button class="track-details-edit-button" title="Edit track details">
                        <i class="fas fa-pencil-alt"></i>
                    </button>
                    
                    ${trackInfo.album && trackInfo.album.image ? 
                        `<div class="album-cover-container">
                            <img src="${trackInfo.album.image}" alt="${trackInfo.album.title}" class="album-cover clickable-image" data-image-type="album">
                         </div>` : 
                        `<div class="album-cover-container">
                            <div class="album-cover placeholder"><i class="fas fa-music"></i></div>
                         </div>`
                    }
                    
                    <div class="track-metadata">
                        <div class="metadata-row editable">
                            <div class="metadata-label">Track</div>
                            <div class="metadata-value editable" data-field="track">${trackInfo.name || trackInfo.title}</div>
                        </div>
                        
                        <div class="metadata-row editable">
                            <div class="metadata-label">Artist</div>
                            <div class="metadata-value editable" data-field="artist">${trackInfo.artist.name || trackInfo.artist}</div>
                        </div>
                        
                        <div class="metadata-row editable">
                            <div class="metadata-label">Album</div>
                            <div class="metadata-value editable" data-field="album">${trackInfo.album.title || 'Unknown'}</div>
                        </div>
                        
                        <div class="metadata-row editable">
                            <div class="metadata-label">Year</div>
                            <div class="metadata-value editable" data-field="year">${trackInfo.album.year || 'Unknown'}</div>
                        </div>
                        
                        <div class="metadata-row editable">
                            <div class="metadata-label">Label</div>
                            <div class="metadata-value editable" data-field="label" data-original-value="${trackInfo.album.label || 'Unknown'}">${trackInfo.album.label || 'Unknown'}</div>
                        </div>
                        
                        ${trackInfo.album.catno ? 
                            `<div class="metadata-row editable">
                                <div class="metadata-label">Catalog #</div>
                                <div class="metadata-value editable" data-field="catno">${trackInfo.album.catno}</div>
                             </div>` : 
                            ''
                        }
                        
                        ${formattedDuration !== 'Unknown' ? 
                            `<div class="metadata-row">
                                <div class="metadata-label">Duration</div>
                                <div class="metadata-value">${formattedDuration}</div>
                             </div>` : 
                            ''
                        }
                        
                        ${trackInfo.position ? 
                            `<div class="metadata-row">
                                <div class="metadata-label">Position</div>
                                <div class="metadata-value">${trackInfo.position}</div>
                             </div>` : 
                            ''
                        }
                        
                        <div class="track-details-edit-actions">
                            <button class="track-details-cancel-button">Cancel</button>
                            <button class="track-details-save-button">Save</button>
                        </div>
                    </div>
                </div>
                
                <div class="tags-container">
                    <h4>Track Tags:</h4>
                    <div class="tags">${trackTags}</div>
                </div>
                
                <div class="track-links">
                    <h4>More info:</h4>
                    <div class="link-buttons">
                        <a href="https://www.youtube.com/results?search_query=${encodeURIComponent(artistInfo.name + ' ' + (trackInfo.name || trackInfo.title))}" target="_blank" class="link-button youtube">
                            <i class="fab fa-youtube"></i> YouTube
                        </a>
                        <a href="${trackInfo.url || `https://www.last.fm/music/${encodeURIComponent(artistInfo.name)}/_/${encodeURIComponent(trackInfo.name || trackInfo.title)}`}" target="_blank" class="link-button lastfm">
                            <i class="fab fa-lastfm"></i> Last.fm
                        </a>
                        <a href="https://bandcamp.com/search?q=${encodeURIComponent(artistInfo.name)}" target="_blank" class="link-button bandcamp">
                            <i class="fab fa-bandcamp"></i> Bandcamp
                        </a>
                        <a href="https://www.discogs.com/search/?q=${encodeURIComponent(artistInfo.name + ' ' + (trackInfo.name || trackInfo.title))}&type=release" target="_blank" class="link-button discogs">
                            <i class="fas fa-compact-disc"></i> Discogs
                        </a>
                        <a href="https://rateyourmusic.com/artist/${this.slugifyForRYM(artistInfo.name)}" target="_blank" class="link-button rym">
                            <i class="fas fa-star"></i> RateYourMusic
                        </a>
                        <a href="${artistInfo.wikipedia_url || `https://en.wikipedia.org/wiki/${encodeURIComponent(artistInfo.name)}`}" target="_blank" class="link-button wikipedia">
                            <i class="fab fa-wikipedia-w"></i> Wikipedia
                        </a>
                    </div>
                </div>
            </div>
            
            <div class="similar-artists-section">
                <h3><i class="fas fa-users"></i> Similar Artists</h3>
                <div class="similar-artists-grid">
                    ${this.renderSimilarArtists(artistInfo.similar_artists || [])}
                </div>
            </div>
        `;
        
        // Store the current artist and track info for image preview
        this.currentArtistInfo = artistInfo;
        this.currentTrackInfo = trackInfo;
        
        // Add event listeners for clickable images
        this.addImageClickListeners();
        
        // Add event listeners for edit functionality
        this.addEditListeners();
    }
    
    renderSimilarArtists(similarArtists) {
        if (!similarArtists || similarArtists.length === 0) {
            return '<div class="no-similar-artists">No similar artists found</div>';
        }
        
        return similarArtists.map(artist => {
            // Format match percentage
            const matchPercentage = artist.match ? Math.round(artist.match) : 0;
            
            // Get top tags (up to 2)
            const tags = artist.tags && artist.tags.length > 0 
                ? artist.tags.slice(0, 2).join(', ') 
                : 'No tags';
            
            return `
                <div class="similar-artist-item">
                    <a href="${artist.url}" target="_blank" class="similar-artist-link">
                        <div class="similar-artist-image-container">
                            ${artist.image 
                                ? `<img src="${artist.image}" alt="${artist.name}" class="similar-artist-image">` 
                                : `<div class="similar-artist-image placeholder"><i class="fas fa-user"></i></div>`
                            }
                            <div class="match-percentage">${matchPercentage}% match</div>
                        </div>
                        <div class="similar-artist-info">
                            <div class="similar-artist-name">${artist.name}</div>
                            <div class="similar-artist-genres">${tags}</div>
                        </div>
                    </a>
                </div>
            `;
        }).join('');
    }
    
    addImageClickListeners() {
        // Artist images
        const artistImages = this.drawer.querySelectorAll('.clickable-image[data-image-type="artist"], .gallery-main-image');
        artistImages.forEach(img => {
            img.addEventListener('click', () => {
                // Create an array with the artist image for preview
                if (this.currentArtistInfo && this.currentArtistInfo.image) {
                    const images = this.currentArtistInfo.images && this.currentArtistInfo.images.length > 0 ? 
                        this.currentArtistInfo.images : [{ uri: this.currentArtistInfo.image }];
                    this.showImagePreview(images, 0);
                }
            });
        });
        
        // Album images
        const albumImages = this.drawer.querySelectorAll('.clickable-image[data-image-type="album"]');
        albumImages.forEach(img => {
            img.addEventListener('click', () => {
                // Create an array with the album cover for preview
                if (this.currentTrackInfo && this.currentTrackInfo.album && this.currentTrackInfo.album.image) {
                    const images = this.currentTrackInfo.album.images && this.currentTrackInfo.album.images.length > 0 ?
                        this.currentTrackInfo.album.images.map(img => ({ uri: img })) : 
                        [{ uri: this.currentTrackInfo.album.image }];
                    this.showImagePreview(images, 0);
                }
            });
        });
        
        // Artist gallery thumbnails
        const galleryThumbnails = this.drawer.querySelectorAll('.clickable-image[data-image-type="artist-gallery"]');
        galleryThumbnails.forEach(img => {
            img.addEventListener('click', () => {
                const imageIndex = parseInt(img.getAttribute('data-image-index'));
                if (!isNaN(imageIndex) && this.currentArtistInfo && this.currentArtistInfo.images && this.currentArtistInfo.images.length > imageIndex) {
                    this.showImagePreview(this.currentArtistInfo.images, imageIndex);
                }
            });
        });
    }
    
    showDrawer() {
        this.isVisible = true;
        this.drawer.classList.add('visible');
        this.overlay.classList.add('visible');
        document.body.style.overflow = 'hidden'; // Prevent scrolling
    }
    
    hideDrawer() {
        this.isVisible = false;
        this.drawer.classList.remove('visible');
        this.overlay.classList.remove('visible');
        document.body.style.overflow = ''; // Restore scrolling
    }
    
    setLoading(isLoading) {
        this.isLoading = isLoading;
        
        const loadingIndicator = this.drawer.querySelector('.loading-indicator');
        const content = this.drawer.querySelector('.sidedrawer-content');
        
        if (isLoading) {
            // Show loading indicator
            if (loadingIndicator) {
                loadingIndicator.style.display = 'flex';
            }
        } else {
            // Hide loading indicator
            if (loadingIndicator) {
                loadingIndicator.style.display = 'none';
            }
        }
    }
    
    showError(message) {
        const content = this.drawer.querySelector('.sidedrawer-content');
        content.innerHTML = `
            <div class="error-message">
                <i class="fas fa-exclamation-circle"></i>
                <p>${message}</p>
            </div>
        `;
    }
    
    // Helper methods for demo purposes
    generateRandomGenres() {
        const allGenres = [
            'Electronic', 'House', 'Techno', 'Ambient', 'Experimental',
            'Jazz', 'Hip-Hop', 'R&B', 'Soul', 'Funk', 'Disco', 'Pop',
            'Rock', 'Alternative', 'Classical', 'Minimal', 'Downtempo'
        ];
        
        // Get 2-4 random genres
        const count = Math.floor(Math.random() * 3) + 2;
        const genres = [];
        
        for (let i = 0; i < count; i++) {
            const randomIndex = Math.floor(Math.random() * allGenres.length);
            const genre = allGenres[randomIndex];
            
            if (!genres.includes(genre)) {
                genres.push(genre);
            }
        }
        
        return genres;
    }
    
    generateSimilarArtists() {
        const similarNames = [
            'Four Tet', 'Floating Points', 'Aphex Twin', 'Boards of Canada',
            'Burial', 'Jamie xx', 'Bonobo', 'Jon Hopkins', 'Caribou',
            'Nicolas Jaar', 'Thom Yorke', 'Autechre', 'Squarepusher'
        ];
        
        // Get 3-5 random artists
        const count = Math.floor(Math.random() * 3) + 3;
        const artists = [];
        
        for (let i = 0; i < count; i++) {
            const randomIndex = Math.floor(Math.random() * similarNames.length);
            const name = similarNames[randomIndex];
            
            if (!artists.some(a => a.name === name)) {
                artists.push({
                    name,
                    image: null
                });
            }
        }
        
        return artists;
    }
    
    setupTrackItemsObserver() {
        // Create a MutationObserver to watch for changes to the DOM
        const observer = new MutationObserver((mutations) => {
            let shouldRefresh = false;
            
            // Check if any tracks were added
            mutations.forEach(mutation => {
                if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
                    // Check if any of the added nodes are track items or contain track items
                    mutation.addedNodes.forEach(node => {
                        if (node.nodeType === 1) { // Element node
                            if (node.classList && node.classList.contains('track-item')) {
                                shouldRefresh = true;
                            } else if (node.querySelector && node.querySelector('.track-item')) {
                                shouldRefresh = true;
                            }
                        }
                    });
                }
            });
            
            // Refresh track item listeners if needed
            if (shouldRefresh) {
                this.refreshTrackItemListeners();
            }
        });
        
        // Start observing the document body for changes
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }
    
    refreshTrackItemListeners() {
        console.log('Refreshing track item listeners');
        this.addTrackItemListeners();
    }
    
    // Updated method to generate recommendations from Discogs
    generateRecommendations(artistInfo, trackInfo) {
        // Check if we have recommendations from the API first
        if (trackInfo.album && trackInfo.album.recommendations && trackInfo.album.recommendations.length > 0) {
            // Use the recommendations from the API, ensuring links are properly formatted
            return trackInfo.album.recommendations.map(rec => {
                // Ensure we have proper links based on IDs when available
                let link = rec.link; // Use existing link if provided
                
                // If we have an artist_id but no link, create a direct artist link
                if (rec.artist_id && !link) {
                    link = `https://www.discogs.com/artist/${rec.artist_id}`;
                } 
                // If we have a release_id but no link, create a direct release link
                else if (rec.release_id && !link) {
                    link = `https://www.discogs.com/release/${rec.release_id}`;
                }
                // If we have a master_id but no link, create a direct master link
                else if (rec.master_id && !link) {
                    link = `https://www.discogs.com/master/${rec.master_id}`;
                }
                // If we have a label_id but no link, create a direct label link
                else if (rec.label_id && !link) {
                    link = `https://www.discogs.com/label/${rec.label_id}`;
                }
                // If we have a genre but no link, create a genre link
                else if (rec.genre && !link) {
                    link = `https://www.discogs.com/genre/${encodeURIComponent(rec.genre)}`;
                }
                // Fallback to a search link if we have nothing else
                else if (!link) {
                    link = `https://www.discogs.com/search/?q=${encodeURIComponent((rec.artist || '') + ' ' + (rec.title || ''))}&type=release`;
                }
                
                return {
                    ...rec,
                    link: link
                };
            });
        }
        
        // Otherwise, generate recommendations based on artist and genre
        const recommendations = [];
        
        // Get similar artists with different names
        if (artistInfo.similarArtists && artistInfo.similarArtists.length > 0) {
            const filteredArtists = artistInfo.similarArtists
                .filter(a => a.name !== artistInfo.name)
                .slice(0, 3);
            
            filteredArtists.forEach(artist => {
                // Create direct artist link if ID is available
                const artistLink = artist.id 
                    ? `https://www.discogs.com/artist/${artist.id}`
                    : `https://www.discogs.com/search/?q=${encodeURIComponent(artist.name)}&type=artist`;
                
                recommendations.push({
                    title: this.getRandomAlbumTitle(),
                    artist: artist.name,
                    cover: artist.image,
                    reason: `Similar to ${artistInfo.name} in style and genre`,
                    link: artistLink
                });
            });
        }
        
        // Add recommendations based on the track's genre
        if (trackInfo.album && trackInfo.album.genres && trackInfo.album.genres.length > 0) {
            const genre = trackInfo.album.genres[0];
            // Link directly to genre page instead of search
            recommendations.push({
                title: this.getRandomAlbumTitle(),
                artist: this.getRandomArtistName(),
                cover: null,
                reason: `Popular in the ${genre} genre`,
                link: `https://www.discogs.com/genre/${encodeURIComponent(genre)}`
            });
        }
        
        // If we don't have enough recommendations, add some generic ones
        while (recommendations.length < 3) {
            recommendations.push({
                title: this.getRandomAlbumTitle(),
                artist: this.getRandomArtistName(),
                cover: null,
                reason: "You might enjoy this based on your listening history",
                link: "https://www.discogs.com/"
            });
        }
        
        return recommendations;
    }
    
    getRandomAlbumTitle() {
        const titles = [
            "Midnight Echoes", "Electric Dreams", "Sonic Landscapes", 
            "Rhythm & Soul", "Harmonic Convergence", "Analog Waves",
            "Digital Horizons", "Ambient Structures", "Future Memories",
            "Cosmic Journeys", "Synthetic Emotions", "Melodic Patterns"
        ];
        return titles[Math.floor(Math.random() * titles.length)];
    }
    
    getRandomArtistName() {
        const names = [
            "Aphex Twin", "Boards of Canada", "Four Tet", "Bonobo",
            "Jon Hopkins", "Tycho", "Floating Points", "Burial",
            "Nicolas Jaar", "Jamie xx", "Caribou", "Thom Yorke"
        ];
        return names[Math.floor(Math.random() * names.length)];
    }
    
    addEditListeners() {
        const editButton = this.drawer.querySelector('.track-details-edit-button');
        const saveButton = this.drawer.querySelector('.track-details-save-button');
        const cancelButton = this.drawer.querySelector('.track-details-cancel-button');
        
        if (editButton) {
            editButton.addEventListener('click', () => this.enableEditMode());
        }
        
        if (saveButton) {
            saveButton.addEventListener('click', () => this.saveEdits());
        }
        
        if (cancelButton) {
            cancelButton.addEventListener('click', () => this.cancelEdits());
        }
    }
    
    enableEditMode() {
        if (this.isEditing) return;
        
        this.isEditing = true;
        
        // Store original data for potential cancel
        this.originalTrackData = {
            artist: this.currentArtistInfo.name,
            track: this.currentTrackInfo.name || this.currentTrackInfo.title,
            album: this.currentTrackInfo.album.title || 'Unknown',
            year: this.currentTrackInfo.album.year || 'Unknown',
            label: this.currentTrackInfo.album.label || 'Unknown',
            catno: this.currentTrackInfo.album.catno || ''
        };
        
        // Add editing class to track details container
        const trackDetailsContainer = this.drawer.querySelector('.track-details');
        if (trackDetailsContainer) {
            trackDetailsContainer.classList.add('editing');
        }
        
        // Show edit actions
        const editActions = this.drawer.querySelector('.track-details-edit-actions');
        if (editActions) {
            editActions.classList.add('visible');
        }
        
        // Make fields editable
        const editableFields = this.drawer.querySelectorAll('.metadata-value.editable');
        editableFields.forEach(field => {
            const fieldName = field.getAttribute('data-field');
            const currentValue = field.textContent;
            
            field.classList.add('editing');
            field.innerHTML = `<input type="text" value="${currentValue}" data-original-value="${currentValue}">`;
        });
    }
    
    cancelEdits() {
        if (!this.isEditing) return;
        
        this.isEditing = false;
        
        // Remove editing class from track details container
        const trackDetailsContainer = this.drawer.querySelector('.track-details');
        if (trackDetailsContainer) {
            trackDetailsContainer.classList.remove('editing');
        }
        
        // Hide edit actions
        const editActions = this.drawer.querySelector('.track-details-edit-actions');
        if (editActions) {
            editActions.classList.remove('visible');
        }
        
        // Restore original values
        const editableFields = this.drawer.querySelectorAll('.metadata-value.editable');
        editableFields.forEach(field => {
            const fieldName = field.getAttribute('data-field');
            const originalValue = this.originalTrackData[fieldName] || field.querySelector('input').getAttribute('data-original-value');
            
            field.classList.remove('editing');
            field.textContent = originalValue;
        });
    }
    
    async saveEdits() {
        if (!this.isEditing) return;
        
        // Get edited values
        const editedData = {};
        const editableFields = this.drawer.querySelectorAll('.metadata-value.editable');
        
        editableFields.forEach(field => {
            const fieldName = field.getAttribute('data-field');
            const input = field.querySelector('input');
            if (input) {
                editedData[fieldName] = input.value;
            }
        });
        
        // Check if artist or track name has changed
        const artistChanged = editedData.artist !== this.originalTrackData.artist;
        const trackChanged = editedData.track !== this.originalTrackData.track;
        
        // Update UI first
        this.isEditing = false;
        
        // Remove editing class from track details container
        const trackDetailsContainer = this.drawer.querySelector('.track-details');
        if (trackDetailsContainer) {
            trackDetailsContainer.classList.remove('editing');
        }
        
        // Hide edit actions
        const editActions = this.drawer.querySelector('.track-details-edit-actions');
        if (editActions) {
            editActions.classList.remove('visible');
        }
        
        // Update fields with edited values
        editableFields.forEach(field => {
            const fieldName = field.getAttribute('data-field');
            const input = field.querySelector('input');
            if (input) {
                field.classList.remove('editing');
                field.textContent = input.value;
            }
        });
        
        // Update the track name in the episode if it has changed
        if (artistChanged || trackChanged) {
            // Find the track item that matches the original artist and track
            const trackItems = document.querySelectorAll('.track-item');
            let matchingTrackItem = null;
            
            trackItems.forEach(item => {
                const itemArtist = item.querySelector('.track-artist').textContent;
                const itemTitle = item.querySelector('.track-title').textContent;
                
                if (itemArtist === this.originalTrackData.artist && 
                    itemTitle === this.originalTrackData.track) {
                    matchingTrackItem = item;
                }
            });
            
            if (matchingTrackItem) {
                // Update the track item with the new artist and track name
                if (artistChanged) {
                    const artistElement = matchingTrackItem.querySelector('.track-artist');
                    artistElement.textContent = editedData.artist;
                    
                    // Update data attributes for the buttons
                    const downloadBtn = matchingTrackItem.querySelector('.track-download-btn');
                    const youtubeBtn = matchingTrackItem.querySelector('.track-youtube-btn');
                    
                    if (downloadBtn) downloadBtn.setAttribute('data-artist', editedData.artist);
                    if (youtubeBtn) youtubeBtn.setAttribute('data-artist', editedData.artist);
                }
                
                if (trackChanged) {
                    const titleElement = matchingTrackItem.querySelector('.track-title');
                    titleElement.textContent = editedData.track;
                    
                    // Update data attributes for the buttons
                    const downloadBtn = matchingTrackItem.querySelector('.track-download-btn');
                    const youtubeBtn = matchingTrackItem.querySelector('.track-youtube-btn');
                    
                    if (downloadBtn) downloadBtn.setAttribute('data-title', editedData.track);
                    if (youtubeBtn) youtubeBtn.setAttribute('data-title', editedData.track);
                }
                
                // Save changes to the server
                this.saveTrackChangesToServer(
                    this.originalTrackData.artist, 
                    this.originalTrackData.track, 
                    editedData.artist, 
                    editedData.track
                );
            }
            
            // Show loading state
            this.setLoading(true);
            
            try {
                // Clear cache for the old entry
                const oldCacheKey = `${this.originalTrackData.artist}:${this.originalTrackData.track}`;
                delete this.cache[oldCacheKey];
                
                // Update current track and artist
                this.currentArtist = editedData.artist;
                this.currentTrack = editedData.track;
                
                // Fetch new data
                await this.fetchTrackInfo(editedData.artist, editedData.track);
                
                // Show success message
                this.showSuccessMessage('Track information updated successfully!');
            } catch (error) {
                console.error('Error updating track info:', error);
                this.showError('Failed to update track information. Please try again.');
            } finally {
                this.setLoading(false);
            }
        }
    }
    
    async saveTrackChangesToServer(originalArtist, originalTitle, newArtist, newTitle) {
        try {
            // Get the current URL to identify the show
            const currentPath = window.location.pathname;
            const showSlug = currentPath.split('/').filter(segment => segment).pop();
            
            // Make API request to update the track
            const response = await fetch('/api/update_track', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    show_slug: showSlug,
                    original_artist: originalArtist,
                    original_title: originalTitle,
                    new_artist: newArtist,
                    new_title: newTitle
                })
            });
            
            if (!response.ok) {
                throw new Error('Failed to save track changes to server');
            }
            
            console.log('Track changes saved to server successfully');
        } catch (error) {
            console.error('Error saving track changes to server:', error);
            // We don't want to block the UI if server save fails
            // The UI changes will still be visible to the user
        }
    }
    
    showSuccessMessage(message) {
        const content = this.drawer.querySelector('.sidedrawer-content');
        const successElement = document.createElement('div');
        successElement.className = 'success-message';
        successElement.innerHTML = `
            <i class="fas fa-check-circle"></i>
            <p>${message}</p>
        `;
        
        // Insert at the top of the content
        content.insertBefore(successElement, content.firstChild);
        
        // Remove after 3 seconds
        setTimeout(() => {
            if (successElement.parentNode) {
                successElement.parentNode.removeChild(successElement);
            }
        }, 3000);
    }
}

// Initialize on DOM content loaded
document.addEventListener('DOMContentLoaded', () => {
    if (window.trackInfoDrawer) return;
    // Initialize track info drawer
    const trackInfoDrawer = new TrackInfoDrawer();
    
    // Make it globally accessible for debugging
    window.trackInfoDrawer = trackInfoDrawer;
}); 
