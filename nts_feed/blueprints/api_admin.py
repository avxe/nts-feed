"""Admin API routes for collection statistics and maintenance jobs."""

import threading
from collections import Counter, defaultdict
from ipaddress import ip_address

from flask import Blueprint, current_app, jsonify, request

from ..settings import load_settings, save_settings

from .helpers import db_available, get_db

bp = Blueprint('api_admin', __name__)

_weight_recalc_job = {'running': False, 'thread': None}
_weight_recalc_lock = threading.Lock()


def _settings_request_allowed() -> bool:
    if current_app.config.get('ALLOW_REMOTE_ADMIN_SETTINGS'):
        return True

    host = (request.host or '').split(':', 1)[0].strip().lower()
    if host in {'localhost', '127.0.0.1', '::1'}:
        return True

    remote_addr = (request.remote_addr or '').strip()
    if not remote_addr:
        return False
    try:
        return ip_address(remote_addr).is_loopback
    except ValueError:
        return False


def _empty_listening_summary():
    return {
        'episode_listens': 0,
        'track_listens': 0,
        'top_shows': [],
        'top_artists': [],
        'top_genres': [],
    }


def _build_listening_summary(session):
    from sqlalchemy import select

    from ..db.models import EpisodeGenre, Genre, ListeningSession, Show

    listening_rows = session.execute(
        select(
            ListeningSession.kind,
            ListeningSession.episode_id,
            ListeningSession.artist_name,
            Show.title.label('show_title'),
        )
        .outerjoin(Show, Show.id == ListeningSession.show_id)
        .where(
            ListeningSession.is_meaningful.is_(True),
            ListeningSession.last_event_at.is_not(None),
        )
        .order_by(ListeningSession.last_event_at.desc(), ListeningSession.id.desc())
    ).all()

    if not listening_rows:
        return _empty_listening_summary()

    episode_ids = sorted({row.episode_id for row in listening_rows if row.episode_id})
    genres_by_episode = defaultdict(list)
    if episode_ids:
        for episode_id, genre_name in session.execute(
            select(EpisodeGenre.episode_id, Genre.name)
            .join(Genre, Genre.id == EpisodeGenre.genre_id)
            .where(EpisodeGenre.episode_id.in_(episode_ids))
        ).all():
            genres_by_episode[episode_id].append(genre_name)

    show_counts = Counter()
    artist_counts = Counter()
    genre_counts = Counter()
    episode_listens = 0
    track_listens = 0

    for row in listening_rows:
        if row.kind == 'episode':
            episode_listens += 1
        elif row.kind == 'track':
            track_listens += 1
            if row.artist_name:
                artist_counts[row.artist_name] += 1

        if row.show_title:
            show_counts[row.show_title] += 1

        if row.episode_id:
            for genre_name in genres_by_episode.get(row.episode_id, []):
                genre_counts[genre_name] += 1

    return {
        'episode_listens': episode_listens,
        'track_listens': track_listens,
        'top_shows': [
            {'name': show_name, 'count': count}
            for show_name, count in show_counts.most_common(5)
            if show_name
        ],
        'top_artists': [
            {'name': artist_name, 'count': count}
            for artist_name, count in artist_counts.most_common(5)
            if artist_name
        ],
        'top_genres': [
            {'name': genre_name, 'count': count}
            for genre_name, count in genre_counts.most_common(5)
            if genre_name
        ],
    }


@bp.route('/api/admin/stats')
def admin_stats():
    """Get overall admin statistics."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503

    try:
        from sqlalchemy import func

        from ..db.models import Episode, Show, Track, Artist

        db_sessionmaker = get_db()

        with db_sessionmaker() as session:
            total_shows = session.query(func.count(Show.id)).scalar() or 0
            total_episodes = session.query(func.count(Episode.id)).scalar() or 0
            total_tracks = session.query(func.count(Track.id)).scalar() or 0
            total_artists = session.query(func.count(Artist.id)).scalar() or 0
            listening_summary = _build_listening_summary(session)

        return jsonify({
            'success': True,
            'stats': {
                'total_shows': total_shows,
                'total_episodes': total_episodes,
                'total_tracks': total_tracks,
                'total_artists': total_artists,
            },
            'listening_summary': listening_summary,
        })

    except Exception as e:
        current_app.logger.exception('Get admin stats failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/admin/recalculate-track-weights', methods=['POST'])
def recalculate_weights():
    """Start background track weight recalculation."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503

    with _weight_recalc_lock:
        if _weight_recalc_job['running']:
            return jsonify({
                'success': False,
                'message': 'Weight recalculation already in progress',
            }), 409
        _weight_recalc_job['running'] = True

    try:
        from ..db.ingest import recalculate_track_weights

        db_sessionmaker = get_db()

        def run_recalc():
            try:
                recalculate_track_weights(db_sessionmaker)
            finally:
                with _weight_recalc_lock:
                    _weight_recalc_job['running'] = False

        thread = threading.Thread(target=run_recalc, daemon=True)
        thread.start()

        with _weight_recalc_lock:
            _weight_recalc_job['thread'] = thread

        return jsonify({'success': True, 'message': 'Track weight recalculation started'})

    except Exception as e:
        with _weight_recalc_lock:
            _weight_recalc_job['running'] = False
        current_app.logger.exception('Start track weight recalculation failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/admin/recalculate-track-weights/progress')
def recalculate_weights_progress():
    """Get track weight recalculation progress."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503

    try:
        from ..db.ingest import get_weight_recalc_progress

        with _weight_recalc_lock:
            is_running = _weight_recalc_job['running']

        progress = get_weight_recalc_progress()

        return jsonify({
            'success': True,
            'running': is_running,
            'progress': progress,
        })

    except Exception as e:
        current_app.logger.exception('Get weight recalc progress failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/admin/settings')
def admin_settings():
    """Return the current lightweight admin settings."""
    if not _settings_request_allowed():
        return jsonify({
            'success': False,
            'message': 'Admin settings are only available on localhost by default.',
        }), 403

    try:
        return jsonify({
            'success': True,
            'settings': load_settings(),
        })
    except Exception as e:
        current_app.logger.exception('Get admin settings failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/admin/settings', methods=['PUT'])
def update_admin_settings():
    """Persist supported admin settings."""
    if not _settings_request_allowed():
        return jsonify({
            'success': False,
            'message': 'Admin settings are only available on localhost by default.',
        }), 403

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'success': False, 'message': 'Invalid settings payload'}), 400

    try:
        settings = save_settings(payload)
        return jsonify({
            'success': True,
            'message': 'Saved. Restart the app to apply settings changes everywhere.',
            'settings': settings,
        })
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        current_app.logger.exception('Save admin settings failed')
        return jsonify({'success': False, 'message': str(e)}), 500
