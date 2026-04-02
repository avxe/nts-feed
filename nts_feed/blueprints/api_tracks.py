"""Track, artist, and episode info API routes."""

import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from flask import Blueprint, current_app, jsonify, make_response, request
from sqlalchemy.orm import selectinload

from ..runtime_paths import genre_taxonomy_cache_path
from ..scrape import load_episodes, load_shows, slugify, _fetch_episode_page
from ..services.audio_service import AudioService

from ..validation import (
    ValidationError, escape_like, validate_id_list,
    MAX_IDS_BATCH,
)
from .helpers import db_available, get_db, get_discogs, get_lastfm, get_track_manager

bp = Blueprint('api_tracks', __name__)

# ---- Genre list cache with 5-minute TTL ----
_genre_cache_lock = threading.Lock()
_genre_cache = {
    'data': None,
    'expires_at': 0.0,
}
_GENRE_CACHE_TTL = 300  # 5 minutes


def _normalize_episode_url(value):
    text = str(value or '').strip()
    if not text:
        return ''
    try:
        parsed = urlsplit(text)
        path = parsed.path.rstrip('/') or '/'
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, '', ''))
    except Exception:
        return text.rstrip('/')


def _serialize_episode(ep):
    return {
        'url': ep.get('url'), 'title': ep.get('title'),
        'date': ep.get('date'), 'image_url': ep.get('image_url'),
        'audio_url': ep.get('audio_url'), 'genres': ep.get('genres', []),
        'tracklist': ep.get('tracklist', []),
        'is_new': ep.get('is_new', False),
        'is_downloaded': ep.get('is_downloaded', False),
    }


@bp.route('/api/episode_audio/<path:episode_url>')
def get_episode_audio(episode_url):
    """Extract streaming audio URL from an NTS episode page."""
    # Validate that the URL matches the NTS domain pattern
    full_url = f"https://{episode_url}" if not episode_url.startswith('http') else episode_url
    if not re.match(r'^https?://(?:www\.)?nts\.live/', full_url, re.IGNORECASE):
        return jsonify({
            'success': False, 'error': 'Invalid episode URL: must be an nts.live URL',
            'episode_url': episode_url, 'streaming_url': None, 'platform': None,
        }), 400
    try:
        audio_data = AudioService.extract_streaming_url(episode_url)
        if audio_data['success']:
            if audio_data['platform'] == 'mixcloud' and 'original_url' in audio_data:
                embed_url = AudioService.get_mixcloud_embed_url(audio_data['original_url'])
                if embed_url:
                    audio_data['embed_url'] = embed_url
            elif audio_data['platform'] == 'soundcloud' and 'original_url' in audio_data:
                embed_url = AudioService.get_soundcloud_embed_url(audio_data['original_url'])
                if embed_url:
                    audio_data['embed_url'] = embed_url
            return jsonify(audio_data)
        return jsonify(audio_data), 404
    except Exception:
        current_app.logger.exception('Audio extraction failed for %s', episode_url)
        return jsonify({
            'success': False, 'error': 'Failed to extract audio URL',
            'episode_url': episode_url, 'streaming_url': None, 'platform': None,
        }), 500


@bp.route('/api/episode_tracklist')
def api_episode_tracklist():
    """Return parsed tracklist for a given episode URL."""
    try:
        episode_url = request.args.get('episode_url')
        if not episode_url:
            return jsonify({'success': False, 'message': 'Missing episode_url'}), 400
        data = _fetch_episode_page(episode_url)
        if not data:
            return jsonify({'success': False, 'message': 'Failed to fetch episode page'}), 502
        return jsonify({'success': True, 'tracklist': data.get('tracklist', [])})
    except Exception:
        current_app.logger.exception('Episode tracklist fetch failed')
        return jsonify({'success': False, 'message': 'Failed to fetch episode tracklist'}), 500


