/* global showNotification */

(function () {
  'use strict';

  // Track initialization state
  let initialized = false;
  
  // Track resources that need cleanup during SPA navigation
  let likesPageCleanupResources = {
    mutationObserver: null,
    checkForPlayerInterval: null,
    checkForPlayerTimeout: null,
  };
  
  // Cleanup function for SPA navigation - prevents memory leaks
  function cleanupLikesPage() {
    if (likesPageCleanupResources.mutationObserver) {
      likesPageCleanupResources.mutationObserver.disconnect();
      likesPageCleanupResources.mutationObserver = null;
    }
    if (likesPageCleanupResources.checkForPlayerInterval) {
      clearInterval(likesPageCleanupResources.checkForPlayerInterval);
      likesPageCleanupResources.checkForPlayerInterval = null;
    }
    if (likesPageCleanupResources.checkForPlayerTimeout) {
      clearTimeout(likesPageCleanupResources.checkForPlayerTimeout);
      likesPageCleanupResources.checkForPlayerTimeout = null;
    }
  }
  
  // Expose cleanup function for SPA router
  window._likesPageCleanup = cleanupLikesPage;
  
  // Main initialization function
  function initLikesPage() {
    // Only run on likes page
    const playlistList = document.getElementById('playlistList');
    if (!playlistList) return;
    
    initLikesPageCore();
  }
  
  window.initLikesPageHandlers = initLikesPage;

  if (window.NTSPageModules && typeof window.NTSPageModules.register === 'function') {
    window.NTSPageModules.register('likes', {
      init: initLikesPage,
      cleanup: cleanupLikesPage,
    });
  }

  function buildTrackPlaybackContext(button) {
    return {
      kind: 'track',
      player: 'youtube',
      source_page: window.location.pathname || '',
      source_url: window.location.href || '',
      artist: button?.dataset.artist || '',
      title: button?.dataset.title || '',
      track_artist: button?.dataset.artist || '',
      track_title: button?.dataset.title || '',
      episode_url: button?.dataset.episodeUrl || '',
      episode_title: button?.dataset.episodeTitle || '',
      show_url: button?.dataset.showUrl || '',
      show_title: button?.dataset.showTitle || '',
    };
  }

  function buildEpisodePlaybackContext(playBtn) {
    return {
      kind: 'episode',
      player: 'nts_audio',
      source_page: window.location.pathname || '',
      source_url: window.location.href || '',
      episode_url: playBtn?.dataset.episodeUrl || '',
      episode_title: playBtn?.dataset.episodeTitle || '',
      episode_date: playBtn?.dataset.episodeDate || '',
      episode_image: playBtn?.dataset.episodeImage || '',
      show_url: playBtn?.dataset.showUrl || '',
      show_title: playBtn?.dataset.showTitle || '',
    };
  }
  
  function initLikesPageCore() {
  const playlistList = document.getElementById('playlistList');
  const state = {
    likes: [],
    likedEpisodes: [],
    playlists: [],
    currentPlaylistId: 'all', // 'all' for all likes, 'episodes' for episodes, or playlist ID
    searchQuery: '',
  };

  // DOM Elements
  const likedTracksList = document.getElementById('likedTracksList');
  const emptyState = document.getElementById('emptyState');
  const currentPlaylistName = document.getElementById('currentPlaylistName');
  const currentTrackCount = document.getElementById('currentTrackCount');
  const allLikedCount = document.getElementById('allLikedCount');
  const playlistActions = document.getElementById('playlistActions');
  const likesSearch = document.getElementById('likesSearch');

  // Buttons
  const createPlaylistBtn = document.getElementById('createPlaylistBtn');
  const renamePlaylistBtn = document.getElementById('renamePlaylistBtn');
  const deletePlaylistBtn = document.getElementById('deletePlaylistBtn');
  const playAllBtn = document.getElementById('playAllBtn');
  
  // Track currently playing for highlighting
  let currentlyPlayingElement = null;

  // ==========================================
  // Event Delegation for YouTube Buttons
  // Industry-standard approach for dynamic content
  // ==========================================
  if (likedTracksList && !likedTracksList.__youtubeClickBound) {
    likedTracksList.__youtubeClickBound = true;
    likedTracksList.addEventListener('click', handleYouTubeButtonClick);
  }

  async function handleYouTubeButtonClick(e) {
    // Find the YouTube button that was clicked (handles clicks on the button or its child icon)
    const youtubeBtn = e.target.closest('.youtube-btn, .track-youtube-btn');
    if (!youtubeBtn) return;
    
    e.stopPropagation();
    if (youtubeBtn.classList.contains('searching')) return;
    
    const artist = youtubeBtn.dataset.artist;
    const title = youtubeBtn.dataset.title;
    if (!artist && !title) return;
    
    // Find the parent track item for highlighting
    const trackItem = youtubeBtn.closest('.liked-track-item');
    
    try {
      youtubeBtn.classList.add('searching');
      const resp = await fetch('/search_youtube', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artist, title })
      });
      
      if (!resp.ok) throw new Error('Failed to search YouTube');
      
      const data = await resp.json();
      if (data.success) {
        if (data.search_only) {
          showNotification(`Searching YouTube for "${artist} - ${title}"`, 'info');
          window.open(data.video_url, '_blank');
        } else {
          // Highlight this track as playing
          if (trackItem) {
            highlightPlayingTrack(trackItem);
          }
          
          if (typeof window.showYouTubePlayer === 'function') {
            window.showYouTubePlayer(data, artist, title, null, null, window.location.pathname, null, buildTrackPlaybackContext(youtubeBtn));
          } else {
            window.open(data.video_url || (data.video_id ? `https://www.youtube.com/watch?v=${data.video_id}` : '#'), '_blank');
          }
        }
      } else {
        if (data.quota_exceeded) {
          showNotification('YouTube API daily quota exceeded. Please try again tomorrow.', 'error', 10000);
        } else {
          showNotification(data.message || 'Failed to search on YouTube', 'error');
        }
      }
    } catch (err) {
      showNotification('Failed to search on YouTube', 'error');
    } finally {
      youtubeBtn.classList.remove('searching');
    }
  }

  // Modals
  const renameModal = document.getElementById('renameModal');
  const createPlaylistModal = document.getElementById('createPlaylistModal');

  // ==========================================
  // API Functions
  // ==========================================

  async function fetchLikes() {
    try {
      const res = await fetch('/api/likes');
      const data = await res.json();
      if (data.success) {
        state.likes = data.likes || [];
      }
    } catch (err) {
      console.error('Failed to fetch likes:', err);
    }
  }

  async function fetchPlaylists() {
    try {
      const res = await fetch('/api/user_playlists');
      const data = await res.json();
      if (data.success) {
        state.playlists = data.playlists || [];
      }
    } catch (err) {
      console.error('Failed to fetch playlists:', err);
    }
  }

  async function fetchLikedEpisodes() {
    try {
      const res = await fetch('/api/episodes/likes');
      const data = await res.json();
      if (data.success) {
        state.likedEpisodes = data.episodes || [];
      }
    } catch (err) {
      console.error('Failed to fetch liked episodes:', err);
    }
  }

  async function fetchPlaylistTracks(playlistId) {
    try {
      const res = await fetch(`/api/user_playlists/${playlistId}`);
      const data = await res.json();
      if (data.success) {
        return data.playlist;
      }
    } catch (err) {
      console.error('Failed to fetch playlist tracks:', err);
    }
    return null;
  }

  async function unlikeTrack(likeId) {
    try {
      const res = await fetch(`/api/likes/${likeId}`, { method: 'DELETE' });
      const data = await res.json();
      if (data.success) {
        state.likes = state.likes.filter(l => l.id !== likeId);
        return true;
      }
    } catch (err) {
      console.error('Failed to unlike track:', err);
    }
    return false;
  }

  async function createPlaylist(name, description) {
    try {
      const res = await fetch('/api/user_playlists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description })
      });
      const data = await res.json();
      if (data.success) {
        await fetchPlaylists();
        return data.id;
      }
    } catch (err) {
      console.error('Failed to create playlist:', err);
    }
    return null;
  }

  async function updatePlaylist(playlistId, name, description) {
    try {
      const res = await fetch(`/api/user_playlists/${playlistId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description })
      });
      const data = await res.json();
      if (data.success) {
        await fetchPlaylists();
        return true;
      }
    } catch (err) {
      console.error('Failed to update playlist:', err);
    }
    return false;
  }

  async function deletePlaylist(playlistId) {
    try {
      const res = await fetch(`/api/user_playlists/${playlistId}`, { method: 'DELETE' });
      const data = await res.json();
      if (data.success) {
        await fetchPlaylists();
        return true;
      }
    } catch (err) {
      console.error('Failed to delete playlist:', err);
    }
    return false;
  }

  async function addTrackToPlaylist(playlistId, likedTrackId) {
    try {
      const res = await fetch(`/api/user_playlists/${playlistId}/tracks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ liked_track_id: likedTrackId })
      });
      const data = await res.json();
      return data.success;
    } catch (err) {
      console.error('Failed to add track to playlist:', err);
    }
    return false;
  }

  async function removeTrackFromPlaylist(playlistId, playlistTrackId) {
    try {
      const res = await fetch(`/api/user_playlists/${playlistId}/tracks/${playlistTrackId}`, {
        method: 'DELETE'
      });
      const data = await res.json();
      return data.success;
    } catch (err) {
      console.error('Failed to remove track from playlist:', err);
    }
    return false;
  }

  // ==========================================
  // Render Functions
  // ==========================================

  function renderPlaylists() {
    // Keep the "All Liked" and "Episodes" items, add dynamic playlists after divider
    const allLikedItem = playlistList.querySelector('[data-playlist-id="all"]');
    const episodesItem = playlistList.querySelector('[data-playlist-id="episodes"]');
    const divider = playlistList.querySelector('.playlist-divider');
    
    // Remove all dynamic playlist items (after divider)
    playlistList.querySelectorAll('.playlist-item:not([data-playlist-id="all"]):not([data-playlist-id="episodes"])').forEach(el => el.remove());

    // Add playlists after divider
    state.playlists.forEach(p => {
      const item = document.createElement('div');
      item.className = 'playlist-item' + (state.currentPlaylistId === p.id ? ' active' : '');
      item.dataset.playlistId = p.id;
      item.innerHTML = `
        <i class="fas fa-list"></i>
        <span class="playlist-name">${escapeHtml(p.name)}</span>
        <span class="playlist-count">${p.track_count || 0}</span>
      `;
      item.addEventListener('click', () => selectPlaylist(p.id));
      
      // Setup drop zone for drag and drop
      setupPlaylistDropZone(item, p.id);
      
      playlistList.appendChild(item);
    });

    // Update counts
    allLikedCount.textContent = state.likes.length;
    const likedEpisodesCount = document.getElementById('likedEpisodesCount');
    if (likedEpisodesCount) {
      likedEpisodesCount.textContent = state.likedEpisodes.length;
    }
    
    // Update active state
    playlistList.querySelectorAll('.playlist-item').forEach(el => {
      const id = el.dataset.playlistId;
      el.classList.toggle('active', id === String(state.currentPlaylistId));
    });
  }

  function renderTracks(tracks, isPlaylist = false) {
    likedTracksList.innerHTML = '';
    emptyState.style.display = 'none';

    const filtered = filterTracks(tracks);

    if (filtered.length === 0) {
      emptyState.style.display = 'block';
      if (state.searchQuery) {
        emptyState.querySelector('h3').textContent = 'No matching tracks';
        emptyState.querySelector('p').textContent = 'Try a different search term.';
      } else if (isPlaylist) {
        emptyState.querySelector('h3').textContent = 'This playlist is empty';
        emptyState.querySelector('p').textContent = 'Add tracks from your liked songs.';
      } else {
        emptyState.querySelector('h3').textContent = 'No liked tracks yet';
        emptyState.querySelector('p').textContent = 'Click the heart icon on any track to add it to your liked songs.';
      }
      return;
    }

    filtered.forEach((track, index) => {
      const item = document.createElement('div');
      item.className = 'liked-track-item';
      item.dataset.trackId = track.id || track.liked_track_id;
      if (isPlaylist && track.playlist_track_id) {
        item.dataset.playlistTrackId = track.playlist_track_id;
      }

      const artist = track.artist || '';
      const title = track.title || '';
      const showTitle = track.show_title || '';
      const episodeTitle = track.episode_title || '';
      const episodeUrl = track.episode_url || '';
      const showUrl = track.show_url || '';

      // Build episode link - link to our internal show page anchored to the episode
      let episodeLink = '';
      if (episodeUrl && episodeTitle) {
        if (showUrl) {
          // Link to internal show page with episode hash for scrolling
          const episodeSlug = episodeUrl.split('/').pop();
          const internalShowUrl = `/show/${encodeURIComponent(showUrl)}#episode-${episodeSlug}`;
          episodeLink = `<a href="${internalShowUrl}" class="liked-track-episode-link">${escapeHtml(episodeTitle)}</a>`;
        } else {
          // Fallback to external NTS episode link
          episodeLink = `<a href="${escapeHtml(episodeUrl)}" target="_blank">${escapeHtml(episodeTitle)}</a>`;
        }
      } else if (episodeTitle) {
        episodeLink = `<span class="liked-track-episode">${escapeHtml(episodeTitle)}</span>`;
      }

      item.innerHTML = `
        <div class="liked-track-drag-handle"><i class="fas fa-grip-vertical"></i></div>
        <button class="liked-track-like-btn" title="Unlike">
          <i class="fas fa-heart"></i>
        </button>
        <div class="liked-track-info">
          <div class="liked-track-title-row">
            <span class="liked-track-artist">${escapeHtml(artist)}</span>
            <span class="liked-track-separator">—</span>
            <span class="liked-track-title">${escapeHtml(title)}</span>
          </div>
          ${episodeLink ? `
            <div class="liked-track-meta">
              ${episodeLink}
            </div>
          ` : ''}
        </div>
        <div class="liked-track-actions">
          <button class="liked-track-action-btn youtube-btn track-youtube-btn" title="Play on YouTube" data-artist="${escapeHtml(artist)}" data-title="${escapeHtml(title)}" data-show-url="${escapeHtml(showUrl)}" data-show-title="${escapeHtml(showTitle)}" data-episode-url="${escapeHtml(episodeUrl)}" data-episode-title="${escapeHtml(episodeTitle)}">
            <span class="loading-spinner"></span>
            <i class="fab fa-youtube"></i>
          </button>
          ${isPlaylist ? `
            <button class="liked-track-action-btn remove-from-playlist-btn" title="Remove from playlist" data-playlist-track-id="${track.playlist_track_id}">
              <i class="fas fa-times"></i>
            </button>
          ` : ''}
        </div>
      `;

      // Event listeners
      // Note: YouTube button click is handled via event delegation on likedTracksList
      const unlikeBtn = item.querySelector('.liked-track-like-btn');
      unlikeBtn.addEventListener('click', () => handleUnlike(track.id || track.liked_track_id));

      if (isPlaylist) {
        const removeBtn = item.querySelector('.remove-from-playlist-btn');
        removeBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          handleRemoveFromPlaylist(track.playlist_track_id);
        });
      }

      // Setup drag and drop for the track
      setupTrackDragAndDrop(item, track);
      
      // Setup reordering within playlists
      if (isPlaylist) {
        setupPlaylistReorder(item, track, index, filtered);
      }

      likedTracksList.appendChild(item);
    });

    currentTrackCount.textContent = `${filtered.length} track${filtered.length !== 1 ? 's' : ''}`;
  }

  function filterTracks(tracks) {
    if (!state.searchQuery) return tracks;
    const q = state.searchQuery.toLowerCase();
    return tracks.filter(t => {
      const artist = (t.artist || '').toLowerCase();
      const title = (t.title || '').toLowerCase();
      const show = (t.show_title || '').toLowerCase();
      return artist.includes(q) || title.includes(q) || show.includes(q);
    });
  }

  function filterEpisodes(episodes) {
    if (!state.searchQuery) return episodes;
    const q = state.searchQuery.toLowerCase();
    return episodes.filter(ep => {
      const title = (ep.episode_title || '').toLowerCase();
      const show = (ep.show_title || '').toLowerCase();
      return title.includes(q) || show.includes(q);
    });
  }

  function renderEpisodes(episodes) {
    likedTracksList.innerHTML = '';
    emptyState.style.display = 'none';

    const filtered = filterEpisodes(episodes);

    if (filtered.length === 0) {
      emptyState.style.display = 'block';
      if (state.searchQuery) {
        emptyState.querySelector('h3').textContent = 'No matching episodes';
        emptyState.querySelector('p').textContent = 'Try a different search term.';
      } else {
        emptyState.querySelector('h3').textContent = 'No liked episodes yet';
        emptyState.querySelector('p').textContent = 'Click the heart icon on any episode in the player to add it here.';
      }
      return;
    }

    filtered.forEach((episode) => {
      const item = document.createElement('div');
      item.className = 'liked-track-item'; // Use same class as tracks
      item.dataset.episodeId = episode.id;

      const episodeTitle = episode.episode_title || 'Unknown Episode';
      const showTitle = episode.show_title || '';
      const episodeDate = episode.episode_date || '';
      const episodeUrl = episode.episode_url || '';
      const showUrl = episode.show_url || '';
      const imageUrl = episode.image_url || '';

      // Build link to internal show page
      let episodeLink = '';
      if (showUrl && episodeUrl) {
        const episodeSlug = episodeUrl.split('/').pop();
        episodeLink = `${showUrl}#episode-${episodeSlug}`;
      }

      item.innerHTML = `
        <div class="liked-track-drag-handle" style="visibility: hidden;"><i class="fas fa-grip-vertical"></i></div>
        <button class="liked-track-like-btn" title="Unlike">
          <i class="fas fa-heart"></i>
        </button>
        <div class="liked-track-info">
          <div class="liked-track-title-row">
            <span class="liked-track-artist">${escapeHtml(showTitle || 'NTS')}</span>
            <span class="liked-track-separator">—</span>
            <span class="liked-track-title">${escapeHtml(episodeTitle)}</span>
          </div>
          ${episodeDate ? `
            <div class="liked-track-meta">
              <span class="liked-track-episode">${escapeHtml(episodeDate)}</span>
            </div>
          ` : ''}
        </div>
        <div class="liked-track-actions">
          <button class="liked-track-action-btn play-episode-btn" title="Play episode" 
            data-episode-url="${escapeHtml(episodeUrl)}"
            data-episode-title="${escapeHtml(episodeTitle)}"
            data-episode-date="${escapeHtml(episodeDate)}"
            data-episode-image="${escapeHtml(imageUrl)}"
            data-show-url="${escapeHtml(showUrl)}"
            data-show-title="${escapeHtml(showTitle)}">
            <span class="loading-spinner"></span>
            <i class="fas fa-play"></i>
          </button>
          ${episodeLink ? `<a href="${episodeLink}" class="liked-track-action-btn" title="Go to show"><i class="fas fa-external-link-alt"></i></a>` : ''}
        </div>
      `;

      // Unlike button handler
      const unlikeBtn = item.querySelector('.liked-track-like-btn');
      unlikeBtn.addEventListener('click', async () => {
        try {
          const res = await fetch(`/api/episodes/likes/${episode.id}`, { method: 'DELETE' });
          const data = await res.json();
          if (data.success) {
            state.likedEpisodes = state.likedEpisodes.filter(e => e.id !== episode.id);
            showNotification('Episode removed from likes', 'success');
            renderPlaylists();
            renderEpisodes(state.likedEpisodes);
          }
        } catch (err) {
          console.error('Failed to unlike episode:', err);
          showNotification('Failed to remove episode', 'error');
        }
      });

      // Play button handler - use NTS audio player
      const playBtn = item.querySelector('.play-episode-btn');
      playBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (playBtn.classList.contains('loading')) return;
        
        playBtn.classList.add('loading');
        
        try {
          // Highlight this episode as playing
          if (currentlyPlayingElement) {
            currentlyPlayingElement.classList.remove('playing');
          }
          item.classList.add('playing');
          currentlyPlayingElement = item;
          
          // Use NTS audio player
          if (window.ntsAudioPlayer) {
            await window.ntsAudioPlayer.showPlayer({
              url: playBtn.dataset.episodeUrl,
              title: playBtn.dataset.episodeTitle,
              date: playBtn.dataset.episodeDate,
              image: playBtn.dataset.episodeImage,
              show_url: playBtn.dataset.showUrl,
              show_title: playBtn.dataset.showTitle,
              playback_context: buildEpisodePlaybackContext(playBtn)
            });
          } else {
            showNotification('Audio player not available', 'error');
          }
        } catch (err) {
          console.error('Failed to play episode:', err);
          showNotification('Failed to play episode', 'error');
        } finally {
          playBtn.classList.remove('loading');
        }
      });

      likedTracksList.appendChild(item);
    });

    currentTrackCount.textContent = `${filtered.length} episode${filtered.length !== 1 ? 's' : ''}`;
  }

  // ==========================================
  // Event Handlers
  // ==========================================

  function selectPlaylist(playlistId) {
    state.currentPlaylistId = playlistId;
    renderPlaylists();

    if (playlistId === 'all') {
      currentPlaylistName.textContent = 'Liked Tracks';
      playlistActions.style.display = 'none';
      renderTracks(state.likes, false);
    } else if (playlistId === 'episodes') {
      currentPlaylistName.textContent = 'Liked Episodes';
      playlistActions.style.display = 'none';
      renderEpisodes(state.likedEpisodes);
    } else {
      const playlist = state.playlists.find(p => p.id === playlistId);
      currentPlaylistName.textContent = playlist ? playlist.name : 'Playlist';
      playlistActions.style.display = 'flex';
      
      // Fetch and render playlist tracks
      fetchPlaylistTracks(playlistId).then(data => {
        if (data) {
          renderTracks(data.tracks || [], true);
        }
      });
    }
  }

  async function handleUnlike(likeId) {
    if (await unlikeTrack(likeId)) {
      showNotification('Track removed from likes', 'success');
      renderPlaylists();
      
      if (state.currentPlaylistId === 'all') {
        renderTracks(state.likes, false);
      } else {
        // Refresh current playlist view
        selectPlaylist(state.currentPlaylistId);
      }
    }
  }

  async function handleRemoveFromPlaylist(playlistTrackId) {
    if (await removeTrackFromPlaylist(state.currentPlaylistId, playlistTrackId)) {
      showNotification('Track removed from playlist', 'success');
      await fetchPlaylists();
      renderPlaylists();
      selectPlaylist(state.currentPlaylistId);
    }
  }

  // ==========================================
  // Modal Functions
  // ==========================================

  function openModal(modal) {
    modal.classList.add('visible');
    modal.setAttribute('aria-hidden', 'false');
  }

  function closeModal(modal) {
    modal.classList.remove('visible');
    modal.setAttribute('aria-hidden', 'true');
  }

  function setupModals() {
    // Close buttons
    document.querySelectorAll('.modal-close').forEach(btn => {
      btn.addEventListener('click', () => {
        const modal = btn.closest('.modal');
        if (modal) closeModal(modal);
      });
    });

    // Click outside to close
    document.querySelectorAll('.modal').forEach(modal => {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal(modal);
      });
    });

    // Escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        document.querySelectorAll('.modal.visible').forEach(closeModal);
      }
    });

    // Create Playlist Modal
    createPlaylistBtn.addEventListener('click', () => {
      document.getElementById('newPlaylistName').value = '';
      document.getElementById('newPlaylistDescription').value = '';
      openModal(createPlaylistModal);
    });

    document.getElementById('cancelCreatePlaylist').addEventListener('click', () => {
      closeModal(createPlaylistModal);
    });

    document.getElementById('confirmCreatePlaylist').addEventListener('click', async () => {
      const name = document.getElementById('newPlaylistName').value.trim();
      const desc = document.getElementById('newPlaylistDescription').value.trim();
      if (!name) {
        showNotification('Please enter a playlist name', 'error');
        return;
      }
      const id = await createPlaylist(name, desc);
      if (id) {
        showNotification('Playlist created', 'success');
        closeModal(createPlaylistModal);
        renderPlaylists();
        selectPlaylist(id);
      }
    });

    // Rename Modal
    renamePlaylistBtn.addEventListener('click', () => {
      const playlist = state.playlists.find(p => p.id === state.currentPlaylistId);
      if (playlist) {
        document.getElementById('renameInput').value = playlist.name;
        document.getElementById('descriptionInput').value = playlist.description || '';
        openModal(renameModal);
      }
    });

    document.getElementById('cancelRename').addEventListener('click', () => {
      closeModal(renameModal);
    });

    document.getElementById('confirmRename').addEventListener('click', async () => {
      const name = document.getElementById('renameInput').value.trim();
      const desc = document.getElementById('descriptionInput').value.trim();
      if (!name) {
        showNotification('Please enter a playlist name', 'error');
        return;
      }
      if (await updatePlaylist(state.currentPlaylistId, name, desc)) {
        showNotification('Playlist updated', 'success');
        closeModal(renameModal);
        renderPlaylists();
        currentPlaylistName.textContent = name;
      }
    });

    // Delete Playlist
    deletePlaylistBtn.addEventListener('click', async () => {
      const playlist = state.playlists.find(p => p.id === state.currentPlaylistId);
      if (playlist && confirm(`Delete "${playlist.name}"? This cannot be undone.`)) {
        if (await deletePlaylist(state.currentPlaylistId)) {
          showNotification('Playlist deleted', 'success');
          selectPlaylist('all');
        }
      }
    });
  }

  // ==========================================
  // Search
  // ==========================================

  function setupSearch() {
    likesSearch.addEventListener('input', (e) => {
      state.searchQuery = e.target.value.trim();
      if (state.currentPlaylistId === 'all') {
        renderTracks(state.likes, false);
      } else {
        fetchPlaylistTracks(state.currentPlaylistId).then(data => {
          if (data) renderTracks(data.tracks || [], true);
        });
      }
    });
  }

  // ==========================================
  // Drag and Drop
  // ==========================================

  let draggedTrack = null;
  let draggedElement = null;
  
  // Touch drag state
  let touchDragState = {
    active: false,
    startX: 0,
    startY: 0,
    currentX: 0,
    currentY: 0,
    ghostElement: null,
    touchTimeout: null
  };

  function setupTrackDragAndDrop(item, track) {
    const dragHandle = item.querySelector('.liked-track-drag-handle');
    const sidebar = document.querySelector('.playlists-sidebar');
    
    // Make the entire track draggable (desktop)
    item.setAttribute('draggable', 'true');
    
    // Desktop drag events
    item.addEventListener('dragstart', (e) => {
      draggedTrack = track;
      draggedElement = item;
      item.classList.add('dragging');
      
      // Highlight sidebar to indicate drop target
      if (sidebar) {
        sidebar.classList.add('drag-active');
      }
      
      // Set drag data
      e.dataTransfer.effectAllowed = 'copy';
      e.dataTransfer.setData('text/plain', JSON.stringify({
        id: track.id || track.liked_track_id,
        artist: track.artist,
        title: track.title
      }));
      
      // Create custom drag image
      const dragImage = item.cloneNode(true);
      dragImage.style.position = 'absolute';
      dragImage.style.top = '-1000px';
      dragImage.style.opacity = '0.8';
      dragImage.style.width = item.offsetWidth + 'px';
      document.body.appendChild(dragImage);
      e.dataTransfer.setDragImage(dragImage, 20, 20);
      
      // Clean up drag image after drag
      setTimeout(() => {
        document.body.removeChild(dragImage);
      }, 0);
    });
    
    item.addEventListener('dragend', () => {
      item.classList.remove('dragging');
      draggedTrack = null;
      draggedElement = null;
      
      // Remove sidebar highlight
      if (sidebar) {
        sidebar.classList.remove('drag-active');
      }
      
      // Remove all drag-over states
      document.querySelectorAll('.drag-over').forEach(el => {
        el.classList.remove('drag-over');
      });
    });
    
    // Touch drag events (mobile)
    if (dragHandle) {
      dragHandle.addEventListener('touchstart', (e) => {
        // Start long press timer
        touchDragState.touchTimeout = setTimeout(() => {
          startTouchDrag(e, item, track, sidebar);
        }, 200); // 200ms hold to start drag
        
        const touch = e.touches[0];
        touchDragState.startX = touch.clientX;
        touchDragState.startY = touch.clientY;
      }, { passive: true });
      
      dragHandle.addEventListener('touchmove', (e) => {
        // Cancel if moved before timeout
        const touch = e.touches[0];
        const dx = Math.abs(touch.clientX - touchDragState.startX);
        const dy = Math.abs(touch.clientY - touchDragState.startY);
        
        if (!touchDragState.active && (dx > 10 || dy > 10)) {
          clearTimeout(touchDragState.touchTimeout);
        }
        
        if (touchDragState.active) {
          e.preventDefault();
          handleTouchDragMove(e);
        }
      }, { passive: false });
      
      dragHandle.addEventListener('touchend', (e) => {
        clearTimeout(touchDragState.touchTimeout);
        if (touchDragState.active) {
          handleTouchDragEnd(e, sidebar);
        }
      });
      
      dragHandle.addEventListener('touchcancel', () => {
        clearTimeout(touchDragState.touchTimeout);
        endTouchDrag(sidebar);
      });
    }
  }
  
  function startTouchDrag(e, item, track, sidebar) {
    touchDragState.active = true;
    draggedTrack = track;
    draggedElement = item;
    
    item.classList.add('dragging');
    if (sidebar) {
      sidebar.classList.add('drag-active');
    }
    
    // Create ghost element
    const ghost = item.cloneNode(true);
    ghost.classList.add('touch-drag-ghost');
    ghost.style.cssText = `
      position: fixed;
      pointer-events: none;
      z-index: 10000;
      width: ${item.offsetWidth}px;
      opacity: 0.9;
      transform: scale(1.02);
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    `;
    document.body.appendChild(ghost);
    touchDragState.ghostElement = ghost;
    
    const touch = e.touches[0];
    positionGhost(ghost, touch.clientX, touch.clientY);
    
    // Vibrate for feedback (if supported)
    if (navigator.vibrate) {
      navigator.vibrate(50);
    }
  }
  
  function handleTouchDragMove(e) {
    const touch = e.touches[0];
    touchDragState.currentX = touch.clientX;
    touchDragState.currentY = touch.clientY;
    
    if (touchDragState.ghostElement) {
      positionGhost(touchDragState.ghostElement, touch.clientX, touch.clientY);
    }
    
    // Check for drop targets
    const elementBelow = document.elementFromPoint(touch.clientX, touch.clientY);
    
    // Remove previous highlights
    document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
    
    // Check if over a playlist
    const playlistItem = elementBelow?.closest('.playlist-item');
    if (playlistItem && playlistItem.dataset.playlistId !== 'all') {
      playlistItem.classList.add('drag-over');
    }
  }
  
  function handleTouchDragEnd(e, sidebar) {
    const touch = e.changedTouches[0];
    const elementBelow = document.elementFromPoint(touch.clientX, touch.clientY);
    const playlistItem = elementBelow?.closest('.playlist-item');
    
    if (playlistItem && draggedTrack && playlistItem.dataset.playlistId !== 'all') {
      const playlistId = parseInt(playlistItem.dataset.playlistId, 10);
      const trackId = draggedTrack.id || draggedTrack.liked_track_id;
      
      addTrackToPlaylist(playlistId, trackId).then(success => {
        if (success) {
          const playlist = state.playlists.find(p => p.id === playlistId);
          showNotification(`Added to ${playlist?.name || 'playlist'}`, 'success');
          fetchPlaylists().then(() => renderPlaylists());
        }
      });
    }
    
    endTouchDrag(sidebar);
  }
  
  function endTouchDrag(sidebar) {
    touchDragState.active = false;
    
    if (touchDragState.ghostElement) {
      document.body.removeChild(touchDragState.ghostElement);
      touchDragState.ghostElement = null;
    }
    
    if (draggedElement) {
      draggedElement.classList.remove('dragging');
    }
    
    if (sidebar) {
      sidebar.classList.remove('drag-active');
    }
    
    document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
    
    draggedTrack = null;
    draggedElement = null;
  }
  
  function positionGhost(ghost, x, y) {
    ghost.style.left = (x - ghost.offsetWidth / 2) + 'px';
    ghost.style.top = (y - 30) + 'px';
  }

  function setupPlaylistDropZone(playlistItem, playlistId) {
    playlistItem.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      playlistItem.classList.add('drag-over');
    });
    
    playlistItem.addEventListener('dragleave', (e) => {
      // Only remove if we're actually leaving the element
      if (!playlistItem.contains(e.relatedTarget)) {
        playlistItem.classList.remove('drag-over');
      }
    });
    
    playlistItem.addEventListener('drop', async (e) => {
      e.preventDefault();
      playlistItem.classList.remove('drag-over');
      
      if (!draggedTrack || playlistId === 'all') return;
      
      const trackId = draggedTrack.id || draggedTrack.liked_track_id;
      const success = await addTrackToPlaylist(playlistId, trackId);
      
      if (success) {
        const playlist = state.playlists.find(p => p.id === playlistId);
        const playlistName = playlist ? playlist.name : 'playlist';
        showNotification(`Added to ${playlistName}`, 'success');
        await fetchPlaylists();
        renderPlaylists();
      }
    });
  }

  // Reorder tracks within a playlist
  let reorderDropTarget = null;
  
  function setupPlaylistReorder(item, track, index, allTracks) {
    // Only enable reordering within playlist view
    if (state.currentPlaylistId === 'all') return;
    
    item.addEventListener('dragover', (e) => {
      e.preventDefault();
      
      // Only allow reordering from within the same playlist
      if (!draggedElement || draggedElement === item) return;
      
      const rect = item.getBoundingClientRect();
      const midY = rect.top + rect.height / 2;
      
      // Clear previous markers
      document.querySelectorAll('.liked-track-item.drop-before, .liked-track-item.drop-after').forEach(el => {
        el.classList.remove('drop-before', 'drop-after');
      });
      
      if (e.clientY < midY) {
        item.classList.add('drop-before');
        reorderDropTarget = { index, position: 'before' };
      } else {
        item.classList.add('drop-after');
        reorderDropTarget = { index, position: 'after' };
      }
    });
    
    item.addEventListener('dragleave', () => {
      item.classList.remove('drop-before', 'drop-after');
    });
    
    item.addEventListener('drop', async (e) => {
      e.preventDefault();
      item.classList.remove('drop-before', 'drop-after');
      
      if (!draggedTrack || !reorderDropTarget || !track.playlist_track_id) return;
      
      // Build new order array
      const currentOrder = allTracks.map(t => t.playlist_track_id);
      const draggedIdx = currentOrder.indexOf(draggedTrack.playlist_track_id);
      const targetIdx = reorderDropTarget.index;
      
      if (draggedIdx === -1 || draggedIdx === targetIdx) return;
      
      // Remove dragged item
      currentOrder.splice(draggedIdx, 1);
      
      // Calculate new insert position
      let insertIdx = targetIdx;
      if (draggedIdx < targetIdx) {
        insertIdx = reorderDropTarget.position === 'before' ? targetIdx - 1 : targetIdx;
      } else {
        insertIdx = reorderDropTarget.position === 'before' ? targetIdx : targetIdx + 1;
      }
      
      // Insert at new position
      currentOrder.splice(insertIdx, 0, draggedTrack.playlist_track_id);
      
      // Call reorder API
      try {
        const res = await fetch(`/api/user_playlists/${state.currentPlaylistId}/reorder`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ track_ids: currentOrder })
        });
        const data = await res.json();
        if (data.success) {
          // Refresh playlist view
          selectPlaylist(state.currentPlaylistId);
        }
      } catch (err) {
        console.error('Failed to reorder tracks:', err);
      }
      
      reorderDropTarget = null;
    });
  }

  // ==========================================
  // Track Highlighting & Play All
  // ==========================================

  function highlightPlayingTrack(trackElement) {
    // Remove highlight from previous track
    if (currentlyPlayingElement) {
      currentlyPlayingElement.classList.remove('playing');
    }
    
    // Add highlight to new track
    if (trackElement) {
      trackElement.classList.add('playing');
      currentlyPlayingElement = trackElement;
      
      // Scroll into view if needed
      trackElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  function clearPlayingHighlight() {
    if (currentlyPlayingElement) {
      currentlyPlayingElement.classList.remove('playing');
      currentlyPlayingElement = null;
    }
  }

  async function playAllTracks() {
    // Get all YouTube buttons in order
    const buttons = likedTracksList.querySelectorAll('.track-youtube-btn');
    if (buttons.length === 0) {
      showNotification('No tracks to play', 'info');
      return;
    }
    
    // Reset the YouTube player track list
    if (typeof window.resetYouTubeTrackList === 'function') {
      window.resetYouTubeTrackList();
    }
    
    // Get the first track's info and play it
    const firstBtn = buttons[0];
    const artist = firstBtn.dataset.artist;
    const title = firstBtn.dataset.title;
    const trackItem = firstBtn.closest('.liked-track-item');
    
    if (!artist || !title) {
      showNotification('Could not find track info', 'error');
      return;
    }
    
    try {
      firstBtn.classList.add('searching');
      const resp = await fetch('/search_youtube', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artist, title })
      });
      
      if (!resp.ok) throw new Error('Failed to search YouTube');
      
      const data = await resp.json();
      if (data.success && !data.search_only) {
        // Highlight this track as playing
        highlightPlayingTrack(trackItem);
        
        if (typeof window.showYouTubePlayer === 'function') {
          window.showYouTubePlayer(data, artist, title, null, null, window.location.pathname, null, buildTrackPlaybackContext(firstBtn));
        }
      } else if (data.success && data.search_only) {
        showNotification('YouTube API limit reached, opening search', 'info');
        window.open(data.video_url, '_blank');
      } else {
        showNotification(data.message || 'Failed to play', 'error');
      }
    } catch (err) {
      console.error('Play all error:', err);
      showNotification('Failed to start playback', 'error');
    } finally {
      firstBtn.classList.remove('searching');
    }
  }

  function setupPlayAll() {
    if (playAllBtn) {
      playAllBtn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        
        // Visual feedback
        playAllBtn.classList.add('loading');
        playAllBtn.disabled = true;
        
        try {
          await playAllTracks();
        } finally {
          playAllBtn.classList.remove('loading');
          playAllBtn.disabled = false;
        }
      });
    }
    
    // Clean up any existing observers/timers before creating new ones
    cleanupLikesPage();
    
    // Listen for YouTube player close to clear highlighting
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
          const player = document.getElementById('youtube-player');
          if (player && !player.classList.contains('visible')) {
            clearPlayingHighlight();
          }
        }
      });
    });
    likesPageCleanupResources.mutationObserver = observer;
    
    // Observe the body for YouTube player changes
    const checkForPlayer = setInterval(() => {
      const player = document.getElementById('youtube-player');
      if (player) {
        observer.observe(player, { attributes: true });
        clearInterval(likesPageCleanupResources.checkForPlayerInterval);
        likesPageCleanupResources.checkForPlayerInterval = null;
      }
    }, 500);
    likesPageCleanupResources.checkForPlayerInterval = checkForPlayer;
    
    // Stop checking after 30 seconds
    likesPageCleanupResources.checkForPlayerTimeout = setTimeout(() => {
      if (likesPageCleanupResources.checkForPlayerInterval) {
        clearInterval(likesPageCleanupResources.checkForPlayerInterval);
        likesPageCleanupResources.checkForPlayerInterval = null;
      }
    }, 30000);
  }

  // ==========================================
  // Utilities
  // ==========================================

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // ==========================================
  // Initialization
  // ==========================================

  // Run the actual initialization
  (async function() {
    await Promise.all([fetchLikes(), fetchPlaylists(), fetchLikedEpisodes()]);
    renderPlaylists();
    renderTracks(state.likes, false);
    
    // Only set up event listeners once
    if (!initialized) {
      setupModals();
      setupSearch();
      setupPlayAll();
      initialized = true;
    }

    // "All Liked" click handler
    const allLikedItem = playlistList.querySelector('[data-playlist-id="all"]');
    if (allLikedItem && !allLikedItem.__likesbound) {
      allLikedItem.__likesbound = true;
      allLikedItem.addEventListener('click', () => selectPlaylist('all'));
    }
    
    // "Liked Episodes" click handler
    const episodesItem = playlistList.querySelector('[data-playlist-id="episodes"]');
    if (episodesItem && !episodesItem.__likesbound) {
      episodesItem.__likesbound = true;
      episodesItem.addEventListener('click', () => selectPlaylist('episodes'));
    }
  })();
  
  } // end initLikesPageCore
})();
