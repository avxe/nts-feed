/**
 * NTS Audio Player Module
 * Handles streaming audio playback for NTS episodes
 */

class NTSAudioPlayer {
    constructor() {
        this.isPlaying = false;
        this.isMuted = false;
        this.volume = 100;
        this.currentEpisode = null;
        this.currentTime = 0;
        this.duration = 0;
        this.isDragging = false;
        this.currentEpisodeIndex = -1;
        this.episodes = [];
        this.audioElement = null;
        this.player = null;
        this.progressInterval = null;
        
        // Track highlighting state
        this.currentTrackBoundaries = [];
        this.activeTrackIndex = -1;
        this.lastHighlightedItem = null;
        
        // Pending seek time to apply after audio is ready
        this.pendingSeekTime = null;
        
        // Episode like state
        this.isEpisodeLiked = false;
        this.episodeLikeId = null;
        
        // Bind methods to maintain context
        this.togglePlayPause = this.togglePlayPause.bind(this);
        this.toggleMute = this.toggleMute.bind(this);
        this.handleVolumeChange = this.handleVolumeChange.bind(this);
        this.toggleExpand = this.toggleExpand.bind(this);
        this.closePlayer = this.closePlayer.bind(this);
        this.playPreviousEpisode = this.playPreviousEpisode.bind(this);
        this.playNextEpisode = this.playNextEpisode.bind(this);
        this.startScrubbing = this.startScrubbing.bind(this);
        this.continueScrubbing = this.continueScrubbing.bind(this);
        this.endScrubbing = this.endScrubbing.bind(this);
        this.updateProgressIndicator = this.updateProgressIndicator.bind(this);
        
        // Bind highlight helpers
        this.updateActiveTrackHighlight = this.updateActiveTrackHighlight.bind(this);
        this.buildCurrentEpisodeTrackMap = this.buildCurrentEpisodeTrackMap.bind(this);
        
        // Bind episode like methods
        this.toggleEpisodeLike = this.toggleEpisodeLike.bind(this);
        this.checkEpisodeLikedStatus = this.checkEpisodeLikedStatus.bind(this);
    }

    buildListeningContext(episodeData = {}) {
        const episodeUrl = episodeData.url || '';
        const showUrl = episodeData.show_url || episodeData.source_show_url || '';
        const showTitle = episodeData.show_title || episodeData.source_show_title || '';
        return {
            kind: 'episode',
            player: 'nts_audio',
            source_page: window.location.pathname || '',
            source_url: window.location.href || '',
            episode_url: episodeUrl,
            episode_title: episodeData.title || '',
            episode_date: episodeData.date || '',
            show_url: showUrl,
            show_title: showTitle,
            title: episodeData.title || '',
            ...episodeData.playback_context,
        };
    }

    /**
     * Show the NTS audio player for a given episode
     * @param {Object} episodeData - Episode data containing URL, title, image, etc.
     */
    async showPlayer(episodeData) {
        console.log('Showing NTS audio player for:', episodeData);
        
        // Close YouTube player if it's open to prevent conflicts
        const youtubePlayer = document.getElementById('youtube-player');
        if (youtubePlayer && youtubePlayer.classList.contains('visible')) {
            const closeButton = youtubePlayer.querySelector('.youtube-player-close');
            if (closeButton) {
                closeButton.click();
            }
        }

        window.NTSListeningTracker.beginSession(this.buildListeningContext(episodeData));
        
        // Store all episodes for navigation
        if (this.episodes.length === 0) {
            this.gatherEpisodes();
        }
        
        // Find current episode index
        this.currentEpisodeIndex = this.episodes.findIndex(
            ep => ep.url === episodeData.url
        );
        
        // Create or update player UI
        this.createPlayerUI(episodeData);
        
        // Load the audio stream
        await this.loadAudioStream(episodeData);

        // Prepare track map for highlighting
        this.buildCurrentEpisodeTrackMap();
        
        // Check if episode is liked
        this.checkEpisodeLikedStatus();
    }

    /**
     * Gather all episodes from the page for navigation
     */
    gatherEpisodes() {
        const episodeElements = document.querySelectorAll('.episode-item');
        this.episodes = Array.from(episodeElements).map(element => {
            const titleElement = element.querySelector('.episode-title');
            const thumbnailElement = element.querySelector('.episode-thumbnail img');
            const dateElement = element.querySelector('.episode-date');
            
            return {
                url: titleElement ? titleElement.href : '',
                title: titleElement ? titleElement.textContent : '',
                image: thumbnailElement ? thumbnailElement.src : '',
                date: dateElement ? dateElement.textContent : '',
                element: element
            };
        });
    }

    /**
     * Create or update the player UI
     * @param {Object} episodeData - Episode data
     */
    createPlayerUI(episodeData) {
        let player = document.getElementById('nts-audio-player');
        let isNewPlayer = false;
        
        if (!player) {
            isNewPlayer = true;
            player = this.createPlayerElement(episodeData);
            document.body.appendChild(player);
            this.addEventListeners(player);
        } else {
            this.updatePlayerInfo(player, episodeData);
        }
        
        // Show the player with animation
        setTimeout(() => {
            player.classList.add('visible');
        }, 10);
        
        this.player = player;
    }

