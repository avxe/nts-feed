"""Unified SQL-backed search API routes.

The current app needs one predictable relational search path. This module keeps
that contract while ranking cheap exact/prefix matches ahead of broader fuzzy
matches so queries stay useful without dragging in semantic search complexity.
"""

import time

from flask import Blueprint, jsonify, request
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import selectinload

from ..validation import escape_like
from .helpers import db_available, get_db

bp = Blueprint('api_search_unified', __name__)

SEARCH_ENTITY_TYPES = ('show', 'episode', 'track', 'artist', 'genre')
SEARCH_TYPE_ALIASES = {
    'show': 'show',
    'shows': 'show',
    'episode': 'episode',
    'episodes': 'episode',
    'track': 'track',
    'tracks': 'track',
    'artist': 'artist',
    'artists': 'artist',
    'genre': 'genre',
    'genres': 'genre',
}
_MAX_EPISODES_PER_TRACK = 3


def _empty_payload():
    return {
        'success': True,
        'shows': [],
        'episodes': [],
        'tracks': [],
        'artists': [],
        'genres': [],
    }


def _parse_types(raw_types: str):
    parsed = []
    for raw_type in (raw_types or '').split(','):
        normalized = SEARCH_TYPE_ALIASES.get(raw_type.strip().lower())
        if normalized and normalized not in parsed:
            parsed.append(normalized)
    return parsed or list(SEARCH_ENTITY_TYPES)


def _match_patterns(query: str):
    normalized = query.strip().lower()
    escaped = escape_like(normalized)
    return {
        'exact': normalized,
        'prefix': f"{escaped}%",
        'contains': f"%{escaped}%",
    }


def _query_tokens(query: str):
    tokens = []
    seen = set()
    for token in query.strip().lower().split():
        normalized = token.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
    return tokens


def _merge_ranked_ids(groups, limit):
    ordered_ids = []
    seen = set()
    for rows in groups:
        for row in rows:
            if row is None:
                continue
            if isinstance(row, (tuple, list)) or hasattr(row, '_mapping'):
                row_id = row[0]
            else:
                row_id = row
            if row_id in seen:
                continue
            seen.add(row_id)
            ordered_ids.append(row_id)
            if len(ordered_ids) >= limit:
                return ordered_ids
    return ordered_ids


def _id_order_case(model, ids):
    if not ids:
        return model.id.asc()
    return case({row_id: position for position, row_id in enumerate(ids)}, value=model.id, else_=len(ids))


def _enrich_tracks_with_episodes(session, tracks):
    if not tracks:
        return tracks

    from ..db.models import Episode, EpisodeTrack, Show

    track_ids = [track['id'] for track in tracks if track.get('id')]
    if not track_ids:
        return tracks

    rows = (
        session.query(
            EpisodeTrack.track_id,
            Episode.title,
            Episode.url,
            Show.title,
            Show.url,
        )
        .join(Episode, Episode.id == EpisodeTrack.episode_id)
        .join(Show, Show.id == Episode.show_id)
        .filter(EpisodeTrack.track_id.in_(track_ids))
        .order_by(EpisodeTrack.track_id, Episode.date.desc())
        .all()
    )

    grouped = {}
    for track_id, episode_title, episode_url, show_title, show_url in rows:
        episode_rows = grouped.setdefault(track_id, [])
        if len(episode_rows) < _MAX_EPISODES_PER_TRACK:
            episode_rows.append({
                'episode_title': episode_title,
                'episode_url': episode_url,
                'show_title': show_title,
                'show_url': show_url,
            })

    for track in tracks:
        track['episodes'] = grouped.get(track['id'], [])

    return tracks


