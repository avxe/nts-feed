"""Unified SQL-backed search API routes.

Uses raw SQL UNION queries for fast ranked ID lookups, then loads
full objects by ID for serialization.  This keeps the ranking step
at ~80ms total instead of 500ms+ with per-pattern ORM queries.
"""

import time

from flask import Blueprint, jsonify, request
from sqlalchemy import text
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


def _ranked_ids(session, sql, params, limit):
    """Execute a UNION-based ranking query and return ordered IDs."""
    wrapped = text(f"SELECT id FROM ({sql}) GROUP BY id ORDER BY MIN(rank) LIMIT :lim")
    params['lim'] = limit
    return [row[0] for row in session.execute(wrapped, params).fetchall()]


def _search_tracks(session, query: str, limit: int):
    from ..db.models import Episode, EpisodeTrack, Show, Track

    patterns = _match_patterns(query)

    sql = """
        SELECT id, 1 as rank FROM tracks
        WHERE lower(title_norm) LIKE :prefix ESCAPE '\\'
      UNION ALL
        SELECT id, 3 as rank FROM tracks
        WHERE lower(title_norm) LIKE :contains ESCAPE '\\'
      UNION ALL
        SELECT DISTINCT ta.track_id as id, 4 as rank
        FROM track_artists ta JOIN artists a ON a.id = ta.artist_id
        WHERE lower(a.name) LIKE :prefix ESCAPE '\\'
      UNION ALL
        SELECT DISTINCT ta.track_id as id, 5 as rank
        FROM track_artists ta JOIN artists a ON a.id = ta.artist_id
        WHERE lower(a.name) LIKE :contains ESCAPE '\\'
    """
    params = {
        'prefix': patterns['prefix'],
        'contains': patterns['contains'],
    }

    # Multi-token search: if the query has multiple words, add a cross-field
    # match that finds tracks where every token appears in either the title
    # or an associated artist name.
    tokens = query.strip().lower().split()
    if len(tokens) > 1:
        token_clauses = []
        for i, token in enumerate(tokens):
            tok_esc = escape_like(token)
            pkey = f'mt{i}'
            params[pkey] = f'%{tok_esc}%'
            token_clauses.append(
                f"(lower(t.title_norm) LIKE :{pkey} ESCAPE '\\'"
                f" OR EXISTS (SELECT 1 FROM track_artists ta2"
                f" JOIN artists a2 ON a2.id = ta2.artist_id"
                f" WHERE ta2.track_id = t.id"
                f" AND lower(a2.name) LIKE :{pkey} ESCAPE '\\'))"
            )
        multi_where = " AND ".join(token_clauses)
        sql += f"""
      UNION ALL
        SELECT t.id, 2 as rank FROM tracks t
        WHERE {multi_where}
        """

    track_ids = _ranked_ids(session, sql, params, limit)

    if not track_ids:
        return []

    tracks = (
        session.query(Track)
        .options(selectinload(Track.artists))
        .filter(Track.id.in_(track_ids))
        .all()
    )

    id_order = {tid: i for i, tid in enumerate(track_ids)}
    tracks.sort(key=lambda t: id_order.get(t.id, 999))

    results = [{
        'id': track.id,
        'title': track.title_original or track.title_norm,
        'artists': [artist.name for artist in track.artists],
    } for track in tracks]

    # Enrich with episode info
    if results:
        ep_rows = (
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
        for track_id, ep_title, ep_url, show_title, show_url in ep_rows:
            episode_rows = grouped.setdefault(track_id, [])
            if len(episode_rows) < _MAX_EPISODES_PER_TRACK:
                episode_rows.append({
                    'episode_title': ep_title,
                    'episode_url': ep_url,
                    'show_title': show_title,
                    'show_url': show_url,
                })

        for track in results:
            track['episodes'] = grouped.get(track['id'], [])

    return results


def _search_episodes(session, query: str, limit: int):
    from ..db.models import Episode, EpisodeGenre

    patterns = _match_patterns(query)

    sql = """
        SELECT e.id, 1 as rank FROM episodes e
        WHERE lower(e.title) LIKE :prefix ESCAPE '\\'
      UNION ALL
        SELECT e.id, 2 as rank FROM episodes e JOIN shows s ON s.id = e.show_id
        WHERE lower(s.title) LIKE :prefix ESCAPE '\\'
      UNION ALL
        SELECT e.id, 3 as rank FROM episodes e
        WHERE lower(e.title) LIKE :contains ESCAPE '\\'
      UNION ALL
        SELECT e.id, 4 as rank FROM episodes e JOIN shows s ON s.id = e.show_id
        WHERE lower(s.title) LIKE :contains ESCAPE '\\'
      UNION ALL
        SELECT DISTINCT eg.episode_id as id, 5 as rank
        FROM episode_genres eg JOIN genres g ON g.id = eg.genre_id
        WHERE lower(g.name) LIKE :contains ESCAPE '\\'
    """
    episode_ids = _ranked_ids(session, sql, {
        'prefix': patterns['prefix'],
        'contains': patterns['contains'],
    }, limit)

    if not episode_ids:
        return []

    episodes = (
        session.query(Episode)
        .options(
            selectinload(Episode.show),
            selectinload(Episode.genres).selectinload(EpisodeGenre.genre),
        )
        .filter(Episode.id.in_(episode_ids))
        .all()
    )

    id_order = {eid: i for i, eid in enumerate(episode_ids)}
    episodes.sort(key=lambda e: id_order.get(e.id, 999))

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

    sql = """
        SELECT id, 1 as rank FROM shows
        WHERE lower(title) LIKE :prefix ESCAPE '\\'
      UNION ALL
        SELECT id, 2 as rank FROM shows
        WHERE lower(title) LIKE :contains ESCAPE '\\'
      UNION ALL
        SELECT id, 3 as rank FROM shows
        WHERE lower(description) LIKE :contains ESCAPE '\\'
    """
    show_ids = _ranked_ids(session, sql, {
        'prefix': patterns['prefix'],
        'contains': patterns['contains'],
    }, limit)

    if not show_ids:
        return []

    shows = session.query(Show).filter(Show.id.in_(show_ids)).all()
    id_order = {sid: i for i, sid in enumerate(show_ids)}
    shows.sort(key=lambda s: id_order.get(s.id, 999))

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

    sql = """
        SELECT id, 1 as rank FROM artists
        WHERE lower(name) LIKE :prefix ESCAPE '\\'
      UNION ALL
        SELECT id, 2 as rank FROM artists
        WHERE lower(name) LIKE :contains ESCAPE '\\'
    """
    artist_ids = _ranked_ids(session, sql, {
        'prefix': patterns['prefix'],
        'contains': patterns['contains'],
    }, limit)

    if not artist_ids:
        return []

    from sqlalchemy import func

    rows = (
        session.query(Artist, func.count(Track.id).label('track_count'))
        .outerjoin(Artist.tracks)
        .filter(Artist.id.in_(artist_ids))
        .group_by(Artist.id)
        .all()
    )

    id_order = {aid: i for i, aid in enumerate(artist_ids)}
    rows.sort(key=lambda r: id_order.get(r[0].id, 999))

    return [{
        'id': artist.id,
        'name': artist.name,
        'track_count': int(track_count or 0),
    } for artist, track_count in rows]


def _search_genres(session, query: str, limit: int):
    from ..db.models import EpisodeGenre, Genre

    patterns = _match_patterns(query)

    sql = """
        SELECT id, 1 as rank FROM genres
        WHERE lower(name) LIKE :prefix ESCAPE '\\'
      UNION ALL
        SELECT id, 2 as rank FROM genres
        WHERE lower(name) LIKE :contains ESCAPE '\\'
    """
    genre_ids = _ranked_ids(session, sql, {
        'prefix': patterns['prefix'],
        'contains': patterns['contains'],
    }, limit)

    if not genre_ids:
        return []

    from sqlalchemy import func

    rows = (
        session.query(Genre, func.count(EpisodeGenre.id).label('episode_count'))
        .outerjoin(EpisodeGenre, EpisodeGenre.genre_id == Genre.id)
        .filter(Genre.id.in_(genre_ids))
        .group_by(Genre.id)
        .all()
    )

    id_order = {gid: i for i, gid in enumerate(genre_ids)}
    rows.sort(key=lambda r: id_order.get(r[0].id, 999))

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
