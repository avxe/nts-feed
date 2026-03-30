"""Listening session API routes."""

from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from .helpers import db_available, get_db

bp = Blueprint('api_listening', __name__)

MEANINGFUL_LISTEN_SECONDS = 120.0
MEANINGFUL_COMPLETION_RATIO = 0.20
COMPLETED_COMPLETION_RATIO = 0.85


def _parse_iso_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _payload_value(data, context, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, ''):
            return value
    for key in keys:
        value = context.get(key)
        if value not in (None, ''):
            return value
    return None


@bp.route('/api/listening/sessions', methods=['POST'])
def upsert_listening_session():
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503

    data = request.get_json(silent=True) or {}
    context = data.get('context')
    if not isinstance(context, dict):
        context = {}
    session_token = (data.get('session_token') or '').strip()[:128]
    kind = (data.get('kind') or '').strip().lower()[:16]
    player = (data.get('player') or '').strip().lower()[:32]

    if not session_token:
        return jsonify({'success': False, 'message': 'session_token is required'}), 400
    if kind not in {'episode', 'track'}:
        return jsonify({'success': False, 'message': 'kind must be episode or track'}), 400
    if player not in {'nts_audio', 'youtube'}:
        return jsonify({'success': False, 'message': 'player must be nts_audio or youtube'}), 400

    from sqlalchemy import func, or_

    from ..db.models import Artist, Episode, ListeningSession, Show, Track

    show_url = str(_payload_value(data, context, 'show_url') or '').strip()[:1024] or None
    episode_url = str(_payload_value(data, context, 'episode_url') or '').strip()[:1024] or None
    artist_name = str(
        _payload_value(data, context, 'artist_name', 'track_artist', 'artist') or ''
    ).strip()[:512] or None
    track_title = str(
        _payload_value(data, context, 'track_title', 'title') or ''
    ).strip()[:512] or None

    listened_seconds = max(0.0, _safe_float(data.get('listened_seconds')))
    duration_seconds = _safe_float(data.get('duration_seconds'), default=None)
    if duration_seconds is not None and duration_seconds < 0:
        duration_seconds = None
    max_position_seconds = max(0.0, _safe_float(data.get('max_position_seconds')))

    started_at = _parse_iso_datetime(data.get('started_at'))
    last_event_at = _parse_iso_datetime(data.get('last_event_at')) or datetime.utcnow()
    ended_at = _parse_iso_datetime(data.get('ended_at'))

    completion_ratio = 0.0
    if duration_seconds and duration_seconds > 0:
        completion_ratio = min(1.0, listened_seconds / duration_seconds)

    is_meaningful = (
        listened_seconds >= MEANINGFUL_LISTEN_SECONDS
        or completion_ratio >= MEANINGFUL_COMPLETION_RATIO
    )
    is_completed = ended_at is not None or completion_ratio >= COMPLETED_COMPLETION_RATIO

    with get_db()() as session:
        show = None
        if show_url:
            show = session.query(Show).filter(Show.url == show_url).first()

        episode = None
        if episode_url:
            episode = session.query(Episode).filter(Episode.url == episode_url).first()
            if episode and not show:
                show = session.get(Show, episode.show_id)
            if episode and not show_url:
                show_url = show.url if show else None

        track = None
        if kind == 'track' and artist_name and track_title:
            track = (
                session.query(Track)
                .outerjoin(Track.artists)
                .filter(
                    or_(
                        func.lower(Track.title_norm) == track_title.lower(),
                        func.lower(Track.title_original) == track_title.lower(),
                    ),
                    func.lower(Artist.name) == artist_name.lower(),
                )
                .first()
            )

        row = session.query(ListeningSession).filter(
            ListeningSession.session_token == session_token
        ).first()
        if row is None:
            row = ListeningSession(session_token=session_token)
            session.add(row)

        row.kind = kind
        row.player = player
        row.show_url = show_url or row.show_url
        row.episode_url = episode_url or row.episode_url
        row.artist_name = artist_name or row.artist_name
        row.track_title = track_title or row.track_title
        row.show_id = show.id if show else row.show_id
        row.episode_id = episode.id if episode else row.episode_id
        row.track_id = track.id if track else row.track_id
        row.started_at = started_at or row.started_at
        row.last_event_at = last_event_at
        row.ended_at = ended_at or row.ended_at
        row.listened_seconds = listened_seconds
        row.duration_seconds = duration_seconds
        row.max_position_seconds = max_position_seconds
        row.completion_ratio = completion_ratio
        row.is_meaningful = is_meaningful
        row.is_completed = is_completed
        session.commit()

    discover_cache = current_app.extensions.get('discover_cache')
    if isinstance(discover_cache, dict):
        discover_cache.clear()

    return jsonify({
        'success': True,
        'session_token': session_token,
        'is_meaningful': is_meaningful,
        'is_completed': is_completed,
        'completion_ratio': round(completion_ratio, 4),
    })