    /**
     * Create the player HTML element
     * @param {Object} episodeData - Episode data
     * @returns {HTMLElement} Player element
     */
    createPlayerElement(episodeData) {
        const player = document.createElement('div');
        player.id = 'nts-audio-player';
        player.className = 'nts-audio-player';
        
        player.innerHTML = `
            <div class="nts-audio-player-header">
                <div class="nts-audio-player-thumbnail" style="background-image: url('${episodeData.image || ''}')"></div>
                <div class="nts-audio-player-info">
                    <div class="nts-audio-player-title">${episodeData.title}</div>
                    <div class="nts-audio-player-channel">${episodeData.date}</div>
                    <div class="nts-audio-player-platform" data-platform=""></div>
                </div>
                <div class="nts-audio-player-center-controls">
                    <div class="nts-audio-player-main-controls">
                        <button class="nts-audio-player-prev" title="Previous Episode">
                            <i class="fas fa-step-backward"></i>
                        </button>
                        <button class="nts-audio-player-play-pause" title="Play/Pause">
                            <i class="fas fa-play"></i>
                        </button>
                        <button class="nts-audio-player-next" title="Next Episode">
                            <i class="fas fa-step-forward"></i>
                        </button>
                    </div>
                    <div class="nts-audio-player-progress-container">
                        <div class="nts-audio-player-progress-bar"></div>
                        <div class="nts-audio-player-progress-handle"></div>
                        <div class="nts-audio-player-progress-indicator">0:00</div>
                    </div>
                </div>
                <div class="nts-audio-player-controls">
                    <div class="nts-audio-player-time">0:00 / 0:00</div>
                    <button class="nts-audio-player-like" title="Like episode">
                        <i class="far fa-heart"></i>
                    </button>
                    <div class="nts-audio-player-volume-container">
                        <button class="nts-audio-player-volume" title="Mute/Unmute">
                            <i class="fas fa-volume-up"></i>
                        </button>
                        <div class="nts-audio-player-volume-slider-container">
                            <input type="range" class="nts-audio-player-volume-slider" min="0" max="100" value="100">
                        </div>
                    </div>
                    <button class="nts-audio-player-close" title="Close">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
            </div>
            <div class="nts-audio-player-container">
                <div class="nts-audio-player-loading">
                    <i class="fas fa-spinner fa-spin"></i>
                    <span style="margin-left: 8px;">Loading audio...</span>
                </div>
                <div class="nts-audio-player-error">
                    <div>
                        <i class="fas fa-exclamation-triangle"></i>
                        <div style="margin-top: 8px;">Audio not available for this episode</div>
                    </div>
                </div>
                <iframe class="nts-audio-player-iframe" style="display: none;"></iframe>
                <audio class="nts-audio-player-audio" style="display: none;" controls></audio>
            </div>
        `;
        
        return player;
    }

    /**
     * Update player info for new episode
     * @param {HTMLElement} player - Player element
     * @param {Object} episodeData - Episode data
     */
    updatePlayerInfo(player, episodeData) {
        player.querySelector('.nts-audio-player-title').textContent = episodeData.title;
        player.querySelector('.nts-audio-player-channel').textContent = episodeData.date;
        player.querySelector('.nts-audio-player-thumbnail').style.backgroundImage = `url('${episodeData.image || ''}')`;
        
        // Reset platform indicator
        const platformElement = player.querySelector('.nts-audio-player-platform');
        platformElement.textContent = '';
        platformElement.setAttribute('data-platform', '');
        
        // Make sure player is visible
        player.classList.add('visible');
        player.classList.remove('loading', 'error');
    }

    /**
     * Load audio stream for the episode
     * @param {Object} episodeData - Episode data
     */
    async loadAudioStream(episodeData) {
        try {
            this.player.classList.add('loading');
            console.log('Loading audio stream for:', episodeData.url);
            
            // Update loading message
            const loadingElement = this.player.querySelector('.nts-audio-player-loading span');
            if (loadingElement) {
                loadingElement.textContent = 'Extracting audio stream...';
            }
            
            // Fetch audio URL from API
            const response = await fetch(`/api/episode_audio/${encodeURIComponent(episodeData.url)}`);
            const audioData = await response.json();
            
            console.log('Audio service response:', audioData);
            
            if (audioData.success) {
                this.currentEpisode = {
                    ...episodeData,
                    ...audioData
                };
                
                // Update platform indicator
                const platformElement = this.player.querySelector('.nts-audio-player-platform');
                let platformText = '';
                switch(audioData.platform) {
                    case 'mixcloud':
                        platformText = 'MIXCLOUD STREAM';
                        break;
                    case 'soundcloud':
                        platformText = 'SOUNDCLOUD STREAM';
                        break;
                    case 'mixcloud_embed':
                        platformText = 'MIXCLOUD EMBED (DASH fallback)';
                        break;
                    case 'soundcloud_embed':
                        platformText = 'SOUNDCLOUD EMBED (DASH fallback)';
                        break;
                    case 'direct':
                        platformText = 'DIRECT STREAM';
                        break;
                    default:
                        platformText = audioData.platform.toUpperCase();
                }
                platformElement.textContent = platformText;
                platformElement.setAttribute('data-platform', audioData.platform);
                
                this.setupAudioPlayer(audioData);
                this.player.classList.remove('loading');
            } else {
                console.error('Audio service failed:', audioData.error);
                throw new Error(audioData.error || 'Failed to load audio');
            }
        } catch (error) {
            console.error('Error loading audio stream:', error);
            let errorMessage = 'Audio not available for this episode';
            
            // Provide more specific error messages
            if (error.message.includes('No streaming URL found')) {
                errorMessage = 'No audio stream found for this episode';
            } else if (error.message.includes('mixcloud')) {
                errorMessage = 'Failed to extract Mixcloud audio stream';
            } else if (error.message.includes('soundcloud')) {
                errorMessage = 'Failed to extract SoundCloud audio stream';
            } else if (error.message.includes('network') || error.message.includes('fetch')) {
                errorMessage = 'Network error - please try again';
            }
            
            this.showError(errorMessage);
        }
    }

