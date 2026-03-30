"""Mixtape persistence plus shared Discover implementation helpers."""

import copy
import hashlib
import random
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request

from ..scrape import load_shows
from .helpers import db_available, get_db, parse_episode_date

bp = Blueprint('api_mixtape', __name__)

DISCOVER_SECTION_LIMIT = 8
DISCOVER_BECAUSE_LIMIT = 16
DISCOVER_GENRE_LIMIT = 6
DISCOVER_GENRE_SHELVES = 20
DISCOVER_CACHE_TTL_SECONDS = 60
LISTENING_GENRE_PROFILE_WEIGHT = 10
RECENT_SHOW_AFFINITY_WEIGHT = 4.0
EXPOSED_EPISODE_PENALTY = 8.0
RECENT_EPISODE_EXTRA_PENALTY = 3.0

_discover_cache_lock = threading.Lock()


def _empty_listening_summary():
    return {
        'episode_listens': 0,
        'track_listens': 0,
        'top_shows': [],
        'top_artists': [],
        'top_genres': [],
    }


def _empty_discover_payload():
    return {
        'success': True,
        'sections': {
            'continue_listening': [],
            'because_you_like': [],
            'genre_spotlight': [],
        },
        'listening_summary': _empty_listening_summary(),
    }


def _empty_discover_state():
    return {
        'candidates': [],
        'genre_shelves': [],
        'surprise_pool': [],
        'listening_summary': _empty_listening_summary(),
    }


def _get_discover_cache_store():
    return current_app.extensions.setdefault('discover_cache', {})


def _get_discover_cache_ttl_seconds() -> int:
    try:
        ttl = int(current_app.config.get('DISCOVER_CACHE_TTL_SECONDS', DISCOVER_CACHE_TTL_SECONDS))
    except (TypeError, ValueError):
        ttl = DISCOVER_CACHE_TTL_SECONDS
    return max(0, ttl)


def _discover_subscription_signature(subscribed) -> str:
    subscribed_urls = sorted(url for url in (subscribed or {}).keys() if url)
    if not subscribed_urls:
        return ''
    return hashlib.sha1('\n'.join(subscribed_urls).encode('utf-8')).hexdigest()


def _prune_expired_discover_cache(cache_store, now: float) -> None:
    expired_keys = [
        cache_key
        for cache_key, entry in cache_store.items()
        if entry.get('expires_at', 0) <= now
    ]
    for cache_key in expired_keys:
        cache_store.pop(cache_key, None)