@bp.route('/api/show/<path:url>/episodes')
def api_show_episodes(url):
    """Paginated episodes API for a given show."""
    try:
        page = max(int(request.args.get('page', 1)), 1)
        per_page = min(max(int(request.args.get('per_page', 20)), 1), 100)
        shows = load_shows()
        if url not in shows:
            return jsonify({'success': False, 'message': 'Show not found'}), 404

        show_slug = slugify(url)
        episodes_data = load_episodes(show_slug)
        all_episodes = episodes_data.get('episodes', [])

        # Only serve episodes that have tracklists loaded
        ready_episodes = [ep for ep in all_episodes if ep.get('tracklist')]
        pending = len(all_episodes) - len(ready_episodes)
        total = len(ready_episodes)

        start = (page - 1) * per_page
        page_episodes = ready_episodes[start:start + per_page]

        track_manager = get_track_manager()
        downloaded_episodes = {
            _normalize_episode_url(value)
            for value in track_manager.get_downloaded_episodes()
        }
        for ep in page_episodes:
            ep['is_downloaded'] = _normalize_episode_url(ep.get('url')) in downloaded_episodes

        return jsonify({
            'success': True,
            'episodes': [_serialize_episode(ep) for ep in page_episodes],
            'page': page, 'per_page': per_page, 'total': total,
            'has_more': (start + per_page) < total,
            'pending_episodes': pending,
        })
    except Exception:
        current_app.logger.exception('Episodes pagination failed')
        return jsonify({'success': False, 'message': 'Failed to load episodes'}), 500