    /**
     * Setup the appropriate audio player based on platform
     * @param {Object} audioData - Audio stream data
     */
    setupAudioPlayer(audioData) {
        const iframe = this.player.querySelector('.nts-audio-player-iframe');
        const audio = this.player.querySelector('.nts-audio-player-audio');
        
        // Check if we have a direct streaming URL (preferred)
        if (audioData.platform === 'mixcloud' && audioData.streaming_url) {
            // Use HTML5 audio for direct Mixcloud stream
            audio.src = audioData.streaming_url;
            audio.style.display = 'block';
            iframe.style.display = 'none';
            this.audioElement = audio;
            
            // Add audio event listeners
            this.setupAudioEventListeners(audio);
            
            // Try to start playback
            audio.load();
        } else if (audioData.platform === 'soundcloud' && audioData.streaming_url) {
            // Use HTML5 audio for direct SoundCloud stream
            audio.src = audioData.streaming_url;
            audio.style.display = 'block';
            iframe.style.display = 'none';
            this.audioElement = audio;
            
            // Add audio event listeners
            this.setupAudioEventListeners(audio);
            
            // Try to start playback
            audio.load();
        } else if (audioData.platform === 'direct' && audioData.streaming_url) {
            // Use audio element for direct files
            audio.src = audioData.streaming_url;
            audio.style.display = 'block';
            iframe.style.display = 'none';
            this.audioElement = audio;
            
            // Add audio event listeners
            this.setupAudioEventListeners(audio);
            
            // Try to start playback
            audio.load();
        } else if (audioData.platform === 'mixcloud_embed' && audioData.streaming_url) {
            // Fallback to iframe for Mixcloud embed
            iframe.src = audioData.streaming_url;
            iframe.style.display = 'block';
            audio.style.display = 'none';
            this.audioElement = iframe;
            
            // For embed, show expanded view
            if (!this.player.classList.contains('expanded')) {
                this.toggleExpand();
            }
        } else if (audioData.platform === 'soundcloud_embed' && audioData.streaming_url) {
            // Fallback to iframe for SoundCloud embed
            iframe.src = audioData.streaming_url;
            iframe.style.display = 'block';
            audio.style.display = 'none';
            this.audioElement = iframe;
            
            // For embed, show expanded view
            if (!this.player.classList.contains('expanded')) {
                this.toggleExpand();
            }
        } else {
            throw new Error('Unsupported audio platform or missing streaming URL');
        }
    }