def _slugify_genre(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', (value or '').strip().lower()).strip('-')


def _serialize_episode_card(candidate, reason_label=None, matched_genres=None):
    return {
        'episode_id': candidate['episode_id'],
        'episode_title': candidate['episode_title'],
        'episode_url': candidate['episode_url'],
        'episode_date': candidate['episode_date'],
        'episode_image_url': candidate['episode_image_url'],
        'show_id': candidate['show_id'],
        'show_title': candidate['show_title'],
        'show_url': candidate['show_url'],
        'matched_genres': matched_genres if matched_genres is not None else candidate.get('matched_genres', []),
        'reason_label': reason_label or candidate.get('reason_label', ''),
        'tracklist_peek': candidate.get('tracklist_peek', []),
    }


def _episode_timestamp(episode_date: str) -> int:
    parsed = parse_episode_date(episode_date)
    return int(parsed.timestamp()) if parsed else 0


def _pick_diverse_candidates(candidates, limit, score_key='score'):
    remaining = list(candidates)
    selected = []
    show_counts = Counter()

    while remaining and len(selected) < limit:
        remaining.sort(
            key=lambda item: (
                item[score_key] - (1.5 * show_counts[item['show_id']]),
                item['sort_timestamp'],
                item['episode_id'],
            ),
            reverse=True,
        )
        chosen = remaining.pop(0)
        selected.append(chosen)
        show_counts[chosen['show_id']] += 1

    return selected


def _choose_reason_label(candidate):
    if candidate.get('recent_show_affinity', 0) > 0:
        return 'Based on recent listening'
    if candidate['liked_artist_overlap'] > 0:
        return 'Matches liked artists'
    if candidate['liked_track_overlap'] > 0:
        return 'Contains liked tracks'
    if candidate['genre_overlap'] > 0:
        return 'Strong genre overlap'
    if candidate['show_affinity'] > 0:
        return 'From a favorite show'
    return 'Fresh from your shows'


def _load_discover_catalog(subscribed=None):
    if not db_available():
        return None

    subscribed = subscribed if subscribed is not None else (load_shows() or {})
    subscribed_urls = list(subscribed.keys())
    if not subscribed_urls:
        return {
            'episodes': [],
            'episode_genres_by_id': {},
            'liked_track_overlap_by_episode': {},
            'liked_artist_overlap_by_episode': {},
            'genre_profile': Counter(),
            'genre_name_map': {},
            'show_affinity': Counter(),
        }

    from sqlalchemy import func, select

    from ..db.models import (
        Artist,
        Episode,
        EpisodeGenre,
        EpisodeInboxState,
        EpisodeTrack,
        Genre,
        LikedEpisode,
        LikedTrack,
        ListeningSession,
        Show,
        track_artists,
    )

    with get_db()() as session:
        show_rows = session.execute(
            select(Show.id, Show.url, Show.title)
            .where(Show.url.in_(subscribed_urls))
        ).all()
        if not show_rows:
            return {
                'episodes': [],
                'episode_genres_by_id': {},
                'liked_track_overlap_by_episode': {},
                'liked_artist_overlap_by_episode': {},
                'genre_profile': Counter(),
                'genre_name_map': {},
                'show_affinity': Counter(),
            }

        show_by_id = {
            row.id: {
                'show_url': row.url,
                'show_title': row.title,
            }
            for row in show_rows
        }
        show_ids = list(show_by_id.keys())

        episode_rows = session.execute(
            select(
                Episode.id,
                Episode.show_id,
                Episode.url,
                Episode.title,
                Episode.date,
                Episode.image_url,
            )
            .where(Episode.show_id.in_(show_ids))
        ).all()
        if not episode_rows:
            return {
                'episodes': [],
                'episode_genres_by_id': {},
                'liked_track_overlap_by_episode': {},
                'liked_artist_overlap_by_episode': {},
                'genre_profile': Counter(),
                'genre_name_map': {},
                'show_affinity': Counter(),
            }

        episode_ids = [row.id for row in episode_rows]
        episode_genre_rows = session.execute(
            select(EpisodeGenre.episode_id, Genre.name)
            .join(Genre, Genre.id == EpisodeGenre.genre_id)
            .where(EpisodeGenre.episode_id.in_(episode_ids))
        ).all()

        liked_tracks = session.execute(
            select(
                LikedTrack.track_id,
                LikedTrack.artist,
                LikedTrack.episode_url,
                LikedTrack.show_title,
            )
        ).all()
        liked_episodes = session.execute(
            select(
                LikedEpisode.episode_id,
                LikedEpisode.episode_url,
                LikedEpisode.show_title,
                LikedEpisode.show_url,
            )
        ).all()
        cutoff = datetime.utcnow() - timedelta(days=30)
        listening_rows = session.execute(
            select(
                ListeningSession.kind,
                ListeningSession.show_id,
                ListeningSession.episode_id,
                ListeningSession.show_url,
                ListeningSession.episode_url,
                ListeningSession.artist_name,
                ListeningSession.last_event_at,
            )
            .where(
                ListeningSession.is_meaningful.is_(True),
                ListeningSession.last_event_at.is_not(None),
                ListeningSession.last_event_at >= cutoff,
            )
            .order_by(ListeningSession.last_event_at.desc(), ListeningSession.id.desc())
        ).all()

        completed_episode_ids = set(session.execute(
            select(ListeningSession.episode_id)
            .where(
                ListeningSession.kind == 'episode',
                ListeningSession.is_completed.is_(True),
                ListeningSession.episode_id.is_not(None),
            )
        ).scalars().all())

        exposed_episode_ids = set(session.execute(
            select(ListeningSession.episode_id)
            .where(
                ListeningSession.is_meaningful.is_(True),
                ListeningSession.episode_id.is_not(None),
            )
        ).scalars().all())

        now = datetime.utcnow()
        dismissed_or_snoozed_urls = set()
        for row in session.execute(select(EpisodeInboxState)).scalars().all():
            if row.dismissed_at is not None:
                dismissed_or_snoozed_urls.add(row.episode_url)
            elif row.snoozed_until and row.snoozed_until > now:
                dismissed_or_snoozed_urls.add(row.episode_url)

        liked_track_ids = sorted({
            liked_track.track_id
            for liked_track in liked_tracks
            if liked_track.track_id
        })
        liked_artist_names = sorted({
            liked_track.artist.strip().casefold()
            for liked_track in liked_tracks
            if liked_track.artist and liked_track.artist.strip()
        })

        track_overlap_rows = []
        liked_track_genre_rows = []
        if liked_track_ids:
            track_overlap_rows = session.execute(
                select(EpisodeTrack.episode_id, EpisodeTrack.track_id)
                .where(
                    EpisodeTrack.episode_id.in_(episode_ids),
                    EpisodeTrack.track_id.in_(liked_track_ids),
                )
            ).all()
            liked_track_genre_rows = session.execute(
                select(EpisodeTrack.track_id, Genre.name, func.count())
                .join(EpisodeGenre, EpisodeGenre.episode_id == EpisodeTrack.episode_id)
                .join(Genre, Genre.id == EpisodeGenre.genre_id)
                .where(
                    EpisodeTrack.episode_id.in_(episode_ids),
                    EpisodeTrack.track_id.in_(liked_track_ids),
                )
                .group_by(EpisodeTrack.track_id, Genre.name)
            ).all()

        artist_overlap_rows = []
        if liked_artist_names:
            artist_overlap_rows = session.execute(
                select(EpisodeTrack.episode_id, func.lower(Artist.name))
                .join(track_artists, track_artists.c.track_id == EpisodeTrack.track_id)
                .join(Artist, Artist.id == track_artists.c.artist_id)
                .where(
                    EpisodeTrack.episode_id.in_(episode_ids),
                    func.lower(Artist.name).in_(liked_artist_names),
                )
            ).all()

        episode_artist_rows = session.execute(
            select(EpisodeTrack.episode_id, Artist.name)
            .join(track_artists, track_artists.c.track_id == EpisodeTrack.track_id)
            .join(Artist, Artist.id == track_artists.c.artist_id)
            .where(EpisodeTrack.episode_id.in_(episode_ids))
            .order_by(EpisodeTrack.episode_id, EpisodeTrack.id)
        ).all()

    tracklist_artists_by_episode = defaultdict(list)
    _seen_artist_keys = defaultdict(set)
    for episode_id, artist_name in episode_artist_rows:
        if artist_name:
            key = artist_name.casefold()
            if key not in _seen_artist_keys[episode_id]:
                _seen_artist_keys[episode_id].add(key)
                tracklist_artists_by_episode[episode_id].append(artist_name)

    liked_artist_names_set = set(liked_artist_names)

    show_affinity = Counter()
    for liked_track in liked_tracks:
        if liked_track.show_title:
            show_affinity[liked_track.show_title] += 1
    for liked_episode in liked_episodes:
        if liked_episode.show_url:
            show_affinity[liked_episode.show_url] += 1
        if liked_episode.show_title:
            show_affinity[liked_episode.show_title] += 1

    genre_profile = Counter()
    genre_name_map = {}
    episode_genres_by_id = defaultdict(list)
    episode_genre_keys_by_id = defaultdict(set)
    show_title_by_id = {}
    show_title_by_url = {}
    for show_id, show_info in show_by_id.items():
        title = show_info.get('show_title', '')
        url = show_info.get('show_url', '')
        show_title_by_id[show_id] = title
        if url:
            show_title_by_url[url] = title
    for episode_id, genre_name in episode_genre_rows:
        episode_genres_by_id[episode_id].append(genre_name)
        genre_key = genre_name.casefold()
        episode_genre_keys_by_id[episode_id].add(genre_key)
        genre_profile[genre_key] += 1
        genre_name_map.setdefault(genre_key, genre_name)

    episodes = []
    episode_by_id = {}
    episode_by_url = {}
    for row in episode_rows:
        show_info = show_by_id.get(row.show_id, {})
        episode = {
            'episode_id': row.id,
            'episode_title': row.title,
            'episode_url': row.url,
            'episode_date': row.date,
            'episode_image_url': row.image_url,
            'show_id': row.show_id,
            'show_title': show_info.get('show_title', ''),
            'show_url': show_info.get('show_url', ''),
            'all_genres': sorted(set(episode_genres_by_id.get(row.id, []))),
            'all_genre_keys': set(episode_genre_keys_by_id.get(row.id, set())),
            'sort_timestamp': _episode_timestamp(row.date),
        }
        episodes.append(episode)
        episode_by_id[row.id] = episode
        episode_by_url[row.url] = episode

    for _track_id, genre_name, count in liked_track_genre_rows:
        genre_key = genre_name.casefold()
        genre_profile[genre_key] += 3 * count
        genre_name_map.setdefault(genre_key, genre_name)

    for liked_track in liked_tracks:
        episode = episode_by_url.get(liked_track.episode_url)
        if not episode:
            continue
        for genre_name in episode_genres_by_id.get(episode['episode_id'], []):
            genre_key = genre_name.casefold()
            genre_profile[genre_key] += 3
            genre_name_map.setdefault(genre_key, genre_name)

    for liked_episode in liked_episodes:
        episode = None
        if liked_episode.episode_id:
            episode = episode_by_id.get(liked_episode.episode_id)
        if episode is None and liked_episode.episode_url:
            episode = episode_by_url.get(liked_episode.episode_url)
        if not episode:
            continue
        for genre_name in episode_genres_by_id.get(episode['episode_id'], []):
            genre_key = genre_name.casefold()
            genre_profile[genre_key] += 4
            genre_name_map.setdefault(genre_key, genre_name)

    liked_source_episode_ids = set()
    for liked_track in liked_tracks:
        ep = episode_by_url.get(liked_track.episode_url)
        if ep:
            liked_source_episode_ids.add(ep['episode_id'])
    for liked_episode in liked_episodes:
        ep = None
        if liked_episode.episode_id:
            ep = episode_by_id.get(liked_episode.episode_id)
        if ep is None and liked_episode.episode_url:
            ep = episode_by_url.get(liked_episode.episode_url)
        if ep:
            liked_source_episode_ids.add(ep['episode_id'])

    exposed_episode_ids |= liked_source_episode_ids

    liked_track_overlap_by_episode = defaultdict(set)
    for episode_id, track_id in track_overlap_rows:
        liked_track_overlap_by_episode[episode_id].add(track_id)

    liked_artist_overlap_by_episode = defaultdict(set)
    for episode_id, artist_name in artist_overlap_rows:
        if artist_name:
            liked_artist_overlap_by_episode[episode_id].add(artist_name)

    listening_show_affinity = Counter()
    listening_artist_affinity = Counter()
    listening_genre_counts = Counter()
    recent_episode_ids = set()
    recent_show_ids = set()
    recent_show_urls = set()
    episode_listens = 0
    track_listens = 0
    for row in listening_rows:
        if row.kind == 'episode':
            episode_listens += 1
        elif row.kind == 'track':
            track_listens += 1
            if row.artist_name:
                listening_artist_affinity[row.artist_name] += 1

        if row.show_id:
            listening_show_affinity[row.show_id] += 1
            recent_show_ids.add(row.show_id)
        elif row.show_url:
            listening_show_affinity[row.show_url] += 1
            recent_show_urls.add(row.show_url)

        if row.episode_id:
            recent_episode_ids.add(row.episode_id)
            for genre_name in episode_genres_by_id.get(row.episode_id, []):
                listening_genre_counts[genre_name] += 1

    listening_summary = {
        'episode_listens': episode_listens,
        'track_listens': track_listens,
        'top_shows': [
            {
                'name': show_title_by_id.get(show_key, '') if isinstance(show_key, int) else show_title_by_url.get(show_key, ''),
                'count': count,
            }
            for show_key, count in listening_show_affinity.most_common(5)
            if (show_title_by_id.get(show_key, '') if isinstance(show_key, int) else show_title_by_url.get(show_key, ''))
        ],
        'top_artists': [
            {'name': artist_name, 'count': count}
            for artist_name, count in listening_artist_affinity.most_common(5)
            if artist_name
        ],
        'top_genres': [
            {'name': genre_name, 'count': count}
            for genre_name, count in listening_genre_counts.most_common(5)
            if genre_name
        ],
    }
    for genre_name, count in listening_genre_counts.items():
        genre_key = genre_name.casefold()
        genre_profile[genre_key] += LISTENING_GENRE_PROFILE_WEIGHT * count
        genre_name_map.setdefault(genre_key, genre_name)

    return {
        'episodes': episodes,
        'episode_genres_by_id': dict(episode_genres_by_id),
        'liked_track_overlap_by_episode': dict(liked_track_overlap_by_episode),
        'liked_artist_overlap_by_episode': dict(liked_artist_overlap_by_episode),
        'genre_profile': genre_profile,
        'genre_name_map': genre_name_map,
        'show_affinity': show_affinity,
        'listening_show_affinity': listening_show_affinity,
        'listening_artist_affinity': listening_artist_affinity,
        'recent_episode_ids': recent_episode_ids,
        'recent_show_ids': recent_show_ids,
        'recent_show_urls': recent_show_urls,
        'listening_summary': listening_summary,
        'tracklist_artists_by_episode': dict(tracklist_artists_by_episode),
        'liked_artist_names_set': liked_artist_names_set,
        'completed_episode_ids': completed_episode_ids,
        'exposed_episode_ids': exposed_episode_ids,
        'liked_source_episode_ids': liked_source_episode_ids,
        'dismissed_or_snoozed_urls': dismissed_or_snoozed_urls,
    }


def _build_discover_state(subscribed=None):
    catalog = _load_discover_catalog(subscribed=subscribed)
    if catalog is None:
        return None

    episodes = catalog['episodes']
    if not episodes:
        return _empty_discover_state()

    top_genres = [
        name
        for name, _ in catalog['genre_profile'].most_common(DISCOVER_GENRE_SHELVES + 1)
    ]
    top_genre_set = set(top_genres)

    recency_ranked = sorted(
        episodes,
        key=lambda episode: episode['sort_timestamp'],
        reverse=True,
    )
    recency_boosts = {}
    total_episodes = max(1, len(recency_ranked) - 1)
    for index, episode in enumerate(recency_ranked):
        recency_boosts[episode['episode_id']] = 1.0 - (index / max(1, total_episodes))

    dismissed_urls = catalog.get('dismissed_or_snoozed_urls', set())

    candidates = []
    for episode in episodes:
        if episode['episode_url'] in dismissed_urls:
            continue
        genre_keys = episode['all_genre_keys']
        liked_artist_overlap = len(
            catalog['liked_artist_overlap_by_episode'].get(episode['episode_id'], set())
        )
        liked_track_overlap = len(
            catalog['liked_track_overlap_by_episode'].get(episode['episode_id'], set())
        )
        genre_overlap = len(genre_keys & top_genre_set)
        show_overlap = (
            catalog['show_affinity'].get(episode['show_url'], 0)
            + catalog['show_affinity'].get(episode['show_title'], 0)
        )
        recent_show_affinity = (
            catalog['listening_show_affinity'].get(episode['show_id'], 0)
            + catalog['listening_show_affinity'].get(episode['show_url'], 0)
        )
        exposed_penalty = 1 if episode['episode_id'] in catalog['exposed_episode_ids'] else 0
        recent_penalty = 1 if episode['episode_id'] in catalog['recent_episode_ids'] else 0
        recency_boost = recency_boosts.get(episode['episode_id'], 0)
        score = (
            (4.0 * liked_artist_overlap)
            + (3.0 * liked_track_overlap)
            + (2.5 * genre_overlap)
            + (2.0 * show_overlap)
            + (RECENT_SHOW_AFFINITY_WEIGHT * recent_show_affinity)
            + recency_boost
            - (EXPOSED_EPISODE_PENALTY * exposed_penalty)
            - (RECENT_EPISODE_EXTRA_PENALTY * recent_penalty)
        )

        matched_genres = [catalog['genre_name_map'][key] for key in top_genres if key in genre_keys]
        candidate = {
            **episode,
            'matched_genres': matched_genres or episode['all_genres'][:3],
            'liked_artist_overlap': liked_artist_overlap,
            'liked_track_overlap': liked_track_overlap,
            'genre_overlap': genre_overlap,
            'show_affinity': show_overlap,
            'recent_show_affinity': recent_show_affinity,
            'exposed_penalty': exposed_penalty,
            'recent_penalty': recent_penalty,
            'recency_boost': recency_boost,
            'score': score,
        }
        candidate['reason_label'] = _choose_reason_label(candidate)

        all_artists = catalog['tracklist_artists_by_episode'].get(episode['episode_id'], [])
        liked_set = catalog['liked_artist_names_set']
        liked_peek = [a for a in all_artists if a.casefold() in liked_set]
        other_peek = [a for a in all_artists if a.casefold() not in liked_set]
        candidate['tracklist_peek'] = (liked_peek + other_peek)[:10]

        candidates.append(candidate)

    candidates.sort(key=lambda item: (item['score'], item['sort_timestamp'], item['episode_id']), reverse=True)

    genre_shelves = []
    for genre_key in top_genres[:DISCOVER_GENRE_SHELVES]:
        matching = [
            candidate
            for candidate in candidates
            if genre_key in candidate['all_genre_keys']
        ]
        if not matching:
            continue
        picked = _pick_diverse_candidates(matching, DISCOVER_GENRE_LIMIT)
        genre_shelves.append({
            'genre': catalog['genre_name_map'].get(genre_key, genre_key.title()),
            'slug': _slugify_genre(catalog['genre_name_map'].get(genre_key, genre_key)),
            'episodes': [
                _serialize_episode_card(
                    candidate,
                    reason_label='Genre spotlight',
                    matched_genres=[catalog['genre_name_map'].get(genre_key, genre_key.title())],
                )
                for candidate in picked
            ],
        })

    show_counts = Counter(candidate['show_id'] for candidate in candidates)
    surprise_pool = sorted(
        candidates,
        key=lambda item: (
            show_counts[item['show_id']],
            len(item['matched_genres']),
            -item['sort_timestamp'],
            item['episode_id'],
        ),
    )

    return {
        'candidates': candidates,
        'genre_shelves': genre_shelves,
        'surprise_pool': surprise_pool,
        'listening_summary': catalog.get('listening_summary', _empty_listening_summary()),
        'completed_episode_ids': catalog.get('completed_episode_ids', set()),
        'liked_source_episode_ids': catalog.get('liked_source_episode_ids', set()),
    }


def _serialize_discover_payload(state):
    payload = _empty_discover_payload()
    candidates = state['candidates']
    if not candidates:
        return payload

    already_engaged_ids = (
        state.get('completed_episode_ids', set())
        | state.get('liked_source_episode_ids', set())
    )
    recommendable = [
        c for c in candidates
        if c['episode_id'] not in already_engaged_ids
    ]
    selected = _pick_diverse_candidates(recommendable, DISCOVER_BECAUSE_LIMIT)
    serialized = [_serialize_episode_card(c) for c in selected]

    surprise_candidates = [
        s for s in state['surprise_pool']
        if s['episode_id'] not in already_engaged_ids
    ]
    if surprise_candidates:
        surprise = surprise_candidates[0]
        existing_ids = {card['episode_id'] for card in serialized}
        if surprise['episode_id'] not in existing_ids:
            serialized.insert(
                min(2, len(serialized)),
                _serialize_episode_card(surprise, reason_label='Surprise episode'),
            )

    payload['sections']['because_you_like'] = serialized
    payload['sections']['genre_spotlight'] = state['genre_shelves']
    payload['listening_summary'] = state.get('listening_summary', _empty_listening_summary())
    return payload


def _get_cached_discover_bundle():
    if not db_available():
        return None

    subscribed = load_shows() or {}
    if not subscribed:
        state = _empty_discover_state()
        return {'state': state, 'payload': _serialize_discover_payload(state)}

    signature = _discover_subscription_signature(subscribed)
    cache_ttl = _get_discover_cache_ttl_seconds()
    now = time.time()

    if cache_ttl > 0 and signature:
        with _discover_cache_lock:
            cache_store = _get_discover_cache_store()
            _prune_expired_discover_cache(cache_store, now)
            entry = cache_store.get(signature)
            if entry and entry.get('expires_at', 0) > now:
                return entry['bundle']
            # Keep the miss path under the same lock so duplicate page inits do
            # not trigger two identical cold rebuilds in parallel.
            state = _build_discover_state(subscribed=subscribed)
            if state is None:
                return None
            bundle = {
                'state': state,
                'payload': _serialize_discover_payload(state),
            }
            cache_store[signature] = {
                'bundle': bundle,
                'expires_at': now + cache_ttl,
            }
            return bundle

    state = _build_discover_state(subscribed=subscribed)
    if state is None:
        return None

    bundle = {
        'state': state,
        'payload': _serialize_discover_payload(state),
    }

    return bundle


def _get_fresh_continue_listening():
    """Return in-progress episodes (always fresh, never cached)."""
    if not db_available():
        return []
    try:
        from sqlalchemy import select as sa_select

        from ..db.models import (
            Artist,
            Episode,
            EpisodeInboxState,
            EpisodeTrack,
            ListeningSession,
            track_artists,
        )

        with get_db()() as session:
            listening_rows = session.execute(
                sa_select(ListeningSession).where(
                    ListeningSession.kind == 'episode',
                    ListeningSession.last_event_at.is_not(None),
                    ListeningSession.is_completed.is_(False),
                ).order_by(ListeningSession.last_event_at.desc())
            ).scalars().all()
            if not listening_rows:
                return []

            inbox_rows = session.execute(sa_select(EpisodeInboxState)).scalars().all()
            now = datetime.utcnow()
            excluded = set()
            for row in inbox_rows:
                if row.dismissed_at is not None:
                    excluded.add(row.episode_url)
                elif row.snoozed_until and row.snoozed_until > now:
                    excluded.add(row.episode_url)
                elif row.saved_for_later:
                    excluded.add(row.episode_url)

            seen: dict = {}
            for row in listening_rows:
                ep = None
                if row.episode_url:
                    ep = session.execute(
                        sa_select(Episode).where(Episode.url == row.episode_url)
                    ).scalars().first()
                if ep is None and row.episode_id:
                    ep = session.get(Episode, row.episode_id)
                if ep is None or ep.url in excluded or ep.url in seen:
                    continue
                seen[ep.url] = {
                    'episode_id': ep.id,
                    'episode_title': ep.title,
                    'episode_url': ep.url,
                    'episode_date': ep.date,
                    'episode_image_url': ep.image_url,
                    'show_id': ep.show_id,
                    'show_title': ep.show.title if ep.show else '',
                    'show_url': ep.show.url if ep.show else '',
                    'reason_label': 'Continue listening',
                    'matched_genres': [],
                    'tracklist_peek': [],
                }

            cards = list(seen.values())[:DISCOVER_SECTION_LIMIT]
            if not cards:
                return cards

            card_ids = [c['episode_id'] for c in cards]
            ep_artist_rows = session.execute(
                sa_select(EpisodeTrack.episode_id, Artist.name)
                .join(track_artists, track_artists.c.track_id == EpisodeTrack.track_id)
                .join(Artist, Artist.id == track_artists.c.artist_id)
                .where(EpisodeTrack.episode_id.in_(card_ids))
                .order_by(EpisodeTrack.episode_id, EpisodeTrack.id)
            ).all()
            peek_map: dict[int, list] = defaultdict(list)
            peek_seen: dict[int, set] = defaultdict(set)
            for eid, aname in ep_artist_rows:
                if aname:
                    key = aname.casefold()
                    if key not in peek_seen[eid]:
                        peek_seen[eid].add(key)
                        peek_map[eid].append(aname)
            for card in cards:
                card['tracklist_peek'] = peek_map.get(card['episode_id'], [])[:10]

            return cards
    except Exception:
        current_app.logger.exception('Continue listening fetch failed')
        return []


def api_discover():
    try:
        bundle = _get_cached_discover_bundle()
        if bundle is None:
            return jsonify({'success': False, 'message': 'Database not available'}), 503

        payload = copy.deepcopy(bundle['payload'])

        try:
            payload['sections']['continue_listening'] = _get_fresh_continue_listening()
        except Exception:
            current_app.logger.exception('Continue listening fetch failed')
            payload['sections']['continue_listening'] = []

        response = jsonify(payload)
        response.headers['Cache-Control'] = f"private, max-age={_get_discover_cache_ttl_seconds()}"
        return response
    except Exception as exc:
        current_app.logger.exception('Discover load failed')
        return jsonify({'success': False, 'message': str(exc)}), 500


def api_discover_surprise():
    try:
        bundle = _get_cached_discover_bundle()
        if bundle is None:
            return jsonify({'success': False, 'message': 'Database not available'}), 503
        state = bundle['state']
        if not state['surprise_pool']:
            return jsonify({'success': True, 'episode': None})

        pool = state['surprise_pool'][: min(6, len(state['surprise_pool']))]
        episode = random.choice(pool)
        return jsonify({
            'success': True,
            'episode': _serialize_episode_card(episode, reason_label='Surprise episode'),
        })
    except Exception as exc:
        current_app.logger.exception('Discover surprise failed')
        return jsonify({'success': False, 'message': str(exc)}), 500


def _build_genre_shelf_on_demand(genre_slug: str, state=None):
    """Build a genre shelf from the already-scored discover candidates.

    Episodes are filtered by genre then ranked by the same personalization
    score used for "Because you like" (liked-artist overlap, liked-track
    overlap, show affinity, listening history, recency).  Falls back to a
    raw DB query only when no scored candidates are available.
    """
    genre_key = genre_slug.replace('-', ' ').lower()

    if state and state.get('candidates'):
        candidates = state['candidates']
        matching = [
            c for c in candidates
            if genre_key in c.get('all_genre_keys', set())
        ]
        if not matching:
            for c in candidates:
                for g in c.get('all_genres', []):
                    if _slugify_genre(g) == genre_slug:
                        matching.append(c)
                        break

        if matching:
            picked = _pick_diverse_candidates(matching, DISCOVER_GENRE_LIMIT)
            genre_display = None
            for c in picked:
                for g in c.get('all_genres', []):
                    if g.casefold() == genre_key or _slugify_genre(g) == genre_slug:
                        genre_display = g
                        break
                if genre_display:
                    break
            genre_display = genre_display or genre_key.title()

            return {
                'genre': genre_display,
                'slug': _slugify_genre(genre_display),
                'episodes': [
                    _serialize_episode_card(
                        c,
                        reason_label=_choose_reason_label(c),
                        matched_genres=[genre_display],
                    )
                    for c in picked
                ],
            }

    if not db_available():
        return None

    try:
        from sqlalchemy import select

        from ..db.models import (
            Artist,
            Episode,
            EpisodeGenre,
            EpisodeTrack,
            Genre,
            Show,
            track_artists,
        )

        subscribed = load_shows() or {}
        subscribed_urls = list(subscribed.keys())
        if not subscribed_urls:
            return None

        with get_db()() as session:
            all_genres = session.execute(
                select(Genre.id, Genre.name)
            ).all()
            genre_row = None
            for row in all_genres:
                if _slugify_genre(row.name) == genre_slug:
                    genre_row = row
                    break
            if not genre_row:
                for row in all_genres:
                    if row.name.lower() == genre_key:
                        genre_row = row
                        break
            if not genre_row:
                return None

            show_ids = [
                row[0] for row in session.execute(
                    select(Show.id).where(Show.url.in_(subscribed_urls))
                ).all()
            ]
            if not show_ids:
                return None

            episode_rows = session.execute(
                select(
                    Episode.id, Episode.show_id, Episode.url,
                    Episode.title, Episode.date, Episode.image_url,
                    Show.url.label('show_url'), Show.title.label('show_title'),
                )
                .join(Show, Show.id == Episode.show_id)
                .join(EpisodeGenre, EpisodeGenre.episode_id == Episode.id)
                .where(
                    Episode.show_id.in_(show_ids),
                    EpisodeGenre.genre_id == genre_row.id,
                )
                .order_by(Episode.id.desc())
                .limit(DISCOVER_GENRE_LIMIT)
            ).all()

            if not episode_rows:
                return None

            ep_ids = [r.id for r in episode_rows]
            artist_rows = session.execute(
                select(EpisodeTrack.episode_id, Artist.name)
                .join(track_artists, track_artists.c.track_id == EpisodeTrack.track_id)
                .join(Artist, Artist.id == track_artists.c.artist_id)
                .where(EpisodeTrack.episode_id.in_(ep_ids))
                .order_by(EpisodeTrack.episode_id, EpisodeTrack.id)
            ).all()

            peek_map = defaultdict(list)
            peek_seen = defaultdict(set)
            for eid, aname in artist_rows:
                if aname:
                    key = aname.casefold()
                    if key not in peek_seen[eid]:
                        peek_seen[eid].add(key)
                        peek_map[eid].append(aname)

            episodes = []
            for row in episode_rows:
                episodes.append({
                    'episode_id': row.id,
                    'episode_title': row.title,
                    'episode_url': row.url,
                    'episode_date': row.date,
                    'episode_image_url': row.image_url,
                    'show_id': row.show_id,
                    'show_title': row.show_title,
                    'show_url': row.show_url,
                    'matched_genres': [genre_row.name],
                    'reason_label': 'Genre spotlight',
                    'tracklist_peek': peek_map.get(row.id, [])[:10],
                })

            return {
                'genre': genre_row.name,
                'slug': _slugify_genre(genre_row.name),
                'episodes': episodes,
            }
    except Exception:
        current_app.logger.exception('On-demand genre shelf build failed')
        return None


def api_discover_genre(genre_slug: str):
    try:
        bundle = _get_cached_discover_bundle()
        if bundle is None:
            return jsonify({'success': False, 'message': 'Database not available'}), 503
        state = bundle['state']

        for shelf in state['genre_shelves']:
            if shelf['slug'] == genre_slug:
                response = jsonify({'success': True, 'genre': shelf['genre'], 'episodes': shelf['episodes']})
                response.headers['Cache-Control'] = f"private, max-age={_get_discover_cache_ttl_seconds()}"
                return response

        result = _build_genre_shelf_on_demand(genre_slug, state=state)
        if result:
            response = jsonify({'success': True, 'genre': result['genre'], 'episodes': result['episodes']})
            response.headers['Cache-Control'] = f"private, max-age={_get_discover_cache_ttl_seconds()}"
            return response

        return jsonify({'success': False, 'message': 'Genre not found'}), 404
    except Exception as exc:
        current_app.logger.exception('Discover genre shelf failed')
        return jsonify({'success': False, 'message': str(exc)}), 500


def api_discover_next_up():
    try:
        from ..services.next_up_service import build_next_up_payload

        payload = build_next_up_payload()
        if payload is None:
            return jsonify({'success': False, 'message': 'Database not available'}), 503
        return jsonify(payload)
    except Exception as exc:
        current_app.logger.exception('Next Up load failed')
        return jsonify({'success': False, 'message': str(exc)}), 500


def api_discover_next_up_state():
    try:
        from ..services.next_up_service import mutate_next_up_state

        data = request.get_json(silent=True) or {}
        response = mutate_next_up_state(
            episode_url=data.get('episode_url'),
            action=data.get('action'),
            snooze_days=data.get('snooze_days'),
        )
        if not response.get('success'):
            status = 503 if response.get('message') == 'Database not available' else 400
            return jsonify(response), status
        return jsonify(response)
    except Exception as exc:
        current_app.logger.exception('Next Up state update failed')
        return jsonify({'success': False, 'message': str(exc)}), 500


@bp.route('/api/mixtape/save', methods=['POST'])
def api_mixtape_save():
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503

    try:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip() or 'Mixtape'
        track_ids = data.get('track_ids', [])
        if not track_ids:
            return jsonify({'success': False, 'message': 'No tracks provided'}), 400

        from ..db.models import Mixtape, MixtapeTrack

        with get_db()() as session:
            mixtape = Mixtape(name=name, seed_track_ids=[], seed_genres=[])
            session.add(mixtape)
            session.flush()
            for index, track_id in enumerate(track_ids):
                session.add(MixtapeTrack(mixtape_id=mixtape.id, track_id=int(track_id), position=index))
            session.commit()
            return jsonify({'success': True, 'id': mixtape.id})
    except Exception as exc:
        current_app.logger.exception('Mixtape save failed')
        return jsonify({'success': False, 'message': str(exc)}), 500


@bp.route('/api/mixtapes', methods=['POST'])
def api_save_mixtape():
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503

    try:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip() or 'Mixtape'
        seed_track_ids = data.get('seed_track_ids', [])
        seed_genres = data.get('seed_genres', [])
        track_ids = data.get('track_ids', [])
        if not track_ids:
            return jsonify({'success': False, 'message': 'No tracks provided'}), 400

        from ..db.models import Mixtape, MixtapeTrack

        with get_db()() as session:
            mixtape = Mixtape(name=name, seed_track_ids=seed_track_ids, seed_genres=seed_genres)
            session.add(mixtape)
            session.flush()
            for index, track_id in enumerate(track_ids):
                session.add(MixtapeTrack(mixtape_id=mixtape.id, track_id=int(track_id), position=index))
            session.commit()
            return jsonify({'success': True, 'id': mixtape.id})
    except Exception as exc:
        current_app.logger.exception('Save mixtape failed')
        return jsonify({'success': False, 'message': str(exc)}), 500


@bp.route('/api/mixtapes', methods=['GET'])
def api_list_mixtapes():
    if not db_available():
        return jsonify({'success': True, 'mixtapes': []})

    try:
        from sqlalchemy import func

        from ..db.models import Mixtape, MixtapeTrack

        with get_db()() as session:
            rows = (
                session.query(Mixtape, func.count(MixtapeTrack.id).label('track_count'))
                .outerjoin(MixtapeTrack, MixtapeTrack.mixtape_id == Mixtape.id)
                .group_by(Mixtape.id)
                .order_by(Mixtape.created_at.desc())
                .all()
            )
            return jsonify({
                'success': True,
                'mixtapes': [{
                    'id': mixtape.id,
                    'name': mixtape.name,
                    'created_at': mixtape.created_at.isoformat() if mixtape.created_at else None,
                    'count': int(track_count or 0),
                } for mixtape, track_count in rows],
            })
    except Exception as exc:
        current_app.logger.exception('List mixtapes failed')
        return jsonify({'success': False, 'message': str(exc)}), 500


@bp.route('/api/mixtapes/<int:mixtape_id>', methods=['GET'])
def api_get_mixtape(mixtape_id: int):
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503

    try:
        from ..db.models import Mixtape, MixtapeTrack, Track

        with get_db()() as session:
            mixtape = session.get(Mixtape, mixtape_id)
            if not mixtape:
                return jsonify({'success': False, 'message': 'Not found'}), 404

            track_rows = (
                session.query(MixtapeTrack, Track)
                .join(Track, Track.id == MixtapeTrack.track_id)
                .filter(MixtapeTrack.mixtape_id == mixtape.id)
                .order_by(MixtapeTrack.position.asc())
                .all()
            )
            tracks = [{
                'track_id': track.id,
                'title': track.title_original or track.title_norm,
                'artists': [artist.name for artist in track.artists],
                'position': mixtape_track.position,
            } for mixtape_track, track in track_rows]

            return jsonify({
                'success': True,
                'mixtape': {
                    'id': mixtape.id,
                    'name': mixtape.name,
                    'created_at': mixtape.created_at.isoformat() if mixtape.created_at else None,
                    'seed_track_ids': mixtape.seed_track_ids,
                    'seed_genres': mixtape.seed_genres,
                    'tracks': tracks,
                },
            })
    except Exception as exc:
        current_app.logger.exception('Get mixtape failed')
        return jsonify({'success': False, 'message': str(exc)}), 500