def _search_tracks(session, query: str, limit: int):
    from ..db.models import Artist, Track

    patterns = _match_patterns(query)
    tokens = _query_tokens(query)

    title_prefix_ids = session.query(Track.id).filter(
        Track.title_norm.like(patterns['prefix'], escape='\\')
    ).order_by(Track.title_norm.asc()).limit(limit).all()

    title_contains_ids = session.query(Track.id).filter(
        Track.title_norm.like(patterns['contains'], escape='\\')
    ).order_by(Track.title_norm.asc()).limit(limit).all()

    artist_prefix_ids = session.query(Track.id).join(Track.artists).filter(
        Artist.name.like(patterns['prefix'], escape='\\')
    ).distinct().order_by(Artist.name.asc(), Track.title_norm.asc()).limit(limit).all()

    artist_contains_ids = session.query(Track.id).join(Track.artists).filter(
        Artist.name.like(patterns['contains'], escape='\\')
    ).distinct().order_by(Artist.name.asc(), Track.title_norm.asc()).limit(limit).all()

    token_match_ids = []
    if tokens:
        token_match_ids = (
            session.query(Track.id)
            .filter(and_(*[
                or_(
                    Track.title_norm.like(f"%{escape_like(token)}%", escape='\\'),
                    Track.artists.any(func.lower(Artist.name).like(f"%{escape_like(token)}%", escape='\\')),
                )
                for token in tokens
            ]))
            .order_by(Track.title_norm.asc())
            .limit(limit)
            .all()
        )

    track_ids = _merge_ranked_ids(
        [title_prefix_ids, artist_prefix_ids, token_match_ids, title_contains_ids, artist_contains_ids],
        limit,
    )
    if not track_ids:
        return []

    tracks = (
        session.query(Track)
        .options(selectinload(Track.artists))
        .filter(Track.id.in_(track_ids))
        .order_by(_id_order_case(Track, track_ids))
        .all()
    )

    results = [{
        'id': track.id,
        'title': track.title_original or track.title_norm,
        'artists': [artist.name for artist in track.artists],
    } for track in tracks]
    return _enrich_tracks_with_episodes(session, results)


def _search_episodes(session, query: str, limit: int):
    from ..db.models import Episode, EpisodeGenre, Genre, Show

    patterns = _match_patterns(query)

    title_prefix_ids = session.query(Episode.id).filter(
        Episode.title.like(patterns['prefix'], escape='\\')
    ).order_by(Episode.date.desc()).limit(limit).all()

    title_contains_ids = session.query(Episode.id).filter(
        Episode.title.like(patterns['contains'], escape='\\')
    ).order_by(Episode.date.desc()).limit(limit).all()

    show_prefix_ids = (
        session.query(Episode.id)
        .join(Show, Show.id == Episode.show_id)
        .filter(Show.title.like(patterns['prefix'], escape='\\'))
        .order_by(Episode.date.desc())
        .limit(limit)
        .all()
    )

    show_contains_ids = (
        session.query(Episode.id)
        .join(Show, Show.id == Episode.show_id)
        .filter(Show.title.like(patterns['contains'], escape='\\'))
        .order_by(Episode.date.desc())
        .limit(limit)
        .all()
    )

    genre_ids = (
        session.query(Episode.id)
        .join(EpisodeGenre, EpisodeGenre.episode_id == Episode.id)
        .join(Genre, Genre.id == EpisodeGenre.genre_id)
        .filter(Genre.name.like(patterns['contains'], escape='\\'))
        .distinct()
        .order_by(Episode.date.desc())
        .limit(limit)
        .all()
    )

    episode_ids = _merge_ranked_ids(
        [title_prefix_ids, show_prefix_ids, title_contains_ids, show_contains_ids, genre_ids],
        limit,
    )
    if not episode_ids:
        return []

    episodes = (
        session.query(Episode)
        .options(
            selectinload(Episode.show),
            selectinload(Episode.genres).selectinload(EpisodeGenre.genre),
        )
        .filter(Episode.id.in_(episode_ids))
        .order_by(_id_order_case(Episode, episode_ids))
        .all()
    )

    results = []
    for episode in episodes:
        matched_genres = sorted({link.genre.name for link in episode.genres if link.genre})
        results.append({
            'id': episode.id,
            'title': episode.title,
            'date': episode.date,
            'show_title': episode.show.title if episode.show else '',
            'show_url': episode.show.url if episode.show else '',
            'image_url': episode.image_url,
            'url': episode.url,
            'matched_genres': matched_genres,
        })
    return results


def _search_shows(session, query: str, limit: int):
    from ..db.models import Show

    patterns = _match_patterns(query)

    prefix_ids = session.query(Show.id).filter(
        Show.title.like(patterns['prefix'], escape='\\')
    ).order_by(Show.title.asc()).limit(limit).all()

    contains_ids = session.query(Show.id).filter(
        Show.title.like(patterns['contains'], escape='\\')
    ).order_by(Show.title.asc()).limit(limit).all()

    description_ids = session.query(Show.id).filter(
        Show.description.like(patterns['contains'], escape='\\')
    ).order_by(Show.title.asc()).limit(limit).all()

    show_ids = _merge_ranked_ids([prefix_ids, contains_ids, description_ids], limit)
    if not show_ids:
        return []

    shows = (
        session.query(Show)
        .filter(Show.id.in_(show_ids))
        .order_by(_id_order_case(Show, show_ids))
        .all()
    )

    return [{
        'id': show.id,
        'title': show.title,
        'url': show.url,
        'thumbnail': show.thumbnail,
        'description': show.description,
    } for show in shows]