    /**
     * Setup event listeners for HTML5 audio element
     * @param {HTMLAudioElement} audio - Audio element
     */
    setupAudioEventListeners(audio) {
        audio.addEventListener('loadstart', () => {
            console.log('Audio loading started');
        });
        
        audio.addEventListener('loadedmetadata', () => {
            this.duration = audio.duration;
            this.updateTimeDisplay();
            console.log('Audio metadata loaded, duration:', this.duration);
            // Apply any pending seek once metadata is available
            if (typeof this.pendingSeekTime === 'number' && this.pendingSeekTime >= 0) {
                try {
                    audio.currentTime = this.pendingSeekTime;
                } catch (_) { /* ignore */ }
                this.pendingSeekTime = null;
                this.updateActiveTrackHighlight();
            }
        });
        
        audio.addEventListener('canplay', () => {
            console.log('Audio can start playing - auto-starting playback...');
            // Enable play button
            const playButton = this.player.querySelector('.nts-audio-player-play-pause');
            if (playButton) {
                playButton.disabled = false;
                playButton.style.opacity = '1';
            }
            
            // Auto-start playback when audio is ready
            const playPromise = audio.play();
            if (playPromise !== undefined) {
                playPromise
                    .then(() => {
                        console.log('Auto-play started successfully');
                        this.isPlaying = true;
                        this.player.classList.remove('loading');
                        this.updatePlayPauseButton();
                        // Apply any pending seek on first play if still pending
                        if (typeof this.pendingSeekTime === 'number' && this.pendingSeekTime >= 0) {
                            try {
                                audio.currentTime = this.pendingSeekTime;
                            } catch (_) { /* ignore */ }
                            this.pendingSeekTime = null;
                            this.updateActiveTrackHighlight();
                        }
                    })
                    .catch((error) => {
                        console.warn('Auto-play failed (browser policy):', error);
                        // Still update the button state even if auto-play fails
                        this.player.classList.remove('loading');
                        this.updatePlayPauseButton();
                        // Show a subtle hint that user can click play
                        this.showAutoplayHint();
                        // Apply pending seek even if autoplay failed
                        if (typeof this.pendingSeekTime === 'number' && this.pendingSeekTime >= 0) {
                            try {
                                audio.currentTime = this.pendingSeekTime;
                            } catch (_) { /* ignore */ }
                            this.pendingSeekTime = null;
                            this.updateActiveTrackHighlight();
                        }
                    });
            } else {
                this.player.classList.remove('loading');
                this.updatePlayPauseButton();
                // Apply pending seek
                if (typeof this.pendingSeekTime === 'number' && this.pendingSeekTime >= 0) {
                    try {
                        audio.currentTime = this.pendingSeekTime;
                    } catch (_) { /* ignore */ }
                    this.pendingSeekTime = null;
                    this.updateActiveTrackHighlight();
                }
            }
        });
        
        audio.addEventListener('timeupdate', () => {
            if (!this.isDragging) {
                this.currentTime = audio.currentTime;
                this.updateProgress();
                window.NTSListeningTracker.syncProgress({
                    current_time: this.currentTime,
                    duration: this.duration || audio.duration || 0,
                    is_playing: this.isPlaying,
                });
                // Update track highlight on each time update
                this.updateActiveTrackHighlight();
            }
        });
        
        audio.addEventListener('play', () => {
            this.isPlaying = true;
            this.updatePlayPauseButton();
            console.log('Audio playback started');
            window.NTSListeningTracker.syncProgress({
                current_time: audio.currentTime || 0,
                duration: audio.duration || this.duration || 0,
                is_playing: true,
            });
        });
        
        audio.addEventListener('pause', () => {
            this.isPlaying = false;
            this.updatePlayPauseButton();
            window.NTSListeningTracker.syncProgress({
                current_time: audio.currentTime || 0,
                duration: audio.duration || this.duration || 0,
                is_playing: false,
            });
        });
        
        audio.addEventListener('ended', () => {
            this.isPlaying = false;
            this.updatePlayPauseButton();
            window.NTSListeningTracker.syncProgress({
                current_time: audio.duration || audio.currentTime || this.currentTime || 0,
                duration: audio.duration || this.duration || 0,
                is_playing: false,
                ended: true,
            });
            window.NTSListeningTracker.closeSession('ended', {
                current_time: audio.duration || audio.currentTime || this.currentTime || 0,
                duration: audio.duration || this.duration || 0,
            });
            // Auto-play next episode if available
            this.playNextEpisode();
            // Clear highlight at end
            if (this.lastHighlightedItem) {
                this.lastHighlightedItem.classList.remove('playing');
                this.lastHighlightedItem = null;
                this.activeTrackIndex = -1;
            }
        });
        
        audio.addEventListener('error', (e) => {
            console.error('Audio error:', e);
            const error = e.target.error;
            let errorMessage = 'Failed to load audio stream';
            
            if (error) {
                switch (error.code) {
                    case error.MEDIA_ERR_ABORTED:
                        errorMessage = 'Audio loading was aborted';
                        break;
                    case error.MEDIA_ERR_NETWORK:
                        errorMessage = 'Network error while loading audio';
                        break;
                    case error.MEDIA_ERR_DECODE:
                        errorMessage = 'Audio format not supported';
                        break;
                    case error.MEDIA_ERR_SRC_NOT_SUPPORTED:
                        errorMessage = 'Audio source not supported - trying fallback...';
                        // Try to fall back to embed if available
                        if (this.currentEpisode && this.currentEpisode.embed_url) {
                            console.log('Falling back to embed player...');
                            this.setupEmbedFallback();
                            return;
                        }
                        break;
                    default:
                        errorMessage = `Audio error (code: ${error.code})`;
                }
            }
            
            this.showError(errorMessage);
        });
        
        audio.addEventListener('stalled', () => {
            console.warn('Audio playback stalled');
        });
        
        audio.addEventListener('waiting', () => {
            console.log('Audio waiting for data');
        });
    }

    /**
     * Add event listeners to player controls
     * @param {HTMLElement} player - Player element
     */
    addEventListeners(player) {
        // Play/Pause button
        const playPauseButton = player.querySelector('.nts-audio-player-play-pause');
        playPauseButton.addEventListener('click', this.togglePlayPause);
        
        // Volume controls
        const volumeButton = player.querySelector('.nts-audio-player-volume');
        volumeButton.addEventListener('click', this.toggleMute);
        
        const volumeSlider = player.querySelector('.nts-audio-player-volume-slider');
        volumeSlider.addEventListener('input', this.handleVolumeChange);
        
        // Thumbnail click to expand
        const thumbnail = player.querySelector('.nts-audio-player-thumbnail');
        thumbnail.addEventListener('click', this.toggleExpand);
        
        // Close button
        const closeButton = player.querySelector('.nts-audio-player-close');
        closeButton.addEventListener('click', this.closePlayer);
        
        // Navigation buttons
        const prevButton = player.querySelector('.nts-audio-player-prev');
        prevButton.addEventListener('click', this.playPreviousEpisode);
        
        const nextButton = player.querySelector('.nts-audio-player-next');
        nextButton.addEventListener('click', this.playNextEpisode);
        
        // Progress bar scrubbing
        const progressContainer = player.querySelector('.nts-audio-player-progress-container');
        progressContainer.addEventListener('mousedown', this.startScrubbing);
        progressContainer.addEventListener('touchstart', this.startScrubbing, { passive: true });
        progressContainer.addEventListener('mousemove', this.updateProgressIndicator);
        document.addEventListener('mousemove', this.continueScrubbing);
        document.addEventListener('touchmove', this.continueScrubbing, { passive: true });
        document.addEventListener('mouseup', this.endScrubbing);
        document.addEventListener('touchend', this.endScrubbing);
        
        // Episode like button
        const likeButton = player.querySelector('.nts-audio-player-like');
        likeButton.addEventListener('click', this.toggleEpisodeLike);
    }

