"""
Database Ingestion Module - Optimized for Performance

Industry best practices applied:
- Batch/bulk insert operations instead of row-by-row
- In-memory lookup caches for frequently accessed entities
- Chunked processing with configurable batch sizes
- Minimized database round-trips
- Progress callbacks for monitoring
- Incremental update support
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import threading
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sqlalchemy import select, text, insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.dialects import postgresql as pg_dialect

from ..runtime_paths import episodes_dir as runtime_episodes_dir, shows_path as runtime_shows_path
from ..storage.paths import get_rebuild_lock_path
from .models import (
    Base,
    Show,
    Episode,
    Genre,
    Artist,
    ArtistGenre,
    Track,
    EpisodeTrack,
    EpisodeGenre,
    Tag,
    TrackTag,
    track_artists,
)

logger = logging.getLogger(__name__)

# Configuration
DEFAULT_BATCH_SIZE = 500
DEFAULT_COMMIT_INTERVAL = 1000

# Tables containing user data that should NOT be deleted during rebuild
USER_DATA_TABLES = frozenset({
    'liked_tracks',
    'liked_episodes',
    'listening_sessions',
    'episode_inbox_state',
    'user_playlists',
    'user_playlist_tracks',
    'mixtapes',
    'mixtape_tracks',
})


@dataclass
class IngestProgress:
    """Progress tracking for database ingestion."""
    phase: str = "initializing"
    total_shows: int = 0
    processed_shows: int = 0
    total_episodes: int = 0
    processed_episodes: int = 0
    entities_created: Dict[str, int] = None
    
    def __post_init__(self):
        if self.entities_created is None:
            self.entities_created = defaultdict(int)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "total_shows": self.total_shows,
            "processed_shows": self.processed_shows,
            "total_episodes": self.total_episodes,
            "processed_episodes": self.processed_episodes,
            "entities_created": dict(self.entities_created),
            "progress_pct": round(
                (self.processed_episodes / max(1, self.total_episodes)) * 100, 1
            )
        }


class LookupCache:
    """In-memory cache for entity lookups to minimize database round-trips.
    
    This is a key optimization - instead of querying the database for each
    genre/artist/tag, we pre-load them and maintain the cache in memory.
    """
    
    def __init__(self):
        self.genres: Dict[str, int] = {}  # name -> id
        self.artists: Dict[str, int] = {}  # normalized_name -> id
        self.tags: Dict[str, int] = {}  # name -> id
        self.tracks: Dict[Tuple[str, str], int] = {}  # (title_norm, artist_hash) -> id
        self.episodes: Dict[str, int] = {}  # url -> id
        
    def load_from_session(self, session) -> None:
        """Pre-load all lookup tables from database.
        
        Optimized to select only required columns (id, name/key) instead of
        loading full ORM objects, reducing memory usage and query time.
        """
        # Load genres - select only id and name columns
        for gid, name in session.execute(select(Genre.id, Genre.name)).all():
            self.genres[name] = gid
        
        # Load artists - select only id and name columns
        for aid, name in session.execute(select(Artist.id, Artist.name)).all():
            self.artists[name] = aid
        
        # Load tags - select only id and name columns
        for tid, name in session.execute(select(Tag.id, Tag.name)).all():
            self.tags[name] = tid
        
        # Load tracks - select only required columns for key construction
        for trid, title_norm, artist_hash in session.execute(
            select(Track.id, Track.title_norm, Track.canonical_artist_set_hash)
        ).all():
            self.tracks[(title_norm, artist_hash)] = trid
        
        # Load episodes - select only id and url columns
        for epid, url in session.execute(select(Episode.id, Episode.url)).all():
            self.episodes[url] = epid
        
        logger.info(
            f"LookupCache loaded: {len(self.genres)} genres, "
            f"{len(self.artists)} artists, {len(self.tags)} tags, "
            f"{len(self.tracks)} tracks, {len(self.episodes)} episodes"
        )


def _normalize_name(name: str) -> str:
    """Unicode-preserving normalization for titles, artists, and genres.

    - Lowercase and collapse whitespace
    - Preserve letters/digits across all Unicode scripts
    - Keep a small safe set of punctuation common in names
    - Remove other symbols/control characters
    """
    raw = (name or "").lower().strip()
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw)
    allowed_punct = set([" ", "&", "+", "/", ".", "-", "'", ",", ":", ";", "(", ")"])
    out_chars: List[str] = []
    for ch in raw:
        if ch.isalnum() or ch in allowed_punct:
            out_chars.append(ch)
    norm = "".join(out_chars)
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm


def _artist_set_hash(artist_names: List[str]) -> str:
    """Generate a deterministic hash for a set of artist names."""
    normalized = sorted(n for n in (_normalize_name(n) for n in artist_names if n) if n)
    m = sha256()
    for n in normalized:
        m.update(n.encode("utf-8"))
        m.update(b"\x00")
    return m.hexdigest()


def _deduplicate_episodes(episodes: List[dict]) -> List[dict]:
    """Remove duplicate episodes by URL, keeping first occurrence."""
    seen_urls: Set[str] = set()
    unique = []
    for ep in episodes:
        url = ep.get("url") or ep.get("audio_url") or ""
        if not url:
            continue
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(ep)
    return unique


# Global thread lock to prevent concurrent database rebuilds within same process
_rebuild_lock = threading.Lock()

# File-based lock path for cross-process synchronization
_REBUILD_LOCK_FILE = str(get_rebuild_lock_path())


def _acquire_file_lock() -> Optional[int]:
    """Acquire a file-based lock for cross-process synchronization.
    
    Returns file descriptor if lock acquired, None if already locked.
    Cross-platform: uses fcntl on Unix, msvcrt on Windows.
    """
    try:
        os.makedirs(os.path.dirname(_REBUILD_LOCK_FILE), exist_ok=True)
        fd = os.open(_REBUILD_LOCK_FILE, os.O_CREAT | os.O_RDWR)
        
        if os.name == 'nt':
            # Windows: use msvcrt for file locking
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except (IOError, OSError):
                os.close(fd)
                return None
        else:
            # Unix: use fcntl for file locking
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                os.close(fd)
                return None
        
        # Write PID to lock file for debugging
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode())
        return fd
    except Exception:
        return None


def _release_file_lock(fd: int) -> None:
    """Release the file-based lock.
    
    Cross-platform: uses fcntl on Unix, msvcrt on Windows.
    """
    if fd is None:
        return
    try:
        if os.name == 'nt':
            # Windows: unlock with msvcrt
            import msvcrt
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        else:
            # Unix: unlock with fcntl
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:
                pass
        os.close(fd)
    except Exception:
        pass


def _optimize_sqlite_for_bulk(session) -> None:
    """Apply SQLite PRAGMAs optimized for bulk insert operations."""
    try:
        conn = session.connection()
        # These settings dramatically improve bulk insert performance
        conn.execute(text("PRAGMA synchronous = OFF"))
        conn.execute(text("PRAGMA journal_mode = MEMORY"))
        conn.execute(text("PRAGMA temp_store = MEMORY"))
        conn.execute(text("PRAGMA cache_size = -64000"))  # 64MB cache
        conn.execute(text("PRAGMA mmap_size = 268435456"))  # 256MB mmap
    except Exception as e:
        logger.warning(f"Could not set bulk SQLite pragmas: {e}")


def _restore_sqlite_settings(session) -> None:
    """Restore safe SQLite settings after bulk operations."""
    try:
        conn = session.connection()
        conn.execute(text("PRAGMA synchronous = NORMAL"))
        conn.execute(text("PRAGMA journal_mode = WAL"))
    except Exception as e:
        logger.warning(f"Could not restore SQLite pragmas: {e}")


def _dialect_insert(session, table, values):
    """Create a dialect-aware INSERT ... ON CONFLICT DO NOTHING statement.
    
    This provides database portability by using the appropriate syntax
    for each database dialect:
    - SQLite: INSERT OR IGNORE (via sqlite dialect)
    - PostgreSQL: INSERT ... ON CONFLICT DO NOTHING
    - Others: Falls back to standard insert (may not support conflict handling)
    """
    dialect_name = session.bind.dialect.name
    
    if dialect_name == 'sqlite':
        return sqlite_dialect.insert(table).values(values).on_conflict_do_nothing()
    elif dialect_name == 'postgresql':
        return pg_dialect.insert(table).values(values).on_conflict_do_nothing()
    else:
        # For other databases, use standard insert without conflict handling
        # The caller should handle IntegrityError if needed
        return insert(table).values(values)


def _bulk_insert_ignore(session, table, records: List[dict], batch_size: int = 500) -> int:
    """Perform bulk INSERT OR IGNORE for a list of records.
    
    This is significantly faster than individual inserts with existence checks.
    Uses dialect-aware insert for database portability (SQLite, PostgreSQL).
    Returns the number of records processed.
    """
    if not records:
        return 0
    
    inserted = 0
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        try:
            stmt = _dialect_insert(session, table, batch)
            session.execute(stmt)
            inserted += len(batch)
        except Exception as e:
            # Fallback to individual inserts on error
            logger.warning(f"Bulk insert failed, falling back to individual: {e}")
            for record in batch:
                try:
                    stmt = _dialect_insert(session, table, record)
                    session.execute(stmt)
                    inserted += 1
                except Exception:
                    pass
    return inserted


def _preprocess_json_data(shows_path: str, episodes_dir: str) -> Tuple[List[dict], Dict[str, List[dict]], int]:
    """Pre-load and preprocess all JSON data before database operations.
    
    This separates I/O from database operations for better performance.
    Returns: (shows_list, slug_to_episodes, total_episode_count)
    """
    if not os.path.exists(shows_path):
        return [], {}, 0
    
    with open(shows_path, "r") as f:
        shows_raw: Dict[str, dict] = json.load(f)
    
    shows_list = []
    slug_to_episodes: Dict[str, List[dict]] = {}
    total_episodes = 0
    
    for url, meta in shows_raw.items():
        slug = url.rstrip("/").split("/")[-1]
        shows_list.append({
            "url": url,
            "slug": slug,
            "title": meta.get("title", ""),
            "description": meta.get("description", ""),
            "thumbnail": meta.get("thumbnail", ""),
            "auto_download": bool(meta.get("auto_download", False)),
        })
        
        # Load episodes for this show
        ep_path = os.path.join(episodes_dir, f"{slug}.json")
        if os.path.exists(ep_path):
            try:
                with open(ep_path, "r") as ef:
                    ep_data = json.load(ef)
                episodes = _deduplicate_episodes(ep_data.get("episodes", []))
                slug_to_episodes[slug] = episodes
                total_episodes += len(episodes)
            except Exception as e:
                logger.warning(f"Failed to load episodes for {slug}: {e}")
    
    return shows_list, slug_to_episodes, total_episodes


def _extract_all_entities(
    slug_to_episodes: Dict[str, List[dict]]
) -> Tuple[Set[str], Set[str], Set[str], List[dict], List[dict]]:
    """Extract all unique entities from episode data for batch processing.
    
    Returns: (genres, artists, tags, tracks_data, track_artists_data)
    
    This pre-extraction allows for efficient batch inserts.
    """
    genres: Set[str] = set()
    artists: Set[str] = set()
    tags: Set[str] = set()  # Same as genres for now
    
    # Collect unique tracks and their artist associations
    track_keys_seen: Set[Tuple[str, str]] = set()
    tracks_data: List[dict] = []
    track_artist_pairs: List[Tuple[str, str, str]] = []  # (title_norm, artist_hash, artist_norm)
    
    for slug, episodes in slug_to_episodes.items():
        for ep in episodes:
            # Genres (also used as tags)
            for gname in ep.get("genres", []) or []:
                gname_norm = _normalize_name(gname)
                if gname_norm:
                    genres.add(gname_norm)
                    tags.add(gname_norm)
            
            # Tracks and artists
            for t in ep.get("tracklist", []) or []:
                artist_raw = t.get("artist", "")
                artist_names = [artist_raw] if isinstance(artist_raw, str) else (artist_raw or [])
                title_original = t.get("name", "")
                title_norm = _normalize_name(title_original)
                
                if not title_norm:
                    continue
                
                artist_hash = _artist_set_hash(artist_names)
                track_key = (title_norm, artist_hash)
                
                # Collect unique artists
                for an in artist_names:
                    an_norm = _normalize_name(an)
                    if an_norm:
                        artists.add(an_norm)
                        track_artist_pairs.append((title_norm, artist_hash, an_norm))
                
                # Collect unique tracks
                if track_key not in track_keys_seen:
                    track_keys_seen.add(track_key)
                    tracks_data.append({
                        "title_original": title_original,
                        "title_norm": title_norm,
                        "canonical_artist_set_hash": artist_hash,
                    })
    
    return genres, artists, tags, tracks_data, track_artist_pairs


def rebuild_database_from_json(
    sessionmaker,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: Optional[Callable[[IngestProgress], None]] = None,
) -> Dict[str, int]:
    """Rebuild relational DB from existing JSON files using optimized batch operations.
    
    Key optimizations:
    - Pre-loads all JSON data before DB operations
    - Uses bulk INSERT OR IGNORE for all entities
    - Maintains in-memory lookup caches
    - Minimizes database round-trips
    - Commits in configurable batches
    
    Args:
        sessionmaker: SQLAlchemy sessionmaker
        batch_size: Number of records per batch operation
        on_progress: Optional callback for progress updates
    
    Returns:
        Dictionary with counts of created/updated entities
    
    Note: User data tables (liked_tracks, user_playlists, etc.) are preserved.
    """
    # Acquire thread lock first (for same-process concurrency)
    thread_acquired = _rebuild_lock.acquire(blocking=False)
    if not thread_acquired:
        raise RuntimeError("Database rebuild already in progress (thread lock).")
    
    # Acquire file lock (for cross-process concurrency, e.g., Flask debug mode)
    file_fd = _acquire_file_lock()
    if file_fd is None:
        _rebuild_lock.release()
        raise RuntimeError("Database rebuild already in progress (file lock).")
    
    progress = IngestProgress()
    
    def _report_progress():
        if on_progress:
            try:
                on_progress(progress)
            except Exception:
                pass
    
    try:
        start_time = time.time()
        counters = defaultdict(int)
        
        # Phase 1: Pre-load all JSON data
        progress.phase = "loading_json"
        _report_progress()
        
        shows_path = str(runtime_shows_path())
        episodes_dir = str(runtime_episodes_dir())
        
        shows_list, slug_to_episodes, total_episodes = _preprocess_json_data(
            shows_path, episodes_dir
        )
        
        if not shows_list:
            return {"shows": 0, "message": "No shows.json found"}
        
        progress.total_shows = len(shows_list)
        progress.total_episodes = total_episodes
        _report_progress()
        
        logger.info(f"Loaded {len(shows_list)} shows, {total_episodes} episodes from JSON")
        
        # Phase 2: Extract all unique entities for batch processing
        progress.phase = "extracting_entities"
        _report_progress()
        
        all_genres, all_artists, all_tags, all_tracks, track_artist_pairs = _extract_all_entities(
            slug_to_episodes
        )
        
        logger.info(
            f"Extracted: {len(all_genres)} genres, {len(all_artists)} artists, "
            f"{len(all_tags)} tags, {len(all_tracks)} unique tracks"
        )
        
        # Phase 3: Clear and rebuild database
        progress.phase = "clearing_database"
        _report_progress()
        
        # Retry logic for database lock handling
        max_retries = 5
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                with sessionmaker() as session:
                    _optimize_sqlite_for_bulk(session)
                    
                    # Delete all rows from tables, preserving user data
                    for tbl in reversed(Base.metadata.sorted_tables):
                        if tbl.name not in USER_DATA_TABLES:
                            session.execute(tbl.delete())
                    session.commit()
                    break
            except OperationalError as e:
                if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                raise
        
        # Phase 4: Batch insert all entities
        progress.phase = "inserting_entities"
        _report_progress()
        
        with sessionmaker() as session:
            _optimize_sqlite_for_bulk(session)
            cache = LookupCache()
            
            # 4a: Insert genres
            genre_records = [{"name": g} for g in all_genres]
            _bulk_insert_ignore(session, Genre.__table__, genre_records, batch_size)
            session.commit()
            counters["genres"] = len(all_genres)
            
            # 4b: Insert artists
            artist_records = [{"name": a} for a in all_artists]
            _bulk_insert_ignore(session, Artist.__table__, artist_records, batch_size)
            session.commit()
            counters["artists"] = len(all_artists)
            
            # 4c: Insert tags
            tag_records = [{"name": t} for t in all_tags]
            _bulk_insert_ignore(session, Tag.__table__, tag_records, batch_size)
            session.commit()
            counters["tags"] = len(all_tags)
            
            # 4d: Insert tracks
            _bulk_insert_ignore(session, Track.__table__, all_tracks, batch_size)
            session.commit()
            counters["tracks"] = len(all_tracks)
            
            # Load caches for association tables
            cache.load_from_session(session)
            
            # 4e: Insert track-artist associations
            track_artist_records = []
            seen_pairs: Set[Tuple[int, int]] = set()
            for title_norm, artist_hash, artist_norm in track_artist_pairs:
                track_id = cache.tracks.get((title_norm, artist_hash))
                artist_id = cache.artists.get(artist_norm)
                if track_id and artist_id:
                    pair = (track_id, artist_id)
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        track_artist_records.append({
                            "track_id": track_id,
                            "artist_id": artist_id,
                        })
            
            _bulk_insert_ignore(session, track_artists, track_artist_records, batch_size)
            session.commit()
            counters["track_artists"] = len(track_artist_records)
            
            logger.info(f"Inserted base entities in {time.time() - start_time:.2f}s")
            
            # Phase 5: Insert shows and episodes with relationships
            progress.phase = "inserting_shows_episodes"
            _report_progress()
            
            # Batch insert all shows first using ON CONFLICT DO NOTHING
            show_records = []
            for show_data in shows_list:
                show_records.append({
                    "url": show_data["url"],
                    "title": show_data["title"],
                    "description": show_data["description"],
                    "thumbnail": show_data["thumbnail"],
                    "auto_download": show_data["auto_download"],
                })
            
            _bulk_insert_ignore(session, Show.__table__, show_records, batch_size)
            session.flush()
            
            # Reload all show IDs
            show_rows = session.execute(select(Show.id, Show.url)).all()
            url_to_show_id = {url: sid for sid, url in show_rows}
            counters["shows"] = len(url_to_show_id)
            
            # Now process each show's episodes
            for show_data in shows_list:
                show_id = url_to_show_id.get(show_data["url"])
                if not show_id:
                    continue
                
                slug = show_data["slug"]
                episodes = slug_to_episodes.get(slug, [])
                
                # Batch prepare episode data
                episode_records = []
                for ep in episodes:
                    ep_url = ep.get("url") or ep.get("audio_url") or ""
                    if not ep_url:
                        continue
                    episode_records.append({
                        "show_id": show_id,
                        "url": ep_url,
                        "title": ep.get("title", ""),
                        "date": ep.get("date", ""),
                        "image_url": ep.get("image_url", ""),
                        "audio_url": ep.get("audio_url", ""),
                    })
                
                # Batch insert episodes
                if episode_records:
                    _bulk_insert_ignore(session, Episode.__table__, episode_records, batch_size)
                    session.flush()
                
                # Reload episode IDs for this show
                ep_rows = session.execute(
                    select(Episode.id, Episode.url).where(Episode.show_id == show_id)
                ).all()
                url_to_ep_id = {url: eid for eid, url in ep_rows}
                
                # Process episode relationships (genres, tracks)
                episode_genre_records = []
                episode_track_records = []
                track_tag_records = []
                seen_ep_genres: Set[Tuple[int, int]] = set()
                seen_ep_tracks: Set[Tuple[int, int, int]] = set()
                seen_track_tags: Set[Tuple[int, int]] = set()
                
                for ep in episodes:
                    ep_url = ep.get("url") or ep.get("audio_url") or ""
                    ep_id = url_to_ep_id.get(ep_url)
                    if not ep_id:
                        continue
                    
                    # Episode genres
                    ep_genres = ep.get("genres", []) or []
                    genre_ids_for_ep = []
                    for gname in ep_genres:
                        gname_norm = _normalize_name(gname)
                        genre_id = cache.genres.get(gname_norm)
                        if genre_id:
                            genre_ids_for_ep.append(genre_id)
                            pair = (ep_id, genre_id)
                            if pair not in seen_ep_genres:
                                seen_ep_genres.add(pair)
                                episode_genre_records.append({
                                    "episode_id": ep_id,
                                    "genre_id": genre_id,
                                })
                    
                    # Episode tracks
                    tracklist = ep.get("tracklist", []) or []
                    for order, t in enumerate(tracklist, start=1):
                        artist_raw = t.get("artist", "")
                        artist_names = [artist_raw] if isinstance(artist_raw, str) else (artist_raw or [])
                        title_norm = _normalize_name(t.get("name", ""))
                        
                        if not title_norm:
                            continue
                        
                        artist_hash = _artist_set_hash(artist_names)
                        track_id = cache.tracks.get((title_norm, artist_hash))
                        
                        if track_id:
                            triple = (ep_id, track_id, order)
                            if triple not in seen_ep_tracks:
                                seen_ep_tracks.add(triple)
                                episode_track_records.append({
                                    "episode_id": ep_id,
                                    "track_id": track_id,
                                    "track_order": order,
                                    "timestamp": t.get("timestamp"),
                                })
                            
                            # Track tags from episode genres
                            for genre_id in genre_ids_for_ep:
                                tag_id = genre_id  # Genres are mirrored as tags
                                tag_pair = (track_id, tag_id)
                                if tag_pair not in seen_track_tags:
                                    seen_track_tags.add(tag_pair)
                                    track_tag_records.append({
                                        "track_id": track_id,
                                        "tag_id": tag_id,
                                        "weight": 1.0,
                                        "source": "episode",
                                    })
                    
                    progress.processed_episodes += 1
                
                # Batch insert relationship records
                _bulk_insert_ignore(session, EpisodeGenre.__table__, episode_genre_records, batch_size)
                _bulk_insert_ignore(session, EpisodeTrack.__table__, episode_track_records, batch_size)
                _bulk_insert_ignore(session, TrackTag.__table__, track_tag_records, batch_size)
                
                counters["episodes"] += len(episode_records)
                counters["episode_genres"] += len(episode_genre_records)
                counters["episode_tracks"] += len(episode_track_records)
                counters["track_tags"] += len(track_tag_records)
                
                progress.processed_shows += 1
                _report_progress()
                
                # Commit after each show to prevent memory buildup
                session.commit()
            
            # Restore safe SQLite settings
            _restore_sqlite_settings(session)
            session.commit()
        
        elapsed = time.time() - start_time
        counters["duration_seconds"] = round(elapsed, 2)
        
        progress.phase = "completed"
        progress.entities_created = dict(counters)
        _report_progress()
        
        logger.info(f"Database rebuild completed in {elapsed:.2f}s: {dict(counters)}")
        return dict(counters)
        
    except Exception as e:
        progress.phase = f"error: {str(e)}"
        _report_progress()
        logger.exception("Database rebuild failed")
        raise
    finally:
        _release_file_lock(file_fd)
        _rebuild_lock.release()


def incremental_update_from_json(
    sessionmaker,
    show_slugs: Optional[List[str]] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Dict[str, int]:
    """Incrementally update database with new episodes only.
    
    This is much faster than a full rebuild when only a few shows have new episodes.
    
    Args:
        sessionmaker: SQLAlchemy sessionmaker
        show_slugs: Optional list of show slugs to update; if None, checks all shows
        batch_size: Number of records per batch operation
    
    Returns:
        Dictionary with counts of created entities
    """
    start_time = time.time()
    counters = defaultdict(int)
    
    shows_path = str(runtime_shows_path())
    episodes_dir = str(runtime_episodes_dir())
    
    if not os.path.exists(shows_path):
        return {"message": "No shows.json found"}
    
    with open(shows_path, "r") as f:
        shows_raw: Dict[str, dict] = json.load(f)
    
    with sessionmaker() as session:
        cache = LookupCache()
        cache.load_from_session(session)
        
        for url, meta in shows_raw.items():
            slug = url.rstrip("/").split("/")[-1]
            
            # Skip if not in the requested slugs
            if show_slugs and slug not in show_slugs:
                continue
            
            ep_path = os.path.join(episodes_dir, f"{slug}.json")
            if not os.path.exists(ep_path):
                continue
            
            try:
                with open(ep_path, "r") as ef:
                    ep_data = json.load(ef)
                episodes = _deduplicate_episodes(ep_data.get("episodes", []))
            except Exception as e:
                logger.warning(f"Failed to load episodes for {slug}: {e}")
                continue
            
            # Find show ID
            show = session.scalar(select(Show).where(Show.url == url))
            if not show:
                # Create show if missing
                show = Show(
                    url=url,
                    title=meta.get("title", ""),
                    description=meta.get("description", ""),
                    thumbnail=meta.get("thumbnail", ""),
                    auto_download=bool(meta.get("auto_download", False)),
                )
                session.add(show)
                session.flush()
                counters["shows_created"] += 1
            
            # Find new episodes (not in cache)
            new_episodes = []
            for ep in episodes:
                ep_url = ep.get("url") or ep.get("audio_url") or ""
                if ep_url and ep_url not in cache.episodes:
                    new_episodes.append(ep)
            
            if not new_episodes:
                continue
            
            logger.info(f"Found {len(new_episodes)} new episodes for {slug}")
            
            # Process new episodes
            for ep in new_episodes:
                ep_url = ep.get("url") or ep.get("audio_url") or ""
                if not ep_url:
                    continue
                
                # Insert episode
                episode = Episode(
                    show_id=show.id,
                    url=ep_url,
                    title=ep.get("title", ""),
                    date=ep.get("date", ""),
                    image_url=ep.get("image_url", ""),
                    audio_url=ep.get("audio_url", ""),
                )
                session.add(episode)
                session.flush()
                cache.episodes[ep_url] = episode.id
                counters["episodes_created"] += 1
                
                # Process genres
                for gname in ep.get("genres", []) or []:
                    gname_norm = _normalize_name(gname)
                    if not gname_norm:
                        continue
                    
                    genre_id = cache.genres.get(gname_norm)
                    if not genre_id:
                        genre = Genre(name=gname_norm)
                        session.add(genre)
                        session.flush()
                        cache.genres[gname_norm] = genre.id
                        genre_id = genre.id
                        counters["genres_created"] += 1
                    
                    session.execute(
                        _dialect_insert(session, EpisodeGenre.__table__, {
                            "episode_id": episode.id, "genre_id": genre_id
                        })
                    )
                
                # Process tracks (simplified for incremental)
                for order, t in enumerate(ep.get("tracklist", []) or [], start=1):
                    artist_raw = t.get("artist", "")
                    artist_names = [artist_raw] if isinstance(artist_raw, str) else (artist_raw or [])
                    title_original = t.get("name", "")
                    title_norm = _normalize_name(title_original)
                    
                    if not title_norm:
                        continue
                    
                    artist_hash = _artist_set_hash(artist_names)
                    track_key = (title_norm, artist_hash)
                    
                    track_id = cache.tracks.get(track_key)
                    if not track_id:
                        track = Track(
                            title_original=title_original,
                            title_norm=title_norm,
                            canonical_artist_set_hash=artist_hash,
                        )
                        session.add(track)
                        session.flush()
                        cache.tracks[track_key] = track.id
                        track_id = track.id
                        counters["tracks_created"] += 1
                        
                        # Add artists
                        for an in artist_names:
                            an_norm = _normalize_name(an)
                            if not an_norm:
                                continue
                            
                            artist_id = cache.artists.get(an_norm)
                            if not artist_id:
                                artist = Artist(name=an_norm)
                                session.add(artist)
                                session.flush()
                                cache.artists[an_norm] = artist.id
                                artist_id = artist.id
                                counters["artists_created"] += 1
                            
                            session.execute(
                                _dialect_insert(session, track_artists, {
                                    "track_id": track_id, "artist_id": artist_id
                                })
                            )
                    
                    # Episode-track link
                    session.execute(
                        _dialect_insert(session, EpisodeTrack.__table__, {
                            "episode_id": episode.id,
                            "track_id": track_id,
                            "track_order": order,
                            "timestamp": t.get("timestamp"),
                        })
                    )
                    counters["episode_tracks_created"] += 1
            
            session.commit()
    
    elapsed = time.time() - start_time
    counters["duration_seconds"] = round(elapsed, 2)
    logger.info(f"Incremental update completed in {elapsed:.2f}s: {dict(counters)}")
    return dict(counters)


def is_rebuild_in_progress() -> bool:
    """Check if a database rebuild is currently running (thread or file lock).
    
    Cross-platform: uses fcntl on Unix, msvcrt on Windows.
    """
    try:
        # Check thread lock
        if _rebuild_lock.locked():
            return True
        
        # Check file lock
        if os.path.exists(_REBUILD_LOCK_FILE):
            try:
                fd = os.open(_REBUILD_LOCK_FILE, os.O_RDWR)
                try:
                    if os.name == 'nt':
                        # Windows: try to acquire lock with msvcrt
                        import msvcrt
                        try:
                            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                            os.close(fd)
                            return False  # Lock acquired and released = not in progress
                        except (IOError, OSError):
                            os.close(fd)
                            return True  # Lock held by another process
                    else:
                        # Unix: try to acquire lock with fcntl
                        import fcntl
                        try:
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            fcntl.flock(fd, fcntl.LOCK_UN)
                            os.close(fd)
                            return False  # Lock acquired and released = not in progress
                        except (BlockingIOError, OSError):
                            os.close(fd)
                            return True  # Lock held by another process
                except Exception:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
                    return False
            except Exception:
                pass
        return False
    except Exception:
        return False


@dataclass
class WeightRecalcProgress:
    """Progress tracking for track weight recalculation."""
    phase: str = "initializing"
    total_tracks: int = 0
    processed_tracks: int = 0
    updated_tracks: int = 0
    skipped_tracks: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "total_tracks": self.total_tracks,
            "processed_tracks": self.processed_tracks,
            "updated_tracks": self.updated_tracks,
            "skipped_tracks": self.skipped_tracks,
            "progress_pct": round(
                (self.processed_tracks / max(1, self.total_tracks)) * 100, 1
            )
        }


# Global progress tracking for weight recalculation
_weight_recalc_progress: Optional[WeightRecalcProgress] = None


def get_weight_recalc_progress() -> Optional[Dict[str, Any]]:
    """Get current progress of track weight recalculation."""
    global _weight_recalc_progress
    if _weight_recalc_progress:
        return _weight_recalc_progress.to_dict()
    return None


def recalculate_track_weights(
    sessionmaker,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: Optional[Callable[[WeightRecalcProgress], None]] = None,
) -> Dict[str, int]:
    """Recalculate track tag weights based on artist genre affinity.
    
    For each track, this function:
    1. Gets the track's artists
    2. Looks up each artist's cached genres (from ArtistGenre table)
    3. For each track tag, calculates weight based on artist-genre alignment
    4. Updates the TrackTag.weight values
    
    Args:
        sessionmaker: SQLAlchemy sessionmaker
        batch_size: Number of tracks to process per batch
        on_progress: Optional callback for progress updates
        
    Returns:
        Dict with statistics about the recalculation
    """
    global _weight_recalc_progress
    
    start_time = time.time()
    progress = WeightRecalcProgress(phase="loading_data")
    _weight_recalc_progress = progress
    
    def _report():
        if on_progress:
            on_progress(progress)
    
    _report()
    
    try:
        with sessionmaker() as session:
            # Step 1: Load all artist genres into memory for fast lookup
            artist_genres_map: Dict[int, Dict[str, float]] = {}
            
            ag_rows = session.query(
                ArtistGenre.artist_id,
                ArtistGenre.genre,
                ArtistGenre.weight
            ).all()
            
            for artist_id, genre, weight in ag_rows:
                if artist_id not in artist_genres_map:
                    artist_genres_map[artist_id] = {}
                artist_genres_map[artist_id][genre.lower()] = weight
            
            logger.info(f"Loaded genres for {len(artist_genres_map)} artists")
            
            # Step 2: Load track-to-artist mapping
            track_artists_map: Dict[int, List[int]] = {}
            
            ta_rows = session.execute(
                select(track_artists.c.track_id, track_artists.c.artist_id)
            ).fetchall()
            
            for track_id, artist_id in ta_rows:
                if track_id not in track_artists_map:
                    track_artists_map[track_id] = []
                track_artists_map[track_id].append(artist_id)
            
            logger.info(f"Loaded artist mappings for {len(track_artists_map)} tracks")
            
            # Step 3: Get all tracks with tags
            track_tags = session.query(
                TrackTag.id,
                TrackTag.track_id,
                TrackTag.tag_id,
                TrackTag.weight,
                TrackTag.source,
                Tag.name.label('tag_name')
            ).join(Tag, Tag.id == TrackTag.tag_id).all()
            
            # Group by track
            track_to_tags: Dict[int, List[tuple]] = {}
            for tt in track_tags:
                if tt.track_id not in track_to_tags:
                    track_to_tags[tt.track_id] = []
                track_to_tags[tt.track_id].append((tt.id, tt.tag_id, tt.tag_name, tt.source))
            
            progress.total_tracks = len(track_to_tags)
            progress.phase = "calculating_weights"
            _report()
            
            logger.info(f"Processing weights for {progress.total_tracks} tracks")
            
            # Step 4: Calculate new weights for each track
            updates = []
            
            for track_id, tags in track_to_tags.items():
                artist_ids = track_artists_map.get(track_id, [])
                total_tags = len(tags)
                
                # Aggregate artist genres for this track
                track_artist_genres: Dict[str, float] = {}
                for artist_id in artist_ids:
                    artist_g = artist_genres_map.get(artist_id, {})
                    for genre, weight in artist_g.items():
                        # Take max weight if genre appears for multiple artists
                        if genre not in track_artist_genres or weight > track_artist_genres[genre]:
                            track_artist_genres[genre] = weight
                
                has_enriched_artists = bool(track_artist_genres)
                
                for tt_id, tag_id, tag_name, source in tags:
                    tag_lower = tag_name.lower()
                    
                    if has_enriched_artists:
                        # Calculate weight based on artist-genre alignment
                        if tag_lower in track_artist_genres:
                            # Artist has this genre - use its weight
                            new_weight = track_artist_genres[tag_lower]
                        else:
                            # Artist doesn't have this genre - downweight
                            # Use inverse of total tags (IDF-like)
                            new_weight = 0.1 / max(total_tags, 1)
                    else:
                        # No artist genre data - use IDF fallback
                        # Equal weight distributed across tags
                        new_weight = 1.0 / max(total_tags, 1)
                    
                    # Clamp to valid range
                    new_weight = max(0.01, min(1.0, new_weight))
                    
                    updates.append({'id': tt_id, 'weight': new_weight})
                
                progress.processed_tracks += 1
                if has_enriched_artists:
                    progress.updated_tracks += 1
                else:
                    progress.skipped_tracks += 1
                
                # Report progress every 100 tracks
                if progress.processed_tracks % 100 == 0:
                    _report()
            
            # Step 5: Batch update weights
            progress.phase = "updating_database"
            _report()
            
            logger.info(f"Updating {len(updates)} track-tag weights")
            
            # Process updates in batches
            for i in range(0, len(updates), batch_size):
                batch = updates[i:i + batch_size]
                for upd in batch:
                    session.query(TrackTag).filter(TrackTag.id == upd['id']).update(
                        {'weight': upd['weight']},
                        synchronize_session=False
                    )
                session.commit()
            
            elapsed = time.time() - start_time
            
            progress.phase = "completed"
            _report()
            
            result = {
                "total_tracks": progress.total_tracks,
                "updated_tracks": progress.updated_tracks,
                "skipped_tracks": progress.skipped_tracks,
                "total_tag_updates": len(updates),
                "duration_seconds": round(elapsed, 2),
            }
            
            logger.info(f"Track weight recalculation completed in {elapsed:.2f}s: {result}")
            return result
            
    except Exception as e:
        progress.phase = f"error: {str(e)}"
        _report()
        logger.exception("Track weight recalculation failed")
        raise
    finally:
        _weight_recalc_progress = None
