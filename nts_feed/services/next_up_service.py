"""Next Up ranking and inbox-state service for Discover."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from flask import current_app
from sqlalchemy import func, select

from ..blueprints.helpers import db_available, get_db, parse_episode_date
from ..db.models import Episode, EpisodeGenre, EpisodeInboxState, EpisodeTrack, Genre, LikedEpisode, LikedTrack, ListeningSession

NEXT_UP_SECTION_LIMIT = 8
NEXT_UP_BRIDGE_PER_SHOW_LIMIT = 2
NEXT_UP_SNOOZE_DAYS_DEFAULT = 7
NEXT_UP_RECENT_LISTEN_BOOST = 5.0
NEXT_UP_REPEAT_SHOW_PENALTY = 1.5
NEXT_UP_BRIDGE_SHOW_PENALTY = 0.75
NEXT_UP_LISTENING_GENRE_PROFILE_WEIGHT = 10


def _discover_api():
    from ..blueprints import api_discover as discover_api

    return discover_api


def _clear_discover_cache() -> None:
    discover_cache = current_app.extensions.get('discover_cache')
    if isinstance(discover_cache, dict):
        discover_cache.clear()


def _episode_timestamp(episode_date: str) -> int:
    parsed = parse_episode_date(episode_date)
    return int(parsed.timestamp()) if parsed else 0


def _resolve_episode(session, episode_url: str | None, episode_id: int | None):
    if episode_url:
        episode = session.execute(
            select(Episode).where(Episode.url == episode_url)
        ).scalars().first()
        if episode:
            return episode
    if episode_id:
        episode = session.get(Episode, episode_id)
        if episode:
            return episode
    return None


def _episode_card_from_episode(episode: Episode) -> dict[str, Any]:
    return {
        'episode_id': episode.id,
        'episode_title': episode.title,
        'episode_url': episode.url,
        'episode_date': episode.date,
        'episode_image_url': episode.image_url,
        'show_id': episode.show_id,
        'show_title': episode.show.title if episode.show else '',
        'show_url': episode.show.url if episode.show else '',
        'sort_timestamp': _episode_timestamp(episode.date),
    }


def _episode_card_from_catalog(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        'episode_id': entry['episode_id'],
        'episode_title': entry['episode_title'],
        'episode_url': entry['episode_url'],
        'episode_date': entry['episode_date'],
        'episode_image_url': entry['episode_image_url'],
        'show_id': entry['show_id'],
        'show_title': entry['show_title'],
        'show_url': entry['show_url'],
        'sort_timestamp': entry['sort_timestamp'],
    }


def _add_actions(card: dict[str, Any], actions: list[dict[str, str]]) -> dict[str, Any]:
    item = dict(card)
    item['actions'] = actions
    return item


def _state_label(row: EpisodeInboxState) -> str | None:
    if row.dismissed_at is not None:
        return 'dismissed'
    if row.snoozed_until is not None and row.snoozed_until > datetime.utcnow():
        return 'snoozed'
    if row.saved_for_later:
        return 'saved_for_later'
    return None


def _load_episode_inbox_rows(session) -> list[tuple[EpisodeInboxState, Episode | None]]:
    rows: list[tuple[EpisodeInboxState, Episode | None]] = []
    inbox_rows = session.execute(
        select(EpisodeInboxState).order_by(
            EpisodeInboxState.updated_at.desc(),
            EpisodeInboxState.created_at.desc(),
            EpisodeInboxState.id.desc(),
        )
    ).scalars().all()
    for row in inbox_rows:
        episode = _resolve_episode(session, row.episode_url, row.episode_id)
        rows.append((row, episode))
    return rows


def _build_state_indexes(inbox_rows: list[tuple[EpisodeInboxState, Episode | None]], now: datetime):
    saved_rows = []
    dismissed_urls = set()
    snoozed_urls = set()
    saved_urls = set()
    inbox_by_url: dict[str, tuple[EpisodeInboxState, Episode | None]] = {}

    for row, episode in inbox_rows:
        inbox_by_url[row.episode_url] = (row, episode)
        if row.dismissed_at is not None:
            dismissed_urls.add(row.episode_url)
        if row.snoozed_until is not None and row.snoozed_until > now:
            snoozed_urls.add(row.episode_url)
        if row.saved_for_later:
            saved_urls.add(row.episode_url)
            saved_rows.append((row, episode))

    saved_rows.sort(
        key=lambda item: (
            item[0].updated_at or item[0].created_at or datetime.min,
            item[0].created_at or datetime.min,
            item[0].id,
        ),
        reverse=True,
    )

    return {
        'saved_rows': saved_rows,
        'saved_urls': saved_urls,
        'dismissed_urls': dismissed_urls,
        'snoozed_urls': snoozed_urls,
        'inbox_by_url': inbox_by_url,
    }


def _build_next_up_personalization(catalog: dict[str, Any], session):
    episode_ids = [episode['episode_id'] for episode in catalog['episodes']]
    episode_genres_by_id = catalog['episode_genres_by_id']
    genre_profile = Counter()
    genre_name_map = dict(catalog['genre_name_map'])
    show_title_by_id = {episode['show_id']: episode['show_title'] for episode in catalog['episodes']}
    show_title_by_url = {
        episode['show_url']: episode['show_title']
        for episode in catalog['episodes']
        if episode['show_url']
    }

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

    liked_track_ids = sorted({row.track_id for row in liked_tracks if row.track_id})
    if liked_track_ids and episode_ids:
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
        for _track_id, genre_name, count in liked_track_genre_rows:
            genre_key = genre_name.casefold()
            genre_profile[genre_key] += 3 * count
            genre_name_map.setdefault(genre_key, genre_name)

    for liked_track in liked_tracks:
        episode = _resolve_episode(session, liked_track.episode_url, None)
        if not episode:
            continue
        for genre_name in episode_genres_by_id.get(episode.id, []):
            genre_key = genre_name.casefold()
            genre_profile[genre_key] += 3
            genre_name_map.setdefault(genre_key, genre_name)

    for liked_episode in liked_episodes:
        episode = _resolve_episode(session, liked_episode.episode_url, liked_episode.episode_id)
        if not episode:
            continue
        for genre_name in episode_genres_by_id.get(episode.id, []):
            genre_key = genre_name.casefold()
            genre_profile[genre_key] += 4
            genre_name_map.setdefault(genre_key, genre_name)

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
            ListeningSession.last_event_at >= datetime.utcnow() - timedelta(days=30),
        )
        .order_by(ListeningSession.last_event_at.desc(), ListeningSession.id.desc())
    ).all()

    listening_show_affinity = Counter()
    listening_artist_affinity = Counter()
    listening_genre_counts = Counter()
    recent_episode_ids = set()
    recent_show_ids = set()
    recent_show_urls = set()
    episode_listens = 0
    track_listens = 0

    for row in listening_rows:
        episode = _resolve_episode(session, row.episode_url, row.episode_id)
        if row.kind == 'episode':
            episode_listens += 1
        elif row.kind == 'track':
            track_listens += 1
            if row.artist_name:
                listening_artist_affinity[row.artist_name] += 1

        if episode is not None:
            recent_episode_ids.add(episode.id)
            if episode.show_id:
                listening_show_affinity[episode.show_id] += 1
                recent_show_ids.add(episode.show_id)
            elif episode.show and episode.show.url:
                listening_show_affinity[episode.show.url] += 1
                recent_show_urls.add(episode.show.url)
            for genre_name in episode_genres_by_id.get(episode.id, []):
                listening_genre_counts[genre_name] += 1
        else:
            if row.show_id:
                listening_show_affinity[row.show_id] += 1
                recent_show_ids.add(row.show_id)
            elif row.show_url:
                listening_show_affinity[row.show_url] += 1
                recent_show_urls.add(row.show_url)
            if row.episode_id:
                recent_episode_ids.add(row.episode_id)

    for genre_name, count in listening_genre_counts.items():
        genre_key = genre_name.casefold()
        genre_profile[genre_key] += NEXT_UP_LISTENING_GENRE_PROFILE_WEIGHT * count
        genre_name_map.setdefault(genre_key, genre_name)

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

    return {
        'genre_profile': genre_profile,
        'genre_name_map': genre_name_map,
        'listening_show_affinity': listening_show_affinity,
        'listening_artist_affinity': listening_artist_affinity,
        'recent_episode_ids': recent_episode_ids,
        'recent_show_ids': recent_show_ids,
        'recent_show_urls': recent_show_urls,
        'listening_summary': listening_summary,
    }


def _build_continue_listening(catalog: dict[str, Any], inbox_indexes: dict[str, Any], session) -> list[dict[str, Any]]:
    episode_rows = session.execute(
        select(ListeningSession).where(
            ListeningSession.kind == 'episode',
            ListeningSession.last_event_at.is_not(None),
            ListeningSession.is_completed.is_(False),
        ).order_by(ListeningSession.last_event_at.desc(), ListeningSession.id.desc())
    ).scalars().all()

    latest_by_episode_url: dict[str, tuple[ListeningSession, Episode]] = {}
    for row in episode_rows:
        episode = _resolve_episode(session, row.episode_url, row.episode_id)
        if episode is None:
            continue
        episode_url = episode.url
        if episode_url in inbox_indexes['saved_urls']:
            continue
        if episode_url in inbox_indexes['dismissed_urls']:
            continue
        if episode_url in inbox_indexes['snoozed_urls']:
            continue
        current = latest_by_episode_url.get(episode_url)
        if current is None or (row.last_event_at or datetime.min) > (current[0].last_event_at or datetime.min):
            latest_by_episode_url[episode_url] = (row, episode)

    cards = []
    for row, episode in sorted(
        latest_by_episode_url.values(),
        key=lambda item: (
            item[0].last_event_at or datetime.min,
            item[0].id,
        ),
        reverse=True,
    ):
        card = _episode_card_from_episode(episode)
        cards.append(_add_actions(card, [
            {'action': 'play', 'label': 'Play'},
            {'action': 'save', 'label': 'Save for later'},
            {'action': 'dismiss', 'label': 'Dismiss'},
        ]))

    return cards[:NEXT_UP_SECTION_LIMIT]


def _recent_genre_keys(catalog: dict[str, Any]) -> list[str]:
    return [name for name, _ in catalog['genre_profile'].most_common(4)]


def _score_discover_candidate(candidate: dict[str, Any], catalog: dict[str, Any], top_genres: list[str]) -> tuple[float, dict[str, Any]]:
    liked_artist_overlap = len(catalog['liked_artist_overlap_by_episode'].get(candidate['episode_id'], set()))
    liked_track_overlap = len(catalog['liked_track_overlap_by_episode'].get(candidate['episode_id'], set()))
    genre_keys = candidate['all_genre_keys']
    genre_overlap = len(genre_keys & set(top_genres))
    show_overlap = (
        catalog['show_affinity'].get(candidate['show_url'], 0)
        + catalog['show_affinity'].get(candidate['show_title'], 0)
    )
    recent_show_affinity = (
        catalog['listening_show_affinity'].get(candidate['show_id'], 0)
        + catalog['listening_show_affinity'].get(candidate['show_url'], 0)
    )
    recency_boost = 0.0
    if candidate['sort_timestamp'] > 0:
        newest = max(episode['sort_timestamp'] for episode in catalog['episodes']) or 1
        recency_boost = candidate['sort_timestamp'] / newest
    score = (
        (4.5 * liked_artist_overlap)
        + (3.5 * liked_track_overlap)
        + (2.75 * genre_overlap)
        + (1.75 * show_overlap)
        + (NEXT_UP_RECENT_LISTEN_BOOST * recent_show_affinity)
        + recency_boost
        - (NEXT_UP_REPEAT_SHOW_PENALTY * max(0, recent_show_affinity - 1))
    )
    card = {
        **candidate,
        'liked_artist_overlap': liked_artist_overlap,
        'liked_track_overlap': liked_track_overlap,
        'genre_overlap': genre_overlap,
        'show_overlap': show_overlap,
        'recent_show_affinity': recent_show_affinity,
        'score': score,
    }
    if recent_show_affinity > 0:
        card['reason_label'] = 'Based on recent listening'
    elif liked_artist_overlap > 0:
        card['reason_label'] = 'Matches liked artists'
    elif liked_track_overlap > 0:
        card['reason_label'] = 'Contains liked tracks'
    elif genre_overlap > 0:
        card['reason_label'] = 'Strong genre overlap'
    elif show_overlap > 0:
        card['reason_label'] = 'From a favorite show'
    else:
        card['reason_label'] = 'Fresh from your shows'
    card['matched_genres'] = [
        catalog['genre_name_map'][genre_key]
        for genre_key in top_genres
        if genre_key in genre_keys and genre_key in catalog['genre_name_map']
    ] or candidate['all_genres'][:3]
    return score, card


def _build_play_next(catalog: dict[str, Any], inbox_indexes: dict[str, Any], continue_listening_urls: set[str]) -> list[dict[str, Any]]:
    top_genres = _recent_genre_keys(catalog)
    completed_episode_urls = set()

    with get_db()() as session:
        completed_rows = session.execute(
            select(ListeningSession.episode_url, ListeningSession.episode_id).where(
                ListeningSession.kind == 'episode',
                ListeningSession.is_completed.is_(True),
            )
        ).all()
        for episode_url, episode_id in completed_rows:
            episode = _resolve_episode(session, episode_url, episode_id)
            if episode is not None:
                completed_episode_urls.add(episode.url)
            elif episode_url:
                completed_episode_urls.add(episode_url)

    scored = []
    for candidate in catalog['episodes']:
        episode_url = candidate['episode_url']
        if episode_url in continue_listening_urls:
            continue
        if episode_url in inbox_indexes['saved_urls']:
            continue
        if episode_url in inbox_indexes['dismissed_urls']:
            continue
        if episode_url in inbox_indexes['snoozed_urls']:
            continue
        if episode_url in completed_episode_urls:
            continue
        score, card = _score_discover_candidate(candidate, catalog, top_genres)
        public_card = _episode_card_from_catalog(card)
        public_card['reason_label'] = card['reason_label']
        public_card['matched_genres'] = card['matched_genres']
        public_card['score'] = score
        scored.append(_add_actions(public_card, [
            {'action': 'play', 'label': 'Play'},
            {'action': 'save', 'label': 'Save for later'},
            {'action': 'dismiss', 'label': 'Dismiss'},
        ]))

    scored.sort(key=lambda item: (item.get('score', 0), item['sort_timestamp'], item['episode_id']), reverse=True)
    return scored[:NEXT_UP_SECTION_LIMIT]


def _build_curiosity_bridges(catalog: dict[str, Any], inbox_indexes: dict[str, Any], continue_listening_urls: set[str]) -> list[dict[str, Any]]:
    top_genres = _recent_genre_keys(catalog)
    show_play_counts = Counter(catalog['listening_show_affinity'])

    per_show_counts = defaultdict(int)
    candidates = []
    for candidate in catalog['episodes']:
        episode_url = candidate['episode_url']
        if episode_url in continue_listening_urls:
            continue
        if episode_url in inbox_indexes['dismissed_urls']:
            continue

        artist_overlap = len(catalog['liked_artist_overlap_by_episode'].get(candidate['episode_id'], set()))
        track_overlap = len(catalog['liked_track_overlap_by_episode'].get(candidate['episode_id'], set()))
        genre_overlap = len(candidate['all_genre_keys'] & set(top_genres))
        recent_show_affinity = (
            catalog['listening_show_affinity'].get(candidate['show_id'], 0)
            + catalog['listening_show_affinity'].get(candidate['show_url'], 0)
        )
        show_overlap = (
            catalog['show_affinity'].get(candidate['show_url'], 0)
            + catalog['show_affinity'].get(candidate['show_title'], 0)
        )
        score = (
            (4.0 * artist_overlap)
            + (3.0 * track_overlap)
            + (2.5 * genre_overlap)
            + (1.5 * show_overlap)
            + (1.5 * recent_show_affinity)
            - (
                NEXT_UP_BRIDGE_SHOW_PENALTY
                * (
                    show_play_counts.get(candidate['show_id'], 0)
                    + show_play_counts.get(candidate['show_url'], 0)
                )
            )
        )

        if score <= 0:
            continue

        if artist_overlap > 0 and genre_overlap > 0:
            reason_label = 'Bridge between liked artists and recent genres'
        elif artist_overlap > 0:
            reason_label = 'Bridge from liked artists'
        elif genre_overlap > 0:
            reason_label = 'Bridge from recent genres'
        elif recent_show_affinity > 0:
            reason_label = 'Bridge from recent listening'
        else:
            reason_label = 'Bridge from your listening history'

        public_card = _episode_card_from_catalog(candidate)
        public_card['score'] = score
        public_card['reason_label'] = reason_label
        public_card['matched_genres'] = [
            catalog['genre_name_map'][genre_key]
            for genre_key in top_genres
            if genre_key in candidate['all_genre_keys'] and genre_key in catalog['genre_name_map']
        ] or candidate['all_genres'][:3]

        if per_show_counts[candidate['show_id']] >= NEXT_UP_BRIDGE_PER_SHOW_LIMIT:
            continue
        per_show_counts[candidate['show_id']] += 1
        candidates.append(_add_actions(public_card, [
            {'action': 'play', 'label': 'Play'},
            {'action': 'save', 'label': 'Save for later'},
            {'action': 'dismiss', 'label': 'Dismiss'},
        ]))

    candidates.sort(key=lambda item: (item.get('score', 0), item['sort_timestamp'], item['episode_id']), reverse=True)
    return candidates[:NEXT_UP_SECTION_LIMIT]


def _build_saved_for_later(inbox_indexes: dict[str, Any]) -> list[dict[str, Any]]:
    cards = []
    for row, episode in inbox_indexes['saved_rows']:
        if episode is None:
            continue
        card = _episode_card_from_episode(episode)
        card['saved_for_later'] = True
        card['saved_at'] = (row.updated_at or row.created_at or datetime.utcnow()).isoformat() if (row.updated_at or row.created_at) else None
        cards.append(_add_actions(card, [
            {'action': 'play', 'label': 'Play'},
            {'action': 'unsave', 'label': 'Remove'},
            {'action': 'dismiss', 'label': 'Dismiss'},
        ]))
    return cards[:NEXT_UP_SECTION_LIMIT]


def build_next_up_payload(subscribed=None) -> dict[str, Any] | None:
    if not db_available():
        return None

    discover_api = _discover_api()
    catalog = discover_api._load_discover_catalog(subscribed=subscribed)
    if catalog is None:
        return None

    with get_db()() as session:
        listening_context = _build_next_up_personalization(catalog, session)
        next_up_catalog = {**catalog, **listening_context}
        inbox_rows = _load_episode_inbox_rows(session)
        now = datetime.utcnow()
        inbox_indexes = _build_state_indexes(inbox_rows, now)

        continue_listening = _build_continue_listening(next_up_catalog, inbox_indexes, session)
        continue_urls = {card['episode_url'] for card in continue_listening}
        play_next = _build_play_next(next_up_catalog, inbox_indexes, continue_urls)
        curiosity_bridges = _build_curiosity_bridges(next_up_catalog, inbox_indexes, continue_urls)
        saved_for_later = _build_saved_for_later(inbox_indexes)

    return {
        'success': True,
        'sections': {
            'continue_listening': continue_listening,
            'play_next': play_next,
            'curiosity_bridges': curiosity_bridges,
            'saved_for_later': saved_for_later,
        },
        'listening_summary': next_up_catalog.get('listening_summary', {}),
    }


def mutate_next_up_state(episode_url: str, action: str, snooze_days: int | None = None) -> dict[str, Any]:
    if not db_available():
        return {'success': False, 'message': 'Database not available'}

    episode_url = (episode_url or '').strip()[:1024]
    action = (action or '').strip().lower()
    if not episode_url:
        return {'success': False, 'message': 'episode_url is required'}
    if action not in {'save', 'unsave', 'snooze', 'dismiss'}:
        return {'success': False, 'message': 'Invalid action'}

    now = datetime.utcnow()
    episode = None
    with get_db()() as session:
        episode = _resolve_episode(session, episode_url, None)
        row = session.execute(
            select(EpisodeInboxState).where(EpisodeInboxState.episode_url == episode_url)
        ).scalars().first()
        if row is None and episode is not None:
            row = EpisodeInboxState(episode_url=episode.url, episode_id=episode.id)
            session.add(row)
        elif row is None:
            row = EpisodeInboxState(episode_url=episode_url)
            session.add(row)

        if episode is not None:
            row.episode_id = episode.id
            row.episode_url = episode.url

        if action == 'save':
            row.saved_for_later = True
            row.snoozed_until = None
            row.dismissed_at = None
        elif action == 'unsave':
            row.saved_for_later = False
            row.snoozed_until = None
            row.dismissed_at = None
        elif action == 'snooze':
            days = snooze_days if snooze_days is not None else NEXT_UP_SNOOZE_DAYS_DEFAULT
            row.saved_for_later = False
            row.snoozed_until = now + timedelta(days=max(1, int(days)))
            row.dismissed_at = None
        elif action == 'dismiss':
            row.saved_for_later = False
            row.snoozed_until = None
            row.dismissed_at = now

        session.commit()

    _clear_discover_cache()
    return {
        'success': True,
        'episode_url': episode_url,
        'action': action,
    }