    /**
     * Toggle play/pause
     */
    togglePlayPause() {
        if (!this.audioElement) return;
        
        if (this.audioElement.tagName === 'AUDIO') {
            if (this.isPlaying) {
                this.audioElement.pause();
            } else {
                // Try to play, with error handling
                const playPromise = this.audioElement.play();
                if (playPromise !== undefined) {
                    playPromise
                        .then(() => {
                            console.log('Audio playback started successfully');
                        })
                        .catch((error) => {
                            console.error('Error starting playback:', error);
                            this.showError('Failed to start audio playback');
                        });
                }
            }
        } else if (this.audioElement.tagName === 'IFRAME') {
            // For iframes, we can't control playback directly
            // Show a message or expand the player so user can control it
            if (!this.player.classList.contains('expanded')) {
                this.toggleExpand();
            }
        }
    }

    /**
     * Update play/pause button icon
     */
    updatePlayPauseButton() {
        if (!this.player) return;
        const playPauseButton = this.player.querySelector('.nts-audio-player-play-pause i');
        if (playPauseButton) {
            if (this.isPlaying) {
                playPauseButton.className = 'fas fa-pause';
            } else {
                playPauseButton.className = 'fas fa-play';
            }
        }
    }

    /**
     * Toggle mute
     */
    toggleMute() {
        if (!this.audioElement || this.audioElement.tagName !== 'AUDIO') return;
        
        const volumeButton = this.player.querySelector('.nts-audio-player-volume i');
        const volumeSlider = this.player.querySelector('.nts-audio-player-volume-slider');
        
        if (this.isMuted) {
            this.audioElement.muted = false;
            this.audioElement.volume = this.volume / 100;
            volumeButton.className = this.volume > 50 ? 'fas fa-volume-up' : 'fas fa-volume-down';
            volumeSlider.value = this.volume;
            this.isMuted = false;
        } else {
            this.audioElement.muted = true;
            volumeButton.className = 'fas fa-volume-mute';
            this.isMuted = true;
        }
    }

    /**
     * Handle volume change
     * @param {Event} e - Input event
     */
    handleVolumeChange(e) {
        if (!this.audioElement || this.audioElement.tagName !== 'AUDIO') return;
        
        const volume = e.target.value;
        this.volume = volume;
        this.audioElement.volume = volume / 100;
        
        const volumeButton = this.player.querySelector('.nts-audio-player-volume i');
        if (volume === '0') {
            volumeButton.className = 'fas fa-volume-mute';
            this.isMuted = true;
        } else {
            volumeButton.className = volume > 50 ? 'fas fa-volume-up' : 'fas fa-volume-down';
            this.isMuted = false;
            this.audioElement.muted = false;
        }
    }

    /**
     * Toggle expand/collapse
     */
    toggleExpand() {
        if (this.player) {
            this.player.classList.toggle('expanded');
            
            const thumbnail = this.player.querySelector('.nts-audio-player-thumbnail');
            if (this.player.classList.contains('expanded')) {
                thumbnail.title = 'Collapse player';
            } else {
                thumbnail.title = 'Expand player';
            }
        }
    }

    /**
     * Close player
     */
    closePlayer() {
        if (this.player) {
            this.player.classList.remove('visible');
            
            setTimeout(() => {
                window.NTSListeningTracker.closeSession('close', {
                    current_time: this.currentTime || 0,
                    duration: this.duration || 0,
                });
                if (this.audioElement && this.audioElement.tagName === 'AUDIO') {
                    this.audioElement.pause();
                }
                
                // Clear progress tracking
                if (this.progressInterval) {
                    clearInterval(this.progressInterval);
                    this.progressInterval = null;
                }
                
                // Reset player state
                this.isPlaying = false;
                this.currentEpisode = null;
                this.currentTime = 0;
                this.duration = 0;
                this.audioElement = null;
                
                // Remove player from DOM
                if (this.player.parentNode) {
                    this.player.parentNode.removeChild(this.player);
                }
                this.player = null;
            }, 300);
        }
    }

    /**
     * Play previous episode
     */
    playPreviousEpisode() {
        if (this.episodes.length === 0 || this.currentEpisodeIndex <= 0) {
            return;
        }
        
        const prevIndex = this.currentEpisodeIndex - 1;
        const prevEpisode = this.episodes[prevIndex];
        
        if (prevEpisode) {
            this.showPlayer(prevEpisode);
        }
    }

    /**
     * Play next episode
     */
    playNextEpisode() {
        if (this.episodes.length === 0 || 
            this.currentEpisodeIndex >= this.episodes.length - 1) {
            return;
        }
        
        const nextIndex = this.currentEpisodeIndex + 1;
        const nextEpisode = this.episodes[nextIndex];
        
        if (nextEpisode) {
            this.showPlayer(nextEpisode);
        }
    }

