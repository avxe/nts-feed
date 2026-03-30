/**
 * Global YouTube Player Module
 * Provides persistent YouTube playback that survives SPA page navigation.
 * This module is loaded once in base.html and maintains state across page transitions.
 */

(function() {
    'use strict';

    const LISTENING_ENDPOINT = '/api/listening/sessions';
    const LISTENING_FLUSH_INTERVAL_MS = 15000;
    const LISTENING_MEANINGFUL_SECONDS = 120;
    const LISTENING_MEANINGFUL_RATIO = 0.2;
    const LISTENING_COMPLETED_RATIO = 0.85;
    const LISTENING_FINALIZED_EVENT = 'listening:session-finalized';

    function createListeningTracker() {
        const trackerState = {
            session: null,
            flushTimer: null,
            lifecycleBound: false,
        };

        function nowIso() {
            return new Date().toISOString();
        }

        function generateSessionToken() {
            if (window.crypto && typeof window.crypto.randomUUID === 'function') {
                return window.crypto.randomUUID();
            }
            return `listen_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
        }

        function toNumber(value) {
            const num = Number(value);
            return Number.isFinite(num) && num > 0 ? num : 0;
        }

        function normalizeContext(context) {
            const base = context && typeof context === 'object' ? context : {};
            return {
                session_token: base.session_token || base.sessionToken || generateSessionToken(),
                kind: base.kind === 'track' ? 'track' : 'episode',
                player: base.player === 'nts_audio' ? 'nts_audio' : 'youtube',
                started_at: base.started_at || null,
                last_event_at: base.last_event_at || nowIso(),
                ended_at: base.ended_at || null,
                listened_seconds: toNumber(base.listened_seconds),
                duration_seconds: toNumber(base.duration_seconds),
                max_position_seconds: toNumber(base.max_position_seconds),
                completion_ratio: toNumber(base.completion_ratio),
                is_meaningful: !!base.is_meaningful,
                is_completed: !!base.is_completed,
                is_playing: !!base.is_playing,
                context: {
                    source_page: base.source_page || window.location.pathname || '',
                    source_url: base.source_url || window.location.href || '',
                    episode_url: base.episode_url || '',
                    episode_title: base.episode_title || '',
                    episode_date: base.episode_date || '',
                    show_url: base.show_url || '',
                    show_title: base.show_title || '',
                    track_artist: base.track_artist || base.artist || '',
                    track_title: base.track_title || base.title || '',
                    artist: base.artist || '',
                    title: base.title || '',
                    video_id: base.video_id || '',
                    video_url: base.video_url || '',
                }
            };
        }

        function applyDerivedFlags(session) {
            session.completion_ratio = session.duration_seconds > 0
                ? Math.min(1, session.max_position_seconds / session.duration_seconds)
                : 0;
            session.is_meaningful = session.listened_seconds >= LISTENING_MEANINGFUL_SECONDS
                || session.completion_ratio >= LISTENING_MEANINGFUL_RATIO;
            session.is_completed = !!session.is_completed
                || session.completion_ratio >= LISTENING_COMPLETED_RATIO;
        }

        function stopFlushTimer() {
            if (trackerState.flushTimer) {
                clearInterval(trackerState.flushTimer);
                trackerState.flushTimer = null;
            }
        }

        function startFlushTimer() {
            if (!trackerState.session || !trackerState.session.is_playing || trackerState.flushTimer) return;
            trackerState.flushTimer = setInterval(() => {
                flushSession('interval');
            }, LISTENING_FLUSH_INTERVAL_MS);
        }

        function buildPayload(reason, isFinal) {
            if (!trackerState.session) return null;
            applyDerivedFlags(trackerState.session);
            return {
                session_token: trackerState.session.session_token,
                kind: trackerState.session.kind,
                player: trackerState.session.player,
                started_at: trackerState.session.started_at,
                last_event_at: nowIso(),
                ended_at: trackerState.session.ended_at,
                listened_seconds: Number(trackerState.session.listened_seconds.toFixed(3)),
                duration_seconds: Number(trackerState.session.duration_seconds.toFixed(3)),
                max_position_seconds: Number(trackerState.session.max_position_seconds.toFixed(3)),
                completion_ratio: Number(trackerState.session.completion_ratio.toFixed(6)),
                is_meaningful: trackerState.session.is_meaningful,
                is_completed: trackerState.session.is_completed,
                is_playing: !!trackerState.session.is_playing,
                reason,
                context: { ...trackerState.session.context },
            };
        }

        function sendPayload(payload) {
            if (!payload) return false;

            const body = JSON.stringify(payload);

            if (navigator.sendBeacon) {
                try {
                    const blob = new Blob([body], { type: 'application/json' });
                    if (navigator.sendBeacon(LISTENING_ENDPOINT, blob)) {
                        return true;
                    }
                } catch (_) {
                    // Fall through to fetch
                }
            }

            if (window.fetch) {
                fetch(LISTENING_ENDPOINT, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body,
                    keepalive: true,
                }).catch(() => {});
                return true;
            }

            return false;
        }

        function emitFinalizedEvent(reason, payload) {
            if (
                !payload
                || (!payload.is_meaningful && !payload.is_completed)
                || !window
                || typeof window.dispatchEvent !== 'function'
                || typeof CustomEvent !== 'function'
            ) {
                return;
            }

            window.dispatchEvent(new CustomEvent(LISTENING_FINALIZED_EVENT, {
                detail: {
                    reason,
                    player: payload.player,
                    sessionToken: payload.session_token,
                    payload,
                },
            }));
        }

        function beginSession(context = {}) {
            const next = normalizeContext(context);
            if (trackerState.session && trackerState.session.session_token !== next.session_token) {
                flushSession('source-switch', true);
            }
            trackerState.session = next;
            trackerState.session.is_playing = false;
            trackerState.session.last_sample_at = null;
            ensureLifecycleBound();
            return trackerState.session;
        }

        function syncProgress(sample = {}) {
            if (sample.context) {
                beginSession(sample.context);
            }
            if (!trackerState.session) return null;

            const now = Date.now();
            const currentTime = toNumber(sample.current_time ?? sample.currentTime);
            const duration = toNumber(sample.duration);
            const isPlaying = !!sample.is_playing;
            const ended = !!sample.ended;

            if (duration > trackerState.session.duration_seconds) {
                trackerState.session.duration_seconds = duration;
            }
            if (currentTime > trackerState.session.max_position_seconds) {
                trackerState.session.max_position_seconds = currentTime;
            }

            trackerState.session.is_playing = isPlaying;
            trackerState.session.last_event_at = nowIso();

            if (isPlaying) {
                if (!trackerState.session.started_at) {
                    trackerState.session.started_at = nowIso();
                }
                if (!trackerState.session.last_sample_at) {
                    trackerState.session.last_sample_at = now;
                } else {
                    trackerState.session.listened_seconds += Math.max(0, (now - trackerState.session.last_sample_at) / 1000);
                    trackerState.session.last_sample_at = now;
                }
                startFlushTimer();
            } else if (trackerState.session.last_sample_at) {
                trackerState.session.listened_seconds += Math.max(0, (now - trackerState.session.last_sample_at) / 1000);
                trackerState.session.last_sample_at = null;
                stopFlushTimer();
            } else {
                stopFlushTimer();
            }

            if (ended) {
                trackerState.session.ended_at = nowIso();
                trackerState.session.is_completed = true;
            }

            applyDerivedFlags(trackerState.session);
            return trackerState.session;
        }

        function flushSession(reason = 'flush', isFinal = false) {
            if (!trackerState.session) return false;
            const hasProgress = trackerState.session.listened_seconds > 0 || trackerState.session.max_position_seconds > 0;
            if (!hasProgress && !trackerState.session.is_completed) {
                if (isFinal) {
                    stopFlushTimer();
                    trackerState.session = null;
                }
                return false;
            }

            const payload = buildPayload(reason, isFinal);
            const sent = sendPayload(payload);
            if (isFinal) {
                stopFlushTimer();
                trackerState.session = null;
            }
            return sent;
        }

        function closeSession(reason = 'close', sample = {}) {
            if (!trackerState.session) return false;
            syncProgress({
                ...sample,
                is_playing: false,
            });
            if (reason === 'ended') {
                trackerState.session.is_completed = true;
                trackerState.session.ended_at = nowIso();
            }
            const finalPayload = buildPayload(reason, true);
            const sent = flushSession(reason, true);
            if (sent) {
                emitFinalizedEvent(reason, finalPayload);
            }
            trackerState.session = null;
            stopFlushTimer();
            return sent;
        }

        function ensureLifecycleBound() {
            if (trackerState.lifecycleBound) return;
            trackerState.lifecycleBound = true;

            const handleHide = () => {
                if (!trackerState.session) return;
                closeSession('pagehide', {
                    current_time: trackerState.session.max_position_seconds,
                    duration: trackerState.session.duration_seconds,
                });
            };

            window.addEventListener('pagehide', handleHide);
            window.addEventListener('beforeunload', handleHide);
            window.addEventListener('unload', handleHide);
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'hidden' && trackerState.session) {
                    handleHide();
                }
            });
        }

        return {
            beginSession,
            syncProgress,
            flushSession,
            closeSession,
            getCurrentSession() {
                return trackerState.session;
            },
        };
    }

    window.NTSListeningTracker = window.NTSListeningTracker || createListeningTracker();

    // Player state - persists across page navigation
    const state = {
        isPlaying: false,
        isMuted: false,
        volume: 100,
        currentVideoId: null,
        player: null,
        duration: 0,
        currentTime: 0,
        isDragging: false,
        currentTrackIndex: -1,
        tracks: [],
        autoplay: false,
        progressInterval: null,
        apiReady: false,
        pendingVideoData: null, // Store video data if API not ready yet
        currentArtist: '',
        currentTitle: '',
        currentLikeId: null, // ID of the like if current track is liked
        isLiked: false,
        sourceUrl: null, // URL to navigate to when clicking track title
        playTrackCallback: null, // Callback function for playing tracks (used by discover page)
        usesCallbackNavigation: false, // Whether to use callback for prev/next
        playbackContext: null,
        isClosing: false,
    };

    // YouTube API loading state
    let apiLoading = false;

    /**
     * Load YouTube IFrame API if not already loaded
     */
    function loadYouTubeAPI() {
        if (state.apiReady || apiLoading) return;
        if (typeof YT !== 'undefined' && YT.Player) {
            state.apiReady = true;
            onAPIReady();
            return;
        }

        apiLoading = true;
        const tag = document.createElement('script');
        tag.src = 'https://www.youtube.com/iframe_api';
        const firstScriptTag = document.getElementsByTagName('script')[0];
        firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);

        // Set up global callback
        window.onYouTubeIframeAPIReady = function() {
            state.apiReady = true;
            apiLoading = false;
            onAPIReady();
        };
    }

    /**
     * Called when YouTube API is ready
     */
    function onAPIReady() {
        console.log('[YouTubePlayerGlobal] API ready');
        // If we had a pending video, play it now
        if (state.pendingVideoData) {
            const { videoData, artist, title, trackList, trackIndex, sourceUrl, playTrackCallback, playbackContext } = state.pendingVideoData;
            state.pendingVideoData = null;
            showPlayer(videoData, artist, title, trackList, trackIndex, sourceUrl, playTrackCallback, playbackContext);
        }
    }

    /**
     * Show the YouTube player with a video
     * @param {Object} videoData - { video_id, thumbnail, video_url }
     * @param {string} artist - Artist name
     * @param {string} title - Track title
     * @param {Array} [trackList] - Optional list of tracks for prev/next navigation
     * @param {number} [trackIndex] - Optional current track index
     * @param {string} [sourceUrl] - Optional URL to navigate to when clicking title
     * @param {Function} [playTrackCallback] - Optional callback for playing tracks by index
     * @param {Object} [playbackContext] - Optional listening context metadata
     */
    function showPlayer(videoData, artist, title, trackList, trackIndex, sourceUrl, playTrackCallback, playbackContext) {
        console.log('[YouTubePlayerGlobal] showPlayer:', artist, '-', title);

        // Store current track info
        state.currentArtist = artist;
        state.currentTitle = title;
        state.isLiked = false;
        state.currentLikeId = null;
        state.sourceUrl = sourceUrl || null;
        state.playbackContext = arguments.length >= 8 ? (playbackContext || null) : null;
        
        // Store callback for track navigation (used by discover page)
        state.playTrackCallback = playTrackCallback || null;
        state.usesCallbackNavigation = !!playTrackCallback;

        // Close NTS audio player if it's open to prevent conflicts
        const ntsPlayer = document.getElementById('nts-audio-player');
        if (ntsPlayer && ntsPlayer.classList.contains('visible')) {
            const closeButton = ntsPlayer.querySelector('.nts-audio-player-close');
            if (closeButton) closeButton.click();
        }

        window.NTSListeningTracker.beginSession({
            kind: 'track',
            player: 'youtube',
            source_page: window.location.pathname || '',
            source_url: window.location.href || '',
            artist,
            title,
            track_artist: artist,
            track_title: title,
            video_id: videoData.video_id || '',
            video_url: videoData.video_url || '',
            ...state.playbackContext,
        });

        // If API not ready yet, queue the video
        if (!state.apiReady) {
            state.pendingVideoData = { videoData, artist, title, trackList, trackIndex, sourceUrl, playTrackCallback, playbackContext: state.playbackContext };
            loadYouTubeAPI();
            return;
        }

        // Use passed trackList if provided, otherwise try to rebuild from DOM
        if (trackList && trackList.length > 0) {
            state.tracks = trackList.map((t, i) => ({
                artist: t.artist || '',
                title: t.title || '',
                trackId: t.trackId || t.track_id || '',
                index: t.index !== undefined ? t.index : i,
                element: null // No DOM element for callback-based tracks
            }));
            state.currentTrackIndex = typeof trackIndex === 'number' ? trackIndex : 0;
        } else {
            // Fallback: rebuild track list from current page's YouTube buttons
            rebuildTrackList();
            // Find current track index in the list
            state.currentTrackIndex = state.tracks.findIndex(
                track => track.artist === artist && track.title === title
            );
        }
        
        // Check if current track is liked
        checkIfTrackLiked(artist, title);

        // Get or create player container
        let playerEl = document.getElementById('youtube-player');
        if (!playerEl) {
            playerEl = createPlayerElement(videoData, artist, title);
            const root = document.getElementById('youtube-player-root') || document.body;
            root.appendChild(playerEl);
            addEventListeners(playerEl);
        } else {
            updatePlayerInfo(playerEl, videoData, artist, title);
        }

        // Show player with animation
        setTimeout(() => playerEl.classList.add('visible'), 10);

        // Load video
        if (state.player && state.currentVideoId !== videoData.video_id) {
            state.player.loadVideoById(videoData.video_id);
            state.currentVideoId = videoData.video_id;
        } else if (!state.player) {
            createYouTubePlayer(videoData.video_id);
        }
    }

    /**
     * Rebuild the track list from current page's YouTube buttons
     */
    function rebuildTrackList() {
        const trackButtons = document.querySelectorAll('.track-youtube-btn');
        if (trackButtons.length > 0) {
            state.tracks = Array.from(trackButtons).map(button => ({
                artist: button.dataset.artist || '',
                title: button.dataset.title || '',
                trackId: button.dataset.trackId || '',
                element: button
            }));
        }
        // If no buttons found, keep existing list for next/prev to work
    }

    /**
     * Reset the track list (called when generating new mixtape, etc.)
     */
    function resetTrackList() {
        state.tracks = [];
        state.currentTrackIndex = -1;
        state.playTrackCallback = null;
        state.usesCallbackNavigation = false;
    }

    /**
     * Set the track list externally (for discover page updates like Load More)
     * @param {Array} trackList - Array of { artist, title } objects
     * @param {number} [currentIndex] - Current track index (optional)
     */
    function setTrackList(trackList, currentIndex) {
        if (trackList && trackList.length > 0) {
            state.tracks = trackList.map((t, i) => ({
                artist: t.artist || '',
                title: t.title || '',
                trackId: t.trackId || t.track_id || '',
                index: t.index !== undefined ? t.index : i,
                element: null
            }));
            if (typeof currentIndex === 'number') {
                state.currentTrackIndex = currentIndex;
            }
        }
    }

    /**
     * Set the callback for playing tracks by index
     * @param {Function} callback - Function that takes track index as parameter
     */
    function setPlayTrackCallback(callback) {
        state.playTrackCallback = callback;
        state.usesCallbackNavigation = !!callback;
    }

    /**
     * Create player DOM element
     */
    function createPlayerElement(videoData, artist, title) {
        const player = document.createElement('div');
        player.id = 'youtube-player';
        player.className = 'youtube-player';

        const hasSourceUrl = !!state.sourceUrl;
        player.innerHTML = `
            <div class="youtube-player-header">
                <div class="youtube-player-thumbnail" style="background-image: url('${videoData.thumbnail || ''}')"></div>
                <div class="youtube-player-info ${hasSourceUrl ? 'clickable' : ''}">
                    <div class="youtube-player-title">${escapeHtml(title)}</div>
                    <div class="youtube-player-channel">${escapeHtml(artist)}</div>
                </div>
                <div class="youtube-player-center-controls">
                    <div class="youtube-player-prev-next-controls">
                        <button class="youtube-player-prev" title="Previous Track">
                            <i class="fas fa-step-backward"></i>
                        </button>
                        <button class="youtube-player-play-pause" title="Play/Pause">
                            <i class="fas fa-play"></i>
                        </button>
                        <button class="youtube-player-next" title="Next Track">
                            <i class="fas fa-step-forward"></i>
                        </button>
                    </div>
                    <div class="youtube-player-progress-container">
                        <div class="youtube-player-progress-bar"></div>
                        <div class="youtube-player-progress-handle"></div>
                        <div class="youtube-player-progress-indicator">0:00</div>
                    </div>
                </div>
                <div class="youtube-player-controls">
                    <div class="youtube-player-time">0:00 / 0:00</div>
                    <button class="youtube-player-like" title="Add to Liked Tracks">
                        <i class="far fa-heart"></i>
                    </button>
                    <div class="youtube-player-volume-container">
                        <button class="youtube-player-volume" title="Mute/Unmute">
                            <i class="fas fa-volume-up"></i>
                        </button>
                        <div class="youtube-player-volume-slider-container">
                            <input type="range" class="youtube-player-volume-slider" min="0" max="100" value="100">
                        </div>
                    </div>
                    <button class="youtube-player-autoplay-toggle ${state.autoplay ? 'active' : ''}" title="Toggle autoplay">
                        <i class="fas fa-sync-alt" style="opacity: ${state.autoplay ? '1' : '0.5'}"></i>
                    </button>
                    <button class="youtube-player-close" title="Close">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
            </div>
            <div class="youtube-player-container">
                <div id="youtube-iframe-container"></div>
            </div>
        `;

        return player;
    }

    /**
     * Update existing player info
     */
    function updatePlayerInfo(playerEl, videoData, artist, title) {
        playerEl.querySelector('.youtube-player-title').textContent = title;
        playerEl.querySelector('.youtube-player-channel').textContent = artist;
        playerEl.querySelector('.youtube-player-thumbnail').style.backgroundImage = `url('${videoData.thumbnail || ''}')`;
        
        // Update clickable state based on sourceUrl
        const infoEl = playerEl.querySelector('.youtube-player-info');
        if (infoEl) {
            if (state.sourceUrl) {
                infoEl.classList.add('clickable');
            } else {
                infoEl.classList.remove('clickable');
            }
        }
        
        playerEl.classList.add('visible');
    }

    /**
     * Add event listeners to player controls
     */
    function addEventListeners(playerEl) {
        // Play/Pause
        playerEl.querySelector('.youtube-player-play-pause').addEventListener('click', togglePlayPause);

        // Volume
        playerEl.querySelector('.youtube-player-volume').addEventListener('click', toggleMute);
        playerEl.querySelector('.youtube-player-volume-slider').addEventListener('input', handleVolumeChange);

        // Thumbnail expand
        playerEl.querySelector('.youtube-player-thumbnail').addEventListener('click', toggleExpand);

        // Title/info click - navigate to source
        playerEl.querySelector('.youtube-player-info').addEventListener('click', navigateToSource);

        // Close
        playerEl.querySelector('.youtube-player-close').addEventListener('click', closePlayer);

        // Previous/Next
        playerEl.querySelector('.youtube-player-prev').addEventListener('click', playPreviousTrack);
        playerEl.querySelector('.youtube-player-next').addEventListener('click', playNextTrack);

        // Autoplay toggle
        playerEl.querySelector('.youtube-player-autoplay-toggle').addEventListener('click', toggleAutoplay);

        // Like button
        playerEl.querySelector('.youtube-player-like').addEventListener('click', toggleLike);

        // Progress bar scrubbing
        const progressContainer = playerEl.querySelector('.youtube-player-progress-container');
        progressContainer.addEventListener('mousedown', startScrubbing);
        progressContainer.addEventListener('touchstart', startScrubbing, { passive: true });
        progressContainer.addEventListener('mousemove', updateProgressIndicator);
        document.addEventListener('mousemove', continueScrubbing);
        document.addEventListener('touchmove', continueScrubbing, { passive: true });
        document.addEventListener('mouseup', endScrubbing);
        document.addEventListener('touchend', endScrubbing);
    }

    /**
     * Create YT.Player instance
     */
    function createYouTubePlayer(videoId) {
        const container = document.getElementById('youtube-iframe-container');
        if (!container) return;

        container.innerHTML = '';
        state.player = new YT.Player(container, {
            host: 'https://www.youtube.com',
            videoId: videoId,
            playerVars: {
                autoplay: 1,
                modestbranding: 1,
                rel: 0,
                playsinline: 1,
                fs: 1,
                origin: window.location.origin
            },
            events: {
                'onReady': onPlayerReady,
                'onStateChange': onPlayerStateChange
            }
        });
        state.currentVideoId = videoId;
    }

    function onPlayerReady(event) {
        event.target.playVideo();
        state.isPlaying = true;
        updatePlayPauseButton();
        startProgressTracking();
        window.NTSListeningTracker.syncProgress({
            current_time: 0,
            duration: state.duration || 0,
            is_playing: true,
        });
    }

    function onPlayerStateChange(event) {
        if (state.isClosing) {
            return;
        }
        if (event.data === YT.PlayerState.PLAYING) {
            state.isPlaying = true;
            window.NTSListeningTracker.syncProgress({
                current_time: state.currentTime || 0,
                duration: state.duration || 0,
                is_playing: true,
            });
        } else if (event.data === YT.PlayerState.PAUSED) {
            state.isPlaying = false;
            window.NTSListeningTracker.syncProgress({
                current_time: state.currentTime || 0,
                duration: state.duration || 0,
                is_playing: false,
            });
        } else if (event.data === YT.PlayerState.ENDED) {
            state.isPlaying = false;
            window.NTSListeningTracker.syncProgress({
                current_time: state.duration || state.currentTime || 0,
                duration: state.duration || 0,
                is_playing: false,
                ended: true,
            });
            window.NTSListeningTracker.closeSession('ended', {
                current_time: state.duration || state.currentTime || 0,
                duration: state.duration || 0,
            });
            if (state.autoplay) {
                playNextTrack();
            }
        }
        updatePlayPauseButton();
    }

    function updatePlayPauseButton() {
        const btn = document.querySelector('.youtube-player-play-pause i');
        if (btn) {
            btn.className = state.isPlaying ? 'fas fa-pause' : 'fas fa-play';
        }
    }

    function togglePlayPause() {
        if (!state.player) return;
        if (state.isPlaying) {
            state.player.pauseVideo();
            state.isPlaying = false;
        } else {
            state.player.playVideo();
            state.isPlaying = true;
        }
        updatePlayPauseButton();
    }

    function toggleMute() {
        if (!state.player) return;
        const volumeButton = document.querySelector('.youtube-player-volume i');
        const volumeSlider = document.querySelector('.youtube-player-volume-slider');
        if (!volumeButton || !volumeSlider) return;

        if (state.isMuted) {
            state.player.unMute();
            state.player.setVolume(state.volume);
            volumeButton.className = state.volume > 50 ? 'fas fa-volume-up' : 'fas fa-volume-down';
            volumeSlider.value = state.volume;
            state.isMuted = false;
        } else {
            state.player.mute();
            volumeButton.className = 'fas fa-volume-mute';
            state.isMuted = true;
        }
    }

    function handleVolumeChange(e) {
        if (!state.player) return;
        const volume = parseInt(e.target.value, 10);
        state.volume = volume;
        state.player.setVolume(volume);

        const volumeButton = document.querySelector('.youtube-player-volume i');
        if (volumeButton) {
            if (volume === 0) {
                volumeButton.className = 'fas fa-volume-mute';
                state.isMuted = true;
            } else {
                volumeButton.className = volume > 50 ? 'fas fa-volume-up' : 'fas fa-volume-down';
                state.isMuted = false;
                state.player.unMute();
            }
        }
    }

    function toggleExpand() {
        const playerEl = document.getElementById('youtube-player');
        if (playerEl) {
            playerEl.classList.toggle('expanded');
            const thumb = playerEl.querySelector('.youtube-player-thumbnail');
            if (playerEl.classList.contains('expanded')) {
                thumb.style.transform = 'scale(0.95)';
                thumb.title = 'Collapse player';
            } else {
                thumb.style.transform = '';
                thumb.title = 'Expand player';
            }
        }
    }

    /**
     * Navigate to the source page (show/episode) when clicking on title
     */
    function navigateToSource(e) {
        e.stopPropagation();
        if (!state.sourceUrl) {
            console.log('[YouTubePlayerGlobal] No sourceUrl set');
            return;
        }
        
        console.log('[YouTubePlayerGlobal] Navigating to:', state.sourceUrl);
        window.location.href = state.sourceUrl;
    }

    function closePlayer() {
        const playerEl = document.getElementById('youtube-player');
        if (playerEl) {
            playerEl.classList.remove('visible');
            state.isClosing = true;
            setTimeout(() => {
                window.NTSListeningTracker.closeSession('close', {
                    current_time: state.currentTime || 0,
                    duration: state.duration || 0,
                });
                if (state.player) {
                    state.player.stopVideo();
                }
                if (state.progressInterval) {
                    clearInterval(state.progressInterval);
                    state.progressInterval = null;
                }
                state.isPlaying = false;
                state.currentVideoId = null;
                state.player = null;
                state.duration = 0;
                state.currentTime = 0;
                state.isClosing = false;
                if (playerEl.parentNode) {
                    playerEl.parentNode.removeChild(playerEl);
                }
            }, 300);
        }
    }

    function playPreviousTrack() {
        if (state.tracks.length === 0 || state.currentTrackIndex <= 0) return;
        
        const prevIndex = state.currentTrackIndex - 1;
        
        // Use callback if available (discover page)
        if (state.usesCallbackNavigation && state.playTrackCallback) {
            state.playTrackCallback(prevIndex);
            return;
        }
        
        // Fallback: rebuild track list and click element
        rebuildTrackList();
        const prevTrack = state.tracks[prevIndex];
        if (prevTrack && prevTrack.element) {
            prevTrack.element.click();
        }
    }

    function playNextTrack() {
        if (state.tracks.length === 0 || state.currentTrackIndex >= state.tracks.length - 1) return;
        
        const nextIndex = state.currentTrackIndex + 1;
        
        // Use callback if available (discover page)
        if (state.usesCallbackNavigation && state.playTrackCallback) {
            state.playTrackCallback(nextIndex);
            return;
        }
        
        // Fallback: rebuild track list and click element
        rebuildTrackList();
        const nextTrack = state.tracks[nextIndex];
        if (nextTrack && nextTrack.element) {
            nextTrack.element.click();
        }
    }

    function toggleAutoplay() {
        state.autoplay = !state.autoplay;
        const btn = document.querySelector('.youtube-player-autoplay-toggle');
        const icon = btn ? btn.querySelector('i') : null;
        if (btn && icon) {
            if (state.autoplay) {
                btn.classList.add('active');
                icon.style.opacity = '1';
            } else {
                btn.classList.remove('active');
                icon.style.opacity = '0.5';
            }
        }
    }

    /**
     * Check if current track is liked
     */
    async function checkIfTrackLiked(artist, title) {
        try {
            const res = await fetch('/api/likes/check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tracks: [{ artist, title }] })
            });
            const data = await res.json();
            if (data.success && data.liked) {
                // API returns format: { liked: { "artist|||title": { liked: bool, id: int } } }
                const key = `${(artist || '').trim().toLowerCase()}|||${(title || '').trim().toLowerCase()}`;
                const result = data.liked[key];
                if (result) {
                    state.isLiked = result.liked;
                    state.currentLikeId = result.id || null;
                } else {
                    state.isLiked = false;
                    state.currentLikeId = null;
                }
                updateLikeButton();
            }
        } catch (err) {
            console.error('[YouTubePlayerGlobal] Error checking like status:', err);
        }
    }

    /**
     * Toggle like state for current track
     */
    async function toggleLike() {
        if (!state.currentArtist && !state.currentTitle) return;
        
        const btn = document.querySelector('.youtube-player-like');
        if (btn) btn.disabled = true;
        
        try {
            if (state.isLiked && state.currentLikeId) {
                // Unlike
                const res = await fetch(`/api/likes/${state.currentLikeId}`, { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    state.isLiked = false;
                    state.currentLikeId = null;
                    if (window.showNotification) {
                        showNotification('Removed from Liked Tracks', 'success');
                    }
                }
            } else {
                // Like
                const res = await fetch('/api/likes', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        artist: state.currentArtist,
                        title: state.currentTitle
                    })
                });
                const data = await res.json();
                if (data.success) {
                    state.isLiked = true;
                    state.currentLikeId = data.like_id;
                    if (window.showNotification) {
                        showNotification('Added to Liked Tracks', 'success');
                    }
                } else if (data.message) {
                    if (window.showNotification) {
                        showNotification(data.message, 'info');
                    }
                    // Track might already be liked, update state
                    if (data.message.includes('already liked') || data.message.includes('Already')) {
                        state.isLiked = true;
                        state.currentLikeId = data.like_id;
                    }
                }
            }
            updateLikeButton();
        } catch (err) {
            console.error('[YouTubePlayerGlobal] Error toggling like:', err);
            if (window.showNotification) {
                showNotification('Failed to update like', 'error');
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    /**
     * Update like button visual state
     */
    function updateLikeButton() {
        const btn = document.querySelector('.youtube-player-like');
        if (!btn) return;
        
        const icon = btn.querySelector('i');
        if (icon) {
            if (state.isLiked) {
                icon.className = 'fas fa-heart'; // Filled heart
                btn.classList.add('liked');
                btn.title = 'Remove from Liked Tracks';
            } else {
                icon.className = 'far fa-heart'; // Outline heart
                btn.classList.remove('liked');
                btn.title = 'Add to Liked Tracks';
            }
        }
    }

    function startProgressTracking() {
        if (state.progressInterval) {
            clearInterval(state.progressInterval);
        }
        state.progressInterval = setInterval(updateProgress, 500);
    }

    function updateProgress() {
        if (!state.player || state.isDragging) return;
        try {
            const currentTime = state.player.getCurrentTime() || 0;
            const duration = state.player.getDuration() || 0;
            state.currentTime = currentTime;
            state.duration = duration;
            window.NTSListeningTracker.syncProgress({
                current_time: currentTime,
                duration,
                is_playing: state.isPlaying,
            });

            const progressBar = document.querySelector('.youtube-player-progress-bar');
            const progressHandle = document.querySelector('.youtube-player-progress-handle');
            const timeDisplay = document.querySelector('.youtube-player-time');

            if (progressBar && duration > 0) {
                const percent = (currentTime / duration) * 100;
                progressBar.style.width = `${percent}%`;
                if (progressHandle) {
                    progressHandle.style.left = `${percent}%`;
                }
                if (timeDisplay) {
                    timeDisplay.textContent = `${formatTime(currentTime)} / ${formatTime(duration)}`;
                }
            }
        } catch (err) {
            console.error('[YouTubePlayerGlobal] Error updating progress:', err);
        }
    }

    function formatTime(seconds) {
        seconds = Math.floor(seconds);
        const minutes = Math.floor(seconds / 60);
        seconds = seconds % 60;
        return `${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;
    }

    function startScrubbing(e) {
        e.preventDefault();
        state.isDragging = true;
        continueScrubbing(e);
    }

    function continueScrubbing(e) {
        if (!state.isDragging) return;
        const progressContainer = document.querySelector('.youtube-player-progress-container');
        const progressBar = document.querySelector('.youtube-player-progress-bar');
        const progressHandle = document.querySelector('.youtube-player-progress-handle');
        const progressIndicator = document.querySelector('.youtube-player-progress-indicator');
        if (!progressContainer || !progressBar) return;

        let clientX;
        if (e.type.includes('touch')) {
            clientX = e.touches[0].clientX;
        } else {
            clientX = e.clientX;
        }

        const rect = progressContainer.getBoundingClientRect();
        let percent = (clientX - rect.left) / rect.width;
        percent = Math.max(0, Math.min(1, percent));

        progressBar.style.width = `${percent * 100}%`;
        if (progressHandle) progressHandle.style.left = `${percent * 100}%`;
        if (progressIndicator) {
            progressIndicator.style.left = `${percent * 100}%`;
            progressIndicator.textContent = formatTime(percent * state.duration);
        }

        const timeDisplay = document.querySelector('.youtube-player-time');
        if (timeDisplay && state.duration) {
            timeDisplay.textContent = `${formatTime(percent * state.duration)} / ${formatTime(state.duration)}`;
        }
    }

    function endScrubbing(e) {
        if (!state.isDragging) return;
        state.isDragging = false;

        const progressContainer = document.querySelector('.youtube-player-progress-container');
        if (!progressContainer || !state.player) return;

        let clientX;
        if (e.type.includes('touch')) {
            clientX = e.changedTouches[0].clientX;
        } else {
            clientX = e.clientX;
        }

        const rect = progressContainer.getBoundingClientRect();
        let percent = (clientX - rect.left) / rect.width;
        percent = Math.max(0, Math.min(1, percent));

        const newTime = percent * state.duration;
        state.player.seekTo(newTime, true);
    }

    function updateProgressIndicator(e) {
        if (!state.duration) return;
        const progressContainer = document.querySelector('.youtube-player-progress-container');
        const progressIndicator = document.querySelector('.youtube-player-progress-indicator');
        if (!progressContainer || !progressIndicator) return;

        const rect = progressContainer.getBoundingClientRect();
        let percent = (e.clientX - rect.left) / rect.width;
        percent = Math.max(0, Math.min(1, percent));

        progressIndicator.style.left = `${percent * 100}%`;
        progressIndicator.textContent = formatTime(percent * state.duration);
    }

    /**
     * Utility: Escape HTML entities
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }

    /**
     * Search YouTube and play the first result
     * @param {string} query - Search query (e.g., "Artist - Track Title")
     */
    async function searchAndPlay(query) {
        if (!query || !query.trim()) return;

        console.log('[YouTubePlayerGlobal] searchAndPlay:', query);
        
        // Parse query to extract artist and title
        let artist = '';
        let title = query;
        if (query.includes(' - ')) {
            const parts = query.split(' - ');
            artist = parts[0].trim();
            title = parts.slice(1).join(' - ').trim();
        }

        try {
            const response = await fetch('/search_youtube', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artist, title })
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            
            if (data.success && data.video_id) {
                showPlayer(data, artist, title, null, null, null, null, null);
            } else {
                // Fallback: open YouTube search in new tab
                console.log('[YouTubePlayerGlobal] No video found, opening YouTube search');
                window.open(`https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`, '_blank');
            }
        } catch (error) {
            console.error('[YouTubePlayerGlobal] searchAndPlay error:', error);
            // Fallback: open YouTube search in new tab
            window.open(`https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`, '_blank');
        }
    }

    /**
     * Check if player is currently visible/active
     */
    function isPlayerVisible() {
        const playerEl = document.getElementById('youtube-player');
        return playerEl && playerEl.classList.contains('visible');
    }

    /**
     * Get current playback state
     */
    function getPlaybackState() {
        return {
            isPlaying: state.isPlaying,
            currentTrack: state.tracks[state.currentTrackIndex] || null,
            currentTime: state.currentTime,
            duration: state.duration,
            videoId: state.currentVideoId
        };
    }

    // =============================================
    // Public API
    // =============================================
    window.YouTubePlayerGlobal = {
        init: function() {
            console.log('[YouTubePlayerGlobal] Initialized');
            // Pre-load YouTube API
            loadYouTubeAPI();
        },
        showPlayer: showPlayer,
        closePlayer: closePlayer,
        togglePlayPause: togglePlayPause,
        playNext: playNextTrack,
        playPrevious: playPreviousTrack,
        isVisible: isPlayerVisible,
        getState: getPlaybackState,
        resetTrackList: resetTrackList,
        rebuildTrackList: rebuildTrackList,
        setTrackList: setTrackList,
        setPlayTrackCallback: setPlayTrackCallback,
        searchAndPlay: searchAndPlay
    };

    // Legacy compatibility - expose showYouTubePlayer globally
    window.showYouTubePlayer = showPlayer;
    window.resetYouTubeTrackList = resetTrackList;
    window.setYouTubeTrackList = setTrackList;

})();