@bp.route('/api/show/<path:url>/episode')
def api_show_episode(url):
    """Return exact episode data for a show-scoped episode URL."""
    try:
        episode_url = request.args.get('episode_url')
        if not episode_url:
            return jsonify({'success': False, 'message': 'Missing episode_url'}), 400

        per_page = min(max(int(request.args.get('per_page', 20)), 1), 100)
        shows = load_shows()
        if url not in shows:
            return jsonify({'success': False, 'message': 'Show not found'}), 404

        show_slug = slugify(url)
        episodes_data = load_episodes(show_slug)
        all_episodes = episodes_data.get('episodes', [])
        normalized_target = _normalize_episode_url(episode_url)
        target_index = next((
            index for index, episode in enumerate(all_episodes)
            if _normalize_episode_url(episode.get('url')) == normalized_target
        ), None)
        if target_index is None:
            return jsonify({'success': False, 'message': 'Episode not found'}), 404

        track_manager = get_track_manager()
        downloaded_episodes = {
            _normalize_episode_url(value)
            for value in track_manager.get_downloaded_episodes()
        }
        episode = dict(all_episodes[target_index])
        episode['is_downloaded'] = _normalize_episode_url(episode.get('url')) in downloaded_episodes

        return jsonify({
            'success': True,
            'episode': _serialize_episode(episode),
            'page': (target_index // per_page) + 1,
            'per_page': per_page,
            'index': target_index,
            'total': len(all_episodes),
        })
    except Exception:
        current_app.logger.exception('Episode lookup failed')
        return jsonify({'success': False, 'message': 'Failed to load episode'}), 500


@bp.route('/api/artist_info')
def artist_info():
    """Fetch artist information from Discogs."""
    artist_name = request.args.get('name')
    if not artist_name:
        return jsonify({'error': 'Artist name is required'}), 400
    try:
        svc = get_discogs()
        response = {
            'name': artist_name, 'image': None,
            'bio': f"Information about {artist_name}. Click the external links below to learn more.",
            'genres': [], 'topAlbums': [],
            'links': {
                'discogs': f"https://www.discogs.com/search/?q={artist_name}&type=artist",
                'youtube': f"https://www.youtube.com/results?search_query={artist_name}",
            },
        }
        results = svc.search_artist(artist_name, limit=5)
        if results:
            artist_data = results[0]
            if 'id' in artist_data:
                response['links']['discogs'] = f"https://www.discogs.com/artist/{artist_data['id']}"
                detail = svc.get_artist_detail(artist_data['id'])
                if detail:
                    if detail.get('profile'):
                        bio = detail['profile']
                        response['bio'] = bio[:497] + '...' if len(bio) > 500 else bio
                    if detail.get('images'):
                        response['image'] = detail['images'][0].get('uri')
                    if detail.get('genres'):
                        response['genres'] = detail['genres'][:5]
        releases = svc.search_release(artist_name, limit=5)
        if releases:
            for r in releases[:5]:
                if 'id' in r and 'title' in r:
                    response['topAlbums'].append({
                        'title': r.get('title', ''),
                        'year': r.get('year', ''),
                        'cover': r.get('cover_image'),
                    })
        return jsonify(response)
    except Exception:
        current_app.logger.exception('Artist info fetch failed')
        return jsonify({'error': 'Failed to fetch artist information'}), 500


@bp.route('/api/track_info')
def track_info():
    """Fetch track info from Discogs."""
    artist = request.args.get('artist')
    title = request.args.get('title')
    if not artist or not title:
        return jsonify({'error': 'Artist and title are required'}), 400
    try:
        svc = get_discogs()
        response = {
            'title': title, 'artist': artist,
            'album': {
                'title': 'Unknown Album', 'year': 'Unknown',
                'cover': None, 'label': 'Unknown Label', 'genres': [],
            },
            'duration': 'Unknown',
            'links': {
                'youtube': f"https://www.youtube.com/results?search_query={artist} {title}",
                'discogs': f"https://www.discogs.com/search/?q={artist}+{title}&type=release",
            },
        }
        results = svc.search_release(f"{artist} {title}", limit=5)
        if results:
            rel = results[0]
            if 'id' in rel:
                response['links']['discogs'] = f"https://www.discogs.com/release/{rel['id']}"
                detail = svc.get_release_detail(rel['id'])
                if detail:
                    response['album']['title'] = detail.get('title', 'Unknown Album')
                    response['album']['year'] = detail.get('year', 'Unknown')
                    if detail.get('labels'):
                        response['album']['label'] = detail['labels'][0].get('name')
                    if detail.get('images'):
                        response['album']['cover'] = detail['images'][0].get('uri')
                    if detail.get('genres'):
                        response['album']['genres'] = detail['genres'][:3]
                    if 'tracklist' in detail:
                        for t in detail['tracklist']:
                            if title.lower() in t.get('title', '').lower():
                                response['duration'] = t.get('duration', 'Unknown')
                                if t.get('position'):
                                    response['position'] = t['position']
                                break
        return jsonify(response)
    except Exception:
        current_app.logger.exception('Track info fetch failed')
        return jsonify({'error': 'Failed to fetch track information'}), 500


@bp.route('/api/lastfm/similar_artists')
def api_lastfm_similar_artists():
    artist_name = request.args.get('name')
    if not artist_name:
        return jsonify({'error': 'No artist name provided'}), 400
    try:
        similar = get_lastfm().get_similar_artists(artist_name)
        if not similar:
            return jsonify({
                'success': True, 'artist': artist_name,
                'similar_artists': [], 'no_results': True,
                'message': 'No similar artists found',
            })
        return jsonify({'success': True, 'artist': artist_name, 'similar_artists': similar})
    except Exception:
        current_app.logger.exception('Last.fm similar artists fetch failed')
        return jsonify({'success': False, 'message': 'Failed to fetch similar artists'}), 500


@bp.route('/api/lastfm/artist_info')
def api_lastfm_artist_info():
    artist_name = request.args.get('name')
    if not artist_name:
        return jsonify({'error': 'No artist name provided'}), 400
    try:
        info = get_lastfm().get_artist_info(artist_name)
        if not info:
            return jsonify({'success': False, 'message': f'No information found for artist: {artist_name}'}), 404
        return jsonify({'success': True, 'artist': info})
    except Exception:
        current_app.logger.exception('Last.fm artist info fetch failed')
        return jsonify({'success': False, 'message': 'Failed to fetch artist information'}), 500


@bp.route('/api/lastfm/track_info')
def api_lastfm_track_info():
    artist_name = request.args.get('artist')
    track_name = request.args.get('title')
    if not artist_name or not track_name:
        return jsonify({'error': 'Both artist and title parameters are required'}), 400
    try:
        info = get_lastfm().get_track_info(artist_name, track_name)
        if not info:
            return jsonify({'success': False, 'message': f'No information found for: {artist_name} - {track_name}'}), 404
        return jsonify({'success': True, 'track': info})
    except Exception:
        current_app.logger.exception('Last.fm track info fetch failed')
        return jsonify({'success': False, 'message': 'Failed to fetch track information'}), 500


@bp.route('/api/update_track', methods=['POST'])
def api_update_track():
    """Update track artist/title in stored episode JSON."""
    try:
        data = request.get_json(silent=True) or {}
        show_slug = (data.get('show_slug') or '').strip()
        original_artist = (data.get('original_artist') or '').strip()
        original_title = (data.get('original_title') or '').strip()
        new_artist = (data.get('new_artist') or '').strip()
        new_title = (data.get('new_title') or '').strip()

        if not all([show_slug, original_artist, original_title, new_artist, new_title]):
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400

        from ..scrape import save_episodes
        episodes_data = load_episodes(show_slug)
        updated = False
        for episode in episodes_data.get('episodes', []):
            for track in episode.get('tracklist') or []:
                if (str(track.get('artist') or '') == original_artist
                        and str(track.get('name') or '') == original_title):
                    track['artist'] = new_artist
                    track['name'] = new_title
                    updated = True

        if updated:
            save_episodes(show_slug, episodes_data)
            return jsonify({'success': True, 'message': 'Track information updated successfully'})
        return jsonify({'success': False, 'message': 'Track not found in episodes'}), 404
    except Exception:
        current_app.logger.exception('Track update failed')
        return jsonify({'success': False, 'message': 'Failed to update track'}), 500


@bp.route('/api/genres')
def api_list_genres():
    """Return all genre names, cached with a 5-minute TTL."""
    if not db_available():
        return jsonify({'success': True, 'genres': []})
    try:
        now = time.time()
        # Check cache under lock
        with _genre_cache_lock:
            if _genre_cache['data'] is not None and now < _genre_cache['expires_at']:
                cached_genres = _genre_cache['data']
                resp = make_response(jsonify({'success': True, 'genres': cached_genres}))
                resp.headers['Cache-Control'] = 'public, max-age=300'
                return resp

        # Cache miss -- query the database
        from ..db.models import Genre
        with get_db()() as session:
            genres = [g.name for g in session.query(Genre).order_by(Genre.name).all()]

        # Store in cache
        with _genre_cache_lock:
            _genre_cache['data'] = genres
            _genre_cache['expires_at'] = time.time() + _GENRE_CACHE_TTL

        resp = make_response(jsonify({'success': True, 'genres': genres}))
        resp.headers['Cache-Control'] = 'public, max-age=300'
        return resp
    except Exception:
        current_app.logger.exception('Genre list fetch failed')
        return jsonify({'success': False, 'message': 'Failed to fetch genres'}), 500


@bp.route('/api/genres/explore')
def api_genres_explore():
    """Search genres and return taxonomy-derived related genres.

    Query params:
        q     – fuzzy search string (returns matching genres with episode counts)
        genre – selected genre name (returns related genres from taxonomy)
    """
    q = (request.args.get('q') or '').strip().lower()
    genre = (request.args.get('genre') or '').strip()

    result = {'success': True}

    if not db_available():
        result['matching_genres'] = []
        result['related_genres'] = []
        result['family'] = None
        return jsonify(result)

    try:
        from sqlalchemy import func, select
        from ..db.models import EpisodeGenre, Genre

        if q:
            with get_db()() as session:
                rows = session.execute(
                    select(Genre.name, func.count(EpisodeGenre.episode_id).label('count'))
                    .outerjoin(EpisodeGenre, EpisodeGenre.genre_id == Genre.id)
                    .where(func.lower(Genre.name).contains(q))
                    .group_by(Genre.id, Genre.name)
                    .order_by(func.count(EpisodeGenre.episode_id).desc())
                    .limit(30)
                ).all()
                result['matching_genres'] = [
                    {'name': row.name, 'episode_count': row.count}
                    for row in rows
                ]
        else:
            result['matching_genres'] = []

        if genre:
            try:
                from ..services.genre_taxonomy_service import GenreTaxonomyService

                cache_path = genre_taxonomy_cache_path()
                if cache_path.exists():
                    taxonomy_service = GenreTaxonomyService(cache_dir=str(cache_path.parent))
                    similar = taxonomy_service.get_similar_genres(genre, min_similarity=0.2)
                    family = taxonomy_service.get_genre_family(genre)
                    result['related_genres'] = [
                        {'name': name, 'similarity': round(score, 3)}
                        for name, score in similar[:15]
                    ]
                    result['family'] = family
                else:
                    result['related_genres'] = []
                    result['family'] = None
            except Exception:
                current_app.logger.debug('Taxonomy lookup unavailable, skipping related genres')
                result['related_genres'] = []
                result['family'] = None
        else:
            result['related_genres'] = []
            result['family'] = None

        resp = make_response(jsonify(result))
        resp.headers['Cache-Control'] = 'public, max-age=60'
        return resp
    except Exception:
        current_app.logger.exception('Genre explore failed')
        return jsonify({'success': False, 'message': 'Failed to explore genres'}), 500


@bp.route('/api/tracks/by_ids', methods=['POST'])
def api_tracks_by_ids():
    """Fetch tracks by a list of IDs."""
    data = request.get_json() or {}
    try:
        ids = validate_id_list(data.get('ids'), 'ids', max_length=MAX_IDS_BATCH)
    except ValidationError as e:
        return jsonify({'success': False, 'message': e.message, 'field': e.field}), 400

    if not ids or not db_available():
        return jsonify({'success': True, 'tracks': []})
    try:
        from ..db.models import Track
        with get_db()() as session:
            tracks = [{
                'id': t.id,
                'title': t.title_original or t.title_norm,
                'artists': [a.name for a in t.artists],
            } for t in session.query(Track).options(selectinload(Track.artists)).filter(Track.id.in_(ids)).all()]
        return jsonify({'success': True, 'tracks': tracks})
    except Exception:
        current_app.logger.exception('Tracks by IDs fetch failed')
        return jsonify({'success': False, 'message': 'Failed to fetch tracks'}), 500


@bp.route('/api/episodes/by_ids', methods=['POST'])
def api_episodes_by_ids():
    """Fetch episodes by a list of IDs."""
    data = request.get_json() or {}
    try:
        ids = validate_id_list(data.get('ids'), 'ids', max_length=MAX_IDS_BATCH)
    except ValidationError as e:
        return jsonify({'success': False, 'message': e.message, 'field': e.field}), 400

    if not ids or not db_available():
        return jsonify({'success': True, 'episodes': []})
    try:
        from ..db.models import Episode, Show
        with get_db()() as session:
            rows = (
                session.query(Episode, Show)
                .join(Show, Show.id == Episode.show_id)
                .filter(Episode.id.in_(ids))
                .all()
            )
            episodes = [{
                'id': ep.id, 'title': ep.title, 'date': ep.date,
                'show_title': show.title, 'show_url': show.url,
                'image_url': ep.image_url, 'url': ep.url,
            } for ep, show in rows]
        return jsonify({'success': True, 'episodes': episodes})
    except Exception:
        current_app.logger.exception('Episodes by IDs fetch failed')
        return jsonify({'success': False, 'message': 'Failed to fetch episodes'}), 500


@bp.route('/api/shows/by_ids', methods=['POST'])
def api_shows_by_ids():
    """Fetch shows by a list of IDs."""
    data = request.get_json() or {}
    try:
        ids = validate_id_list(data.get('ids'), 'ids', max_length=MAX_IDS_BATCH)
    except ValidationError as e:
        return jsonify({'success': False, 'message': e.message, 'field': e.field}), 400

    if not ids or not db_available():
        return jsonify({'success': True, 'shows': []})
    try:
        from ..db.models import Show
        with get_db()() as session:
            shows = [{
                'id': s.id, 'title': s.title,
                'url': s.url, 'thumbnail': s.thumbnail,
            } for s in session.query(Show).filter(Show.id.in_(ids)).all()]
        return jsonify({'success': True, 'shows': shows})
    except Exception:
        current_app.logger.exception('Shows by IDs fetch failed')
        return jsonify({'success': False, 'message': 'Failed to fetch shows'}), 500


@bp.route('/api/tracks')
def api_tracks():
    """Paginated, sortable, filterable track stats API.

    Performance optimizations:
    - Aggregation query avoids joining artists unless text search ``q`` is active
    - Count query uses the same filtered subquery (no double computation)
    - Enrichment (artists, genres, episodes) uses batch IN queries on page IDs only
    - Episodes per track capped at MAX_EPISODES_PER_TRACK in Python (SQLite compat)
    - Early return on empty track_ids avoids unnecessary enrichment queries
    """
    DEFAULT_EPISODES_PER_TRACK = 15
    MAX_EPISODES_PER_TRACK = 15

    if not db_available():
        resp = make_response(jsonify({'success': True, 'tracks': [], 'page': 1, 'per_page': 50, 'total': 0, 'has_more': False}))
        resp.headers['Cache-Control'] = 'no-cache'
        return resp
    try:
        from sqlalchemy import func, distinct, or_, desc, asc
        from ..db.models import Track, Artist, EpisodeTrack, Episode, Show, EpisodeGenre, Genre

        page = max(int(request.args.get('page', 1)), 1)
        per_page = max(1, min(int(request.args.get('per_page', 50)), 200))
        sort_by = (request.args.get('sort_by') or 'play_count').strip()
        sort_dir = (request.args.get('sort_dir') or 'desc').strip().lower()
        try:
            episodes_limit = int(request.args.get('episodes_limit', DEFAULT_EPISODES_PER_TRACK))
        except (TypeError, ValueError):
            episodes_limit = DEFAULT_EPISODES_PER_TRACK
        episodes_limit = max(1, min(episodes_limit, MAX_EPISODES_PER_TRACK))
        _MAX_FILTER_LEN = 500
        q = (request.args.get('q') or '').strip().lower()[:_MAX_FILTER_LEN]
        title_filter_param = (request.args.get('title_filter') or '').strip().lower()[:_MAX_FILTER_LEN]
        artist_filter_param = (request.args.get('artist_filter') or '').strip().lower()[:_MAX_FILTER_LEN]
        show_filter = (request.args.get('show_filter') or '').strip()[:_MAX_FILTER_LEN]
        genres_param = (request.args.get('genres') or '').strip()[:_MAX_FILTER_LEN]
        episode_id_param = (request.args.get('episode_id') or '').strip()[:_MAX_FILTER_LEN]
        episode_filter = (request.args.get('episode_filter') or '').strip()[:_MAX_FILTER_LEN]
        show_id_param = (request.args.get('show_id') or '').strip()[:_MAX_FILTER_LEN]
        show_url_param = (request.args.get('show_url') or '').strip()[:_MAX_FILTER_LEN]

        genres_filter = [g.strip() for g in genres_param.split(',') if g.strip()] if genres_param else []

        with get_db()() as session:
            # Build base aggregation query
            base = (
                session.query(
                    Track.id.label('track_id'),
                    func.count(EpisodeTrack.id).label('play_count'),
                    func.count(distinct(Show.id)).label('shows_count'),
                    func.min(Episode.date).label('first_seen'),
                    func.max(Episode.date).label('last_seen'),
                )
                .select_from(Track)
                .outerjoin(EpisodeTrack, EpisodeTrack.track_id == Track.id)
                .outerjoin(Episode, Episode.id == EpisodeTrack.episode_id)
                .outerjoin(Show, Show.id == Episode.show_id)
            )

            # Separate title/artist filters (preferred) or combined q fallback
            if title_filter_param or artist_filter_param:
                if title_filter_param:
                    base = base.filter(Track.title_norm.contains(escape_like(title_filter_param), escape='\\'))
                if artist_filter_param:
                    base = base.outerjoin(Track.artists)
                    base = base.filter(Artist.name.ilike(f"%{escape_like(artist_filter_param)}%", escape='\\'))
            elif q:
                # Legacy combined search: requires artist JOIN
                base = base.outerjoin(Track.artists)
                base = base.filter(or_(Track.title_norm.contains(escape_like(q), escape='\\'), Artist.name.ilike(f"%{escape_like(q)}%", escape='\\')))

            # Show filters (by id, url, or text search)
            if show_id_param:
                try:
                    base = base.filter(Show.id == int(show_id_param))
                except (ValueError, TypeError):
                    pass
            elif show_url_param:
                base = base.filter(Show.url == show_url_param)
            elif show_filter:
                base = base.filter(Show.title.ilike(f"%{escape_like(show_filter)}%", escape='\\'))

            # Genre filter
            if genres_filter:
                base = (
                    base.join(EpisodeGenre, EpisodeGenre.episode_id == Episode.id)
                    .join(Genre, Genre.id == EpisodeGenre.genre_id)
                    .filter(Genre.name.in_(genres_filter))
                )

            # Episode filter: by exact ID or by text search on episode title
            if episode_id_param:
                try:
                    base = base.filter(Episode.id == int(episode_id_param))
                except (ValueError, TypeError):
                    pass
            elif episode_filter:
                base = base.filter(Episode.title.ilike(f"%{escape_like(episode_filter)}%", escape='\\'))

            base = base.group_by(Track.id)

            # Build sort clause
            sort_map = {
                'title': asc(Track.title_norm),
                'play_count': desc(func.count(EpisodeTrack.id)),
                'shows_count': desc(func.count(distinct(Show.id))),
                'first_seen': asc(func.min(Episode.date)),
                'last_seen': desc(func.max(Episode.date)),
            }
            order_clause = sort_map.get(sort_by, sort_map['play_count'])
            if sort_by in ('title', 'first_seen') and sort_dir == 'desc':
                order_clause = desc(Track.title_norm) if sort_by == 'title' else desc(func.min(Episode.date))
            elif sort_by not in ('title', 'first_seen') and sort_dir == 'asc':
                asc_map = {
                    'play_count': asc(func.count(EpisodeTrack.id)),
                    'shows_count': asc(func.count(distinct(Show.id))),
                    'last_seen': asc(func.max(Episode.date)),
                }
                order_clause = asc_map.get(sort_by, order_clause)

            # Count total matching tracks via lightweight subquery
            sub = base.with_entities(Track.id).subquery()
            total = session.query(func.count()).select_from(sub).scalar() or 0

            # Early exit if no results
            if total == 0:
                resp = make_response(jsonify({'success': True, 'tracks': [], 'page': page, 'per_page': per_page, 'total': 0, 'has_more': False}))
                resp.headers['Cache-Control'] = 'no-cache'
                return resp

            rows = base.order_by(order_clause).offset((page - 1) * per_page).limit(per_page).all()
            track_ids = [r.track_id for r in rows]

            if not track_ids:
                resp = make_response(jsonify({'success': True, 'tracks': [], 'page': page, 'per_page': per_page, 'total': total, 'has_more': (page * per_page) < total}))
                resp.headers['Cache-Control'] = 'no-cache'
                return resp

            # ---- Batch enrichment (single query per dimension) ----

            # Artists - single JOIN query for the page's tracks
            artist_rows = session.query(Track.id, Artist.name).join(Track.artists).filter(Track.id.in_(track_ids)).all()
            artists_map: dict = {}
            for tid, name in artist_rows:
                artists_map.setdefault(tid, []).append(name)

            # Top genres per track (via episode genres, capped at 5 per track)
            genre_rows = (
                session.query(EpisodeTrack.track_id, Genre.name, func.count(EpisodeGenre.id).label('cnt'))
                .join(Episode, Episode.id == EpisodeTrack.episode_id)
                .join(EpisodeGenre, EpisodeGenre.episode_id == Episode.id)
                .join(Genre, Genre.id == EpisodeGenre.genre_id)
                .filter(EpisodeTrack.track_id.in_(track_ids))
                .group_by(EpisodeTrack.track_id, Genre.name)
                .order_by(EpisodeTrack.track_id.asc(), func.count(EpisodeGenre.id).desc())
                .all()
            )
            genres_map: dict = {}
            for tid, gname, cnt in genre_rows:
                lst = genres_map.setdefault(tid, [])
                if len(lst) < 5:
                    lst.append(gname)

            # Episodes per track - fetch only columns we need (no full ORM load)
            # and cap with Python-side truncation for SQLite compatibility
            all_ep_rows = (
                session.query(
                    EpisodeTrack.track_id,
                    Episode.id, Episode.url, Episode.title, Episode.date,
                    Show.title, Show.url,
                )
                .join(Episode, Episode.id == EpisodeTrack.episode_id)
                .join(Show, Show.id == Episode.show_id)
                .filter(EpisodeTrack.track_id.in_(track_ids))
                .order_by(EpisodeTrack.track_id.asc(), Episode.date.desc())
                .all()
            )
            all_episodes_map: dict = {}
            for tid, ep_id, ep_url, ep_title, ep_date, show_title, show_url in all_ep_rows:
                lst = all_episodes_map.setdefault(tid, [])
                if len(lst) < episodes_limit:
                    lst.append({
                        'id': ep_id, 'url': ep_url, 'title': ep_title,
                        'date': ep_date, 'show_title': show_title,
                        'show_url': show_url,
                    })

            # Track titles - single lightweight query
            agg_map = {r.track_id: r for r in rows}
            trows = session.query(Track.id, Track.title_original, Track.title_norm).filter(Track.id.in_(track_ids)).all()
            title_map = {tid: (to or tn) for tid, to, tn in trows}

            results = [{
                'id': tid,
                'title': title_map.get(tid),
                'artists': artists_map.get(tid, []),
                'play_count': int(agg_map[tid].play_count or 0),
                'shows_count': int(agg_map[tid].shows_count or 0),
                'top_genres': genres_map.get(tid, []),
                'all_episodes': all_episodes_map.get(tid, []),
            } for tid in track_ids]

            resp = make_response(jsonify({
                'success': True, 'tracks': results,
                'page': page, 'per_page': per_page,
                'total': total, 'has_more': (page * per_page) < total,
            }))
            resp.headers['Cache-Control'] = 'no-cache'
            return resp
    except Exception:
        current_app.logger.exception('Tracks API failed')
        return jsonify({'success': False, 'message': 'Failed to fetch tracks'}), 500


@bp.route('/api/track/<int:track_id>/episodes')
def api_track_episodes(track_id):
    """Return episodes that include the given track id."""
    if not db_available():
        return jsonify({'success': True, 'episodes': []})
    try:
        from ..db.models import EpisodeTrack, Episode, Show
        with get_db()() as session:
            rows = (
                session.query(EpisodeTrack, Episode, Show)
                .join(Episode, Episode.id == EpisodeTrack.episode_id)
                .join(Show, Show.id == Episode.show_id)
                .filter(EpisodeTrack.track_id == track_id)
                .all()
            )
            episodes = [{
                'url': ep.url, 'title': ep.title, 'date': ep.date,
                'image_url': ep.image_url, 'show_title': show.title,
            } for _, ep, show in rows]
            try:
                episodes.sort(key=lambda x: x.get('date') or '', reverse=True)
            except Exception:
                pass
            return jsonify({'success': True, 'episodes': episodes})
    except Exception:
        current_app.logger.exception('Track episodes query failed')
        return jsonify({'success': False, 'message': 'Failed to fetch track episodes'}), 500


@bp.route('/api/track/<int:track_id>/explain')
def api_track_explain(track_id):
    """Get detailed explanation for why a track matches given seeds."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503

    try:
        from ..db.models import (
            Track, ArtistGenre, ArtistRelationship,
            EpisodeTrack, Episode, Show, TrackTag, Tag,
        )

        seed_show_ids_param = request.args.get('seed_show_ids', '')
        seed_genres_param = request.args.get('seed_genres', '')
        seed_show_ids = [int(x.strip()) for x in seed_show_ids_param.split(',') if x.strip().isdigit()]
        seed_genres = [x.strip().lower() for x in seed_genres_param.split(',') if x.strip()]

        with get_db()() as session:
            track = session.query(Track).filter(Track.id == track_id).first()
            if not track:
                return jsonify({'success': False, 'message': 'Track not found'}), 404

            artist_names = [a.name for a in track.artists]

            tag_rows = (
                session.query(Tag.name, TrackTag.weight)
                .join(TrackTag, TrackTag.tag_id == Tag.id)
                .filter(TrackTag.track_id == track_id)
                .order_by(TrackTag.weight.desc())
                .limit(10).all()
            )
            track_tags = [{'name': name, 'weight': round(weight or 0.0, 3)} for name, weight in tag_rows]
            track_tag_names = {t['name'].lower() for t in track_tags}
            shared_genres = sorted(list(track_tag_names & set(seed_genres)))

            episode_rows = (
                session.query(Episode, Show)
                .join(EpisodeTrack, EpisodeTrack.episode_id == Episode.id)
                .join(Show, Show.id == Episode.show_id)
                .filter(EpisodeTrack.track_id == track_id)
                .limit(10).all()
            )
            episodes = [{
                'title': ep.title, 'date': ep.date, 'episode_id': ep.id,
                'episode_url': ep.url, 'show_title': show.title,
                'show_id': show.id, 'show_url': show.url,
                'is_seed_show': show.id in seed_show_ids,
            } for ep, show in episode_rows]

            enriched_artists = []
            for artist in track.artists:
                artist_data = {
                    'id': artist.id, 'name': artist.name,
                    'mbid': artist.mbid, 'disambiguation': artist.disambiguation,
                    'type': artist.mb_type, 'country': artist.country,
                    'genres': [], 'relationships': [],
                }
                genres = (
                    session.query(ArtistGenre)
                    .filter(ArtistGenre.artist_id == artist.id)
                    .order_by(ArtistGenre.weight.desc()).limit(8).all()
                )
                artist_data['genres'] = [
                    {'name': g.genre, 'weight': round(g.weight, 3), 'source': g.source}
                    for g in genres
                ]
                relationships = (
                    session.query(ArtistRelationship)
                    .filter(ArtistRelationship.artist_id == artist.id)
                    .limit(10).all()
                )
                artist_data['relationships'] = [{
                    'type': r.relationship_type,
                    'related_name': r.related_artist_name,
                    'related_mbid': r.related_artist_mbid,
                    'direction': r.direction,
                    'related_in_db': r.related_artist_id is not None,
                } for r in relationships]
                enriched_artists.append(artist_data)

            explanation = {
                'has_enrichment': any(a.get('mbid') or a.get('genres') for a in enriched_artists),
                'shared_genres': shared_genres,
                'from_seed_show': any(ep.get('is_seed_show') for ep in episodes),
                'artist_connections': [],
            }
            for ad in enriched_artists:
                for rel in ad.get('relationships', []):
                    if rel.get('related_in_db'):
                        explanation['artist_connections'].append({
                            'artist': ad['name'],
                            'relationship': rel['type'],
                            'related_to': rel['related_name'],
                        })

            return jsonify({
                'success': True,
                'track': {'id': track.id, 'title': track.title_original or track.title_norm, 'artists': artist_names},
                'tags': track_tags, 'episodes': episodes,
                'enriched_artists': enriched_artists, 'explanation': explanation,
            })

    except Exception:
        current_app.logger.exception('Track explain query failed')
        return jsonify({'success': False, 'message': 'Failed to fetch track explanation'}), 500