    /**
     * Update progress bar and time display
     */
    updateProgress() {
        if (!this.audioElement || this.isDragging) return;
        
        const progressBar = this.player.querySelector('.nts-audio-player-progress-bar');
        const progressHandle = this.player.querySelector('.nts-audio-player-progress-handle');
        
        if (progressBar && this.duration > 0) {
            const percent = (this.currentTime / this.duration) * 100;
            progressBar.style.width = `${percent}%`;
            
            if (progressHandle) {
                progressHandle.style.left = `${percent}%`;
            }
            
            this.updateTimeDisplay();
        }
    }

    /**
     * Parse a timestamp string like HH:MM:SS or MM:SS to seconds
     * @param {string} ts
     * @returns {number|null}
     */
    parseTimestampToSeconds(ts) {
        try {
            if (!ts || typeof ts !== 'string') return null;
            const parts = ts.trim().split(':').map(p => p.trim());
            if (parts.length < 2 || parts.some(p => p === '' || isNaN(parseInt(p, 10)))) return null;
            const nums = parts.map(p => parseInt(p, 10));
            let seconds = 0;
            if (nums.length === 3) {
                seconds = nums[0] * 3600 + nums[1] * 60 + nums[2];
            } else if (nums.length === 2) {
                seconds = nums[0] * 60 + nums[1];
            } else {
                seconds = nums[0];
            }
            return Number.isFinite(seconds) ? seconds : null;
        } catch (_) {
            return null;
        }
    }

    /**
     * Build the current episode's track timestamp map for highlighting
     */
    buildCurrentEpisodeTrackMap() {
        try {
            this.currentTrackBoundaries = [];
            this.activeTrackIndex = -1;
            if (!Array.isArray(this.episodes) || this.currentEpisodeIndex < 0) return;
            const currentEp = this.episodes[this.currentEpisodeIndex];
            if (!currentEp || !currentEp.element) return;
            const listItems = currentEp.element.querySelectorAll('.tracks-list .track-item');
            if (!listItems || listItems.length === 0) return;

            // Collect tracks with timestamps
            const tracks = Array.from(listItems).map((li, idx) => {
                const tsEl = li.querySelector('.track-timestamp');
                const tsText = tsEl ? (tsEl.textContent || '').trim() : '';
                const start = this.parseTimestampToSeconds(tsText);
                return { index: idx, start, el: li };
            }).filter(t => t.start !== null && t.start >= 0);

            if (tracks.length === 0) return;

            // Sort by start time just in case
            tracks.sort((a, b) => a.start - b.start);

            // Compute end boundaries (next start or duration if known)
            const duration = this.duration || null;
            const boundaries = tracks.map((t, i) => {
                const next = tracks[i + 1];
                const end = next ? next.start : (duration || Infinity);
                return { start: t.start, end, el: t.el };
            });

            this.currentTrackBoundaries = boundaries;
        } catch (e) {
            console.warn('Failed to build track timestamp map:', e);
        }
    }

    /**
     * Update DOM highlight for the track corresponding to currentTime
     */
    updateActiveTrackHighlight() {
        if (!this.currentTrackBoundaries || this.currentTrackBoundaries.length === 0) return;
        if (typeof this.currentTime !== 'number') return;

        // Find active boundary
        const t = this.currentTime;
        let activeIdx = -1;
        for (let i = 0; i < this.currentTrackBoundaries.length; i++) {
            const b = this.currentTrackBoundaries[i];
            if (t >= b.start && t < b.end) {
                activeIdx = i;
                break;
            }
        }

        if (activeIdx === this.activeTrackIndex) return;

        // Remove previous highlight
        if (this.lastHighlightedItem) {
            this.lastHighlightedItem.classList.remove('playing');
            this.lastHighlightedItem = null;
        }

        this.activeTrackIndex = activeIdx;
        if (activeIdx >= 0) {
            const item = this.currentTrackBoundaries[activeIdx].el;
            if (item) {
                item.classList.add('playing');
                this.lastHighlightedItem = item;
            }
        }
    }

    /**
     * Update time display
     */
    updateTimeDisplay() {
        const timeDisplay = this.player.querySelector('.nts-audio-player-time');
        if (timeDisplay) {
            timeDisplay.textContent = `${this.formatTime(this.currentTime)} / ${this.formatTime(this.duration)}`;
        }
    }