def _search_artists(session, query: str, limit: int):
    from ..db.models import Artist, Track

    patterns = _match_patterns(query)

    artist_ids = _merge_ranked_ids([
        session.query(Artist.id).filter(
            Artist.name.like(patterns['prefix'], escape='\\')
        ).order_by(Artist.name.asc()).limit(limit).all(),
        session.query(Artist.id).filter(
            Artist.name.like(patterns['contains'], escape='\\')
        ).order_by(Artist.name.asc()).limit(limit).all(),
    ], limit)

    if not artist_ids:
        return []

    rows = (
        session.query(Artist, func.count(Track.id).label('track_count'))
        .outerjoin(Artist.tracks)
        .filter(Artist.id.in_(artist_ids))
        .group_by(Artist.id)
        .order_by(_id_order_case(Artist, artist_ids))
        .all()
    )

    return [{
        'id': artist.id,
        'name': artist.name,
        'track_count': int(track_count or 0),
    } for artist, track_count in rows]


def _search_genres(session, query: str, limit: int):
    from ..db.models import EpisodeGenre, Genre

    patterns = _match_patterns(query)

    genre_ids = _merge_ranked_ids([
        session.query(Genre.id).filter(
            Genre.name.like(patterns['prefix'], escape='\\')
        ).order_by(Genre.name.asc()).limit(limit).all(),
        session.query(Genre.id).filter(
            Genre.name.like(patterns['contains'], escape='\\')
        ).order_by(Genre.name.asc()).limit(limit).all(),
    ], limit)
    if not genre_ids:
        return []

    rows = (
        session.query(Genre, func.count(EpisodeGenre.id).label('episode_count'))
        .outerjoin(EpisodeGenre, EpisodeGenre.genre_id == Genre.id)
        .filter(Genre.id.in_(genre_ids))
        .group_by(Genre.id)
        .order_by(_id_order_case(Genre, genre_ids))
        .all()
    )

    return [{
        'id': genre.id,
        'name': genre.name,
        'episode_count': int(episode_count or 0),
    } for genre, episode_count in rows]


def _run_search(query: str, entity_types, limit: int):
    if not db_available():
        return _empty_payload()

    with get_db()() as session:
        payload = _empty_payload()
        if 'show' in entity_types:
            payload['shows'] = _search_shows(session, query, limit)
        if 'episode' in entity_types:
            payload['episodes'] = _search_episodes(session, query, limit)
        if 'track' in entity_types:
            payload['tracks'] = _search_tracks(session, query, limit)
        if 'artist' in entity_types:
            payload['artists'] = _search_artists(session, query, limit)
        if 'genre' in entity_types:
            payload['genres'] = _search_genres(session, query, limit)
        return payload


@bp.route('/api/tracks/search')
def api_search_tracks():
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'success': True, 'tracks': []})

    try:
        limit = min(int(request.args.get('limit', 25)), 100)
    except (TypeError, ValueError):
        limit = 25

    payload = _run_search(query, ['track'], limit)
    return jsonify({'success': True, 'tracks': payload['tracks']})


@bp.route('/api/episodes/search')
def api_search_episodes():
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'success': True, 'episodes': []})

    try:
        limit = min(int(request.args.get('limit', 25)), 100)
    except (TypeError, ValueError):
        limit = 25

    payload = _run_search(query, ['episode'], limit)
    return jsonify({'success': True, 'episodes': payload['episodes']})


@bp.route('/api/shows/search')
def api_search_shows():
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'success': True, 'shows': []})

    try:
        limit = min(int(request.args.get('limit', 25)), 100)
    except (TypeError, ValueError):
        limit = 25

    payload = _run_search(query, ['show'], limit)
    return jsonify({'success': True, 'shows': payload['shows']})


@bp.route('/api/search')
def api_unified_search():
    start_time = time.time()

    query = (request.args.get('q') or '').strip()
    if not query:
        payload = _empty_payload()
        payload['stats'] = {'query_time_ms': 0, 'method': 'sql'}
        return jsonify(payload)

    try:
        limit = min(int(request.args.get('limit', 25)), 100)
    except (TypeError, ValueError):
        limit = 25

    entity_types = _parse_types(request.args.get('types', 'show,episode,track,artist,genre'))
    payload = _run_search(query, entity_types, limit)
    payload['stats'] = {
        'query_time_ms': int((time.time() - start_time) * 1000),
        'method': 'sql',
    }
    return jsonify(payload)