    /**
     * Format time in MM:SS format
     * @param {number} seconds - Time in seconds
     * @returns {string} Formatted time
     */
    formatTime(seconds) {
        seconds = Math.floor(seconds);
        const minutes = Math.floor(seconds / 60);
        seconds = seconds % 60;
        return `${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;
    }

    /**
     * Start scrubbing
     * @param {Event} e - Mouse/touch event
     */
    startScrubbing(e) {
        if (!this.audioElement || this.audioElement.tagName !== 'AUDIO') return;
        
        e.preventDefault();
        this.isDragging = true;
        this.continueScrubbing(e);
    }

    /**
     * Continue scrubbing
     * @param {Event} e - Mouse/touch event
     */
    continueScrubbing(e) {
        if (!this.isDragging || !this.audioElement || this.audioElement.tagName !== 'AUDIO') return;
        
        const progressContainer = this.player.querySelector('.nts-audio-player-progress-container');
        if (!progressContainer) return;
        
        let clientX;
        if (e.type.includes('touch')) {
            clientX = e.touches[0].clientX;
        } else {
            clientX = e.clientX;
        }
        
        const rect = progressContainer.getBoundingClientRect();
        let percent = (clientX - rect.left) / rect.width;
        percent = Math.max(0, Math.min(1, percent));
        
        // Update visual progress
        const progressBar = this.player.querySelector('.nts-audio-player-progress-bar');
        const progressHandle = this.player.querySelector('.nts-audio-player-progress-handle');
        const progressIndicator = this.player.querySelector('.nts-audio-player-progress-indicator');
        
        if (progressBar) {
            progressBar.style.width = `${percent * 100}%`;
        }
        if (progressHandle) {
            progressHandle.style.left = `${percent * 100}%`;
        }
        
        // Update indicator
        if (progressIndicator) {
            progressIndicator.style.left = `${percent * 100}%`;
            const time = percent * this.duration;
            progressIndicator.textContent = this.formatTime(time);
        }
        
        // Update time display
        const timeDisplay = this.player.querySelector('.nts-audio-player-time');
        if (timeDisplay && this.duration) {
            const newTime = percent * this.duration;
            timeDisplay.textContent = `${this.formatTime(newTime)} / ${this.formatTime(this.duration)}`;
        }
    }

    /**
     * End scrubbing
     * @param {Event} e - Mouse/touch event
     */
    endScrubbing(e) {
        if (!this.isDragging || !this.audioElement || this.audioElement.tagName !== 'AUDIO') return;
        
        this.isDragging = false;
        
        const progressContainer = this.player.querySelector('.nts-audio-player-progress-container');
        if (!progressContainer) return;
        
        let clientX;
        if (e.type.includes('touch')) {
            clientX = e.changedTouches[0].clientX;
        } else {
            clientX = e.clientX;
        }
        
        const rect = progressContainer.getBoundingClientRect();
        let percent = (clientX - rect.left) / rect.width;
        percent = Math.max(0, Math.min(1, percent));
        
        // Seek to new position
        const newTime = percent * this.duration;
        this.audioElement.currentTime = newTime;
        this.currentTime = newTime;
    }

    /**
     * Update progress indicator on hover
     * @param {Event} e - Mouse event
     */
    updateProgressIndicator(e) {
        if (!this.duration) return;
        
        const progressContainer = this.player.querySelector('.nts-audio-player-progress-container');
        const progressIndicator = this.player.querySelector('.nts-audio-player-progress-indicator');
        
        if (!progressContainer || !progressIndicator) return;
        
        const rect = progressContainer.getBoundingClientRect();
        let percent = (e.clientX - rect.left) / rect.width;
        percent = Math.max(0, Math.min(1, percent));
        
        progressIndicator.style.left = `${percent * 100}%`;
        const time = percent * this.duration;
        progressIndicator.textContent = this.formatTime(time);
    }

    /**
     * Setup embed fallback when direct streaming fails
     */
    setupEmbedFallback() {
        if (!this.currentEpisode || !this.currentEpisode.embed_url) {
            this.showError('No fallback available');
            return;
        }
        
        console.log('Setting up embed fallback with URL:', this.currentEpisode.embed_url);
        
        const iframe = this.player.querySelector('.nts-audio-player-iframe');
        const audio = this.player.querySelector('.nts-audio-player-audio');
        
        // Switch to iframe embed
        iframe.src = this.currentEpisode.embed_url;
        iframe.style.display = 'block';
        audio.style.display = 'none';
        this.audioElement = iframe;
        
        // Remove error state and show expanded player
        this.player.classList.remove('error', 'loading');
        if (!this.player.classList.contains('expanded')) {
            this.toggleExpand();
        }
        
        // Update platform indicator
        const platformElement = this.player.querySelector('.nts-audio-player-platform');
        if (platformElement) {
            platformElement.textContent = platformElement.textContent.replace('STREAM', 'EMBED');
        }
    }

    /**
     * Show auto-play hint when browser prevents auto-play
     */
    showAutoplayHint() {
        const playButton = this.player.querySelector('.nts-audio-player-play-pause');
        if (playButton) {
            // Add a subtle pulse animation to indicate user should click
            playButton.style.animation = 'pulse 2s infinite';
            
            // Show temporary hint message
            const platformElement = this.player.querySelector('.nts-audio-player-platform');
            if (platformElement) {
                const originalText = platformElement.textContent;
                platformElement.textContent = 'Click ▶ to start playback';
                platformElement.style.color = 'var(--color-accent)';
                
                // Restore original text after 3 seconds
                setTimeout(() => {
                    platformElement.textContent = originalText;
                    platformElement.style.color = '';
                    playButton.style.animation = '';
                }, 3000);
            }
        }
    }

    /**
     * Show error state
     * @param {string} message - Error message
     */
    showError(message) {
        if (this.player) {
            this.player.classList.remove('loading');
            this.player.classList.add('error');
            
            const errorElement = this.player.querySelector('.nts-audio-player-error div');
            if (errorElement) {
                errorElement.innerHTML = `
                    <i class="fas fa-exclamation-triangle"></i>
                    <div style="margin-top: 8px;">${message}</div>
                    ${this.currentEpisode && this.currentEpisode.embed_url ? 
                        '<button onclick="window.ntsAudioPlayer.setupEmbedFallback()" style="margin-top: 10px; padding: 5px 10px; background: var(--color-text-primary); color: var(--color-background); border: none; border-radius: 4px; cursor: pointer;">Try Embed Player</button>' : 
                        ''}
                `;
            }
        }
    }

    /**
     * Public: Seek to a time in seconds. If a different episode is specified, load it first.
     * @param {number} seconds
     * @param {string} episodeUrl - Optional URL of the episode to play/seek within
     * @param {Object} episodeData - Optional episodeData { url, title, date, image }
     */
    async seekTo(seconds, episodeUrl, episodeData) {
        try {
            const targetSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
            this.pendingSeekTime = targetSeconds;

            // If we already have audio for the requested episode, seek immediately
            if (this.audioElement && this.currentEpisode && (!episodeUrl || this.currentEpisode.url === episodeUrl)) {
                if (this.audioElement.tagName === 'AUDIO') {
                    try {
                        this.audioElement.currentTime = targetSeconds;
                        window.NTSListeningTracker.syncProgress({
                            current_time: targetSeconds,
                            duration: this.duration || this.audioElement.duration || 0,
                            is_playing: this.isPlaying,
                        });
                        // Start playback if not already playing
                        if (!this.isPlaying) {
                            const playPromise = this.audioElement.play();
                            if (playPromise && typeof playPromise.then === 'function') {
                                await playPromise.catch(() => {});
                            }
                        }
                        // Rebuild track boundaries and update highlight after seek
                        this.buildCurrentEpisodeTrackMap();
                        this.updateActiveTrackHighlight();
                        this.updateActiveTrackHighlight();
                        this.pendingSeekTime = null;
                        return;
                    } catch (_) { /* fall through to reload */ }
                }
                // If iframe (embed) we cannot seek programmatically
                // Just return without changing episode
                return;
            }

            // If another episode is requested, switch to it and apply pending seek on ready
            if (episodeUrl) {
                if (!this.episodes || this.episodes.length === 0) {
                    this.gatherEpisodes();
                }
                let targetEpisode = null;
                if (episodeData && episodeData.url) {
                    targetEpisode = episodeData;
                } else {
                    targetEpisode = (this.episodes || []).find(ep => ep.url === episodeUrl) || null;
                }
                if (targetEpisode) {
                    await this.showPlayer(targetEpisode);
                    // After player shows, rebuild boundaries (timestamps may be present now)
                    this.buildCurrentEpisodeTrackMap();
                    this.updateActiveTrackHighlight();
                }
            }
        } catch (e) {
            console.warn('Seek failed:', e);
        }
    }

    /**
     * Check if the current episode is liked
     */
    async checkEpisodeLikedStatus() {
        if (!this.currentEpisode || !this.currentEpisode.url) return;
        
        try {
            const res = await fetch('/api/episodes/likes/check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ episode_urls: [this.currentEpisode.url] })
            });
            const data = await res.json();
            
            if (data.success && data.liked) {
                const info = data.liked[this.currentEpisode.url];
                if (info && info.liked) {
                    this.isEpisodeLiked = true;
                    this.episodeLikeId = info.id;
                } else {
                    this.isEpisodeLiked = false;
                    this.episodeLikeId = null;
                }
                this.updateEpisodeLikeButton();
            }
        } catch (err) {
            console.error('Failed to check episode liked status:', err);
        }
    }

    /**
     * Toggle like state for current episode
     */
    async toggleEpisodeLike() {
        if (!this.currentEpisode || !this.currentEpisode.url) return;
        
        const likeBtn = this.player?.querySelector('.nts-audio-player-like');
        if (likeBtn) likeBtn.disabled = true;
        
        try {
            if (this.isEpisodeLiked && this.episodeLikeId) {
                // Unlike
                const res = await fetch(`/api/episodes/likes/${this.episodeLikeId}`, { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    this.isEpisodeLiked = false;
                    this.episodeLikeId = null;
                    if (window.showNotification) {
                        window.showNotification('Removed episode from likes', 'info');
                    }
                }
            } else {
                // Like
                const showTitle = document.querySelector('.header-container h1')?.textContent || '';
                const showUrl = window.location.pathname;
                
                const res = await fetch('/api/episodes/likes', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        episode_url: this.currentEpisode.url,
                        episode_title: this.currentEpisode.title || '',
                        episode_date: this.currentEpisode.date || '',
                        image_url: this.currentEpisode.image || '',
                        show_title: showTitle,
                        show_url: showUrl
                    })
                });
                const data = await res.json();
                if (data.success) {
                    this.isEpisodeLiked = true;
                    this.episodeLikeId = data.like_id;
                    if (data.message !== 'Already liked' && window.showNotification) {
                        window.showNotification('Episode added to likes', 'success');
                    }
                }
            }
            this.updateEpisodeLikeButton();
        } catch (err) {
            console.error('Episode like error:', err);
            if (window.showNotification) {
                window.showNotification('Failed to update episode like', 'error');
            }
        } finally {
            if (likeBtn) likeBtn.disabled = false;
        }
    }

    /**
     * Update the like button visual state
     */
    updateEpisodeLikeButton() {
        const likeBtn = this.player?.querySelector('.nts-audio-player-like');
        if (!likeBtn) return;
        
        const icon = likeBtn.querySelector('i');
        if (icon) {
            if (this.isEpisodeLiked) {
                icon.className = 'fas fa-heart';
                likeBtn.classList.add('liked');
                likeBtn.title = 'Unlike episode';
            } else {
                icon.className = 'far fa-heart';
                likeBtn.classList.remove('liked');
                likeBtn.title = 'Like episode';
            }
        }
    }
}

// Export the class for use in other modules
window.NTSAudioPlayer = NTSAudioPlayer;
