"""Database bootstrap and lightweight runtime schema migration helpers."""

from __future__ import annotations

import logging

from .engines import get_db_engine

logger = logging.getLogger('flask_app')


def _get_optional_imports():
    """Import optional database dependencies, returning None when unavailable."""
    results = {}

    try:
        from . import init_sessionmaker
        from .ingest import (
            incremental_update_from_json,
            is_rebuild_in_progress,
            rebuild_database_from_json,
        )
        from .models import Base

        results.update(
            Base=Base,
            init_sessionmaker=init_sessionmaker,
            incremental_update_from_json=incremental_update_from_json,
            is_rebuild_in_progress=is_rebuild_in_progress,
            rebuild_database_from_json=rebuild_database_from_json,
        )
    except Exception:
        results.update(
            Base=None,
            init_sessionmaker=None,
            incremental_update_from_json=None,
            is_rebuild_in_progress=None,
            rebuild_database_from_json=None,
        )

    return results


def bootstrap_database(app, database_url: str | None = None):
    """Create the engine/sessionmaker and apply runtime-safe schema updates."""
    deps = _get_optional_imports()
    db_engine = get_db_engine(database_url)
    db_sessionmaker = None
    base = deps['Base']

    if base is not None:
        try:
            base.metadata.create_all(db_engine)
            ensure_runtime_schema(db_engine)
            db_sessionmaker = deps['init_sessionmaker'](db_engine)
        except Exception:
            logger.exception('Failed to bootstrap database')

    app.extensions['db_engine'] = db_engine
    app.extensions['db_sessionmaker'] = db_sessionmaker
    app.extensions['Base'] = base
    app.extensions['rebuild_database_from_json'] = deps['rebuild_database_from_json']
    app.extensions['is_rebuild_in_progress'] = deps['is_rebuild_in_progress']
    app.extensions['incremental_update_from_json'] = deps['incremental_update_from_json']
    return app


def ensure_runtime_schema(engine):
    """Apply runtime-safe migrations for existing installs."""
    _ensure_liked_tracks_schema(engine)
    _ensure_artist_mbid_schema(engine)
    _ensure_track_youtube_schema(engine)
    _ensure_listening_sessions_schema(engine)
    _ensure_episode_inbox_state_schema(engine)


def _ensure_artist_mbid_schema(engine):
    """Ensure artists table has legacy metadata columns expected by old installs."""
    if engine is None:
        return
    try:
        from sqlalchemy import text
    except Exception:
        return
    try:
        with engine.begin() as conn:
            columns_info = conn.execute(text('PRAGMA table_info(artists)')).fetchall()
            if not columns_info:
                return
            columns = {col[1] for col in columns_info}
            for col_name, col_type in [
                ('mbid', 'VARCHAR(36)'),
                ('disambiguation', 'VARCHAR(512)'),
                ('mb_type', 'VARCHAR(32)'),
                ('country', 'VARCHAR(2)'),
                ('mb_fetched_at', 'DATETIME'),
                ('enrichment_attempted_at', 'DATETIME'),
            ]:
                if col_name not in columns:
                    try:
                        conn.execute(text(f'ALTER TABLE artists ADD COLUMN {col_name} {col_type}'))
                        logger.info('Added column %s to artists table', col_name)
                    except Exception as exc:
                        logger.debug('Column %s may already exist: %s', col_name, exc)
            try:
                conn.execute(text('CREATE INDEX IF NOT EXISTS ix_artists_mbid ON artists(mbid)'))
            except Exception:
                pass
            try:
                conn.execute(text(
                    'CREATE INDEX IF NOT EXISTS ix_artists_enrichment_attempted_at '
                    'ON artists(enrichment_attempted_at)'
                ))
            except Exception:
                pass
    except Exception:
        logger.exception('Failed to ensure artist MBID schema')


def _ensure_liked_tracks_schema(engine):
    """Ensure liked_tracks table matches the current nullable track schema."""
    if engine is None:
        return
    try:
        from sqlalchemy import text
    except Exception:
        return
    try:
        with engine.begin() as conn:
            columns_info = conn.execute(text('PRAGMA table_info(liked_tracks)')).fetchall()
            if not columns_info:
                return
            columns = {col[1]: {'notnull': col[3]} for col in columns_info}
            required_columns = {
                'artist': "artist VARCHAR(512) NOT NULL DEFAULT ''",
                'title': "title VARCHAR(512) NOT NULL DEFAULT ''",
                'episode_url': 'episode_url VARCHAR(1024)',
                'episode_title': 'episode_title VARCHAR(512)',
                'show_title': 'show_title VARCHAR(512)',
            }
            for column_name, ddl in required_columns.items():
                if column_name not in columns:
                    conn.execute(text(f'ALTER TABLE liked_tracks ADD COLUMN {ddl}'))
                    columns[column_name] = {'notnull': 0}
            track_not_nullable = columns.get('track_id', {}).get('notnull') == 1
            if not track_not_nullable:
                return
            conn.execute(text('DROP TABLE IF EXISTS liked_tracks_tmp'))
            conn.execute(text("""
                CREATE TABLE liked_tracks_tmp (
                    id INTEGER PRIMARY KEY,
                    artist VARCHAR(512) NOT NULL,
                    title VARCHAR(512) NOT NULL,
                    track_id INTEGER,
                    episode_url VARCHAR(1024),
                    episode_title VARCHAR(512),
                    show_title VARCHAR(512),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(track_id) REFERENCES tracks(id) ON DELETE SET NULL,
                    UNIQUE(artist, title)
                )
            """))
            conn.execute(text("""
                INSERT OR IGNORE INTO liked_tracks_tmp
                    (id, artist, title, track_id, episode_url, episode_title, show_title, created_at)
                SELECT
                    id,
                    COALESCE(artist, ''),
                    COALESCE(title, ''),
                    track_id,
                    episode_url,
                    episode_title,
                    show_title,
                    COALESCE(created_at, CURRENT_TIMESTAMP)
                FROM liked_tracks
            """))
            conn.execute(text('DROP TABLE liked_tracks'))
            conn.execute(text('ALTER TABLE liked_tracks_tmp RENAME TO liked_tracks'))
    except Exception:
        logger.exception('Failed to ensure liked_tracks schema')


def _ensure_listening_sessions_schema(engine):
    """Ensure listening_sessions table includes the latest columns and indexes."""
    if engine is None:
        return
    try:
        from sqlalchemy import text
    except Exception:
        return

    required_columns = {
        'session_token': "session_token VARCHAR(128) NOT NULL DEFAULT ''",
        'kind': "kind VARCHAR(16) NOT NULL DEFAULT 'episode'",
        'player': "player VARCHAR(32) NOT NULL DEFAULT 'nts_audio'",
        'show_id': 'show_id INTEGER',
        'episode_id': 'episode_id INTEGER',
        'track_id': 'track_id INTEGER',
        'show_url': 'show_url VARCHAR(1024)',
        'episode_url': 'episode_url VARCHAR(1024)',
        'artist_name': 'artist_name VARCHAR(512)',
        'track_title': 'track_title VARCHAR(512)',
        'started_at': 'started_at DATETIME',
        'last_event_at': 'last_event_at DATETIME',
        'ended_at': 'ended_at DATETIME',
        'listened_seconds': 'listened_seconds FLOAT DEFAULT 0',
        'duration_seconds': 'duration_seconds FLOAT',
        'max_position_seconds': 'max_position_seconds FLOAT DEFAULT 0',
        'completion_ratio': 'completion_ratio FLOAT DEFAULT 0',
        'is_meaningful': 'is_meaningful BOOLEAN DEFAULT 0',
        'is_completed': 'is_completed BOOLEAN DEFAULT 0',
        'created_at': 'created_at DATETIME DEFAULT CURRENT_TIMESTAMP',
        'updated_at': 'updated_at DATETIME DEFAULT CURRENT_TIMESTAMP',
    }
    try:
        with engine.begin() as conn:
            columns_info = conn.execute(text('PRAGMA table_info(listening_sessions)')).fetchall()
            if not columns_info:
                return
            columns = {col[1] for col in columns_info}
            for column_name, ddl in required_columns.items():
                if column_name not in columns:
                    conn.execute(text(f'ALTER TABLE listening_sessions ADD COLUMN {ddl}'))
            for statement in [
                'CREATE UNIQUE INDEX IF NOT EXISTS uq_listening_session_token ON listening_sessions(session_token)',
                'CREATE INDEX IF NOT EXISTS ix_listening_sessions_kind_last_event ON listening_sessions(kind, last_event_at)',
                'CREATE INDEX IF NOT EXISTS ix_listening_sessions_meaningful_last_event ON listening_sessions(is_meaningful, last_event_at)',
                'CREATE INDEX IF NOT EXISTS ix_listening_sessions_show_id ON listening_sessions(show_id)',
                'CREATE INDEX IF NOT EXISTS ix_listening_sessions_episode_id ON listening_sessions(episode_id)',
                'CREATE INDEX IF NOT EXISTS ix_listening_sessions_track_id ON listening_sessions(track_id)',
            ]:
                conn.execute(text(statement))
    except Exception:
        logger.exception('Failed to ensure listening_sessions schema')


def _ensure_track_youtube_schema(engine):
    """Ensure tracks table includes persistent YouTube resolution fields."""
    if engine is None:
        return
    try:
        from sqlalchemy import text
    except Exception:
        return

    required_columns = {
        'youtube_video_id': 'youtube_video_id VARCHAR(32)',
        'youtube_video_url': 'youtube_video_url VARCHAR(1024)',
        'youtube_embed_url': 'youtube_embed_url VARCHAR(1024)',
        'youtube_title': 'youtube_title VARCHAR(512)',
        'youtube_channel': 'youtube_channel VARCHAR(512)',
        'youtube_thumbnail': 'youtube_thumbnail VARCHAR(1024)',
        'youtube_search_only': 'youtube_search_only BOOLEAN DEFAULT 0',
        'youtube_lookup_attempted_at': 'youtube_lookup_attempted_at DATETIME',
    }
    try:
        with engine.begin() as conn:
            columns_info = conn.execute(text('PRAGMA table_info(tracks)')).fetchall()
            if not columns_info:
                return
            columns = {col[1] for col in columns_info}
            for column_name, ddl in required_columns.items():
                if column_name not in columns:
                    conn.execute(text(f'ALTER TABLE tracks ADD COLUMN {ddl}'))
            conn.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_tracks_youtube_lookup_attempted_at '
                'ON tracks(youtube_lookup_attempted_at)'
            ))
    except Exception:
        logger.exception('Failed to ensure track YouTube schema')


def _ensure_episode_inbox_state_schema(engine):
    """Ensure episode_inbox_state has the latest rebuild-safe inbox columns."""
    if engine is None:
        return
    try:
        from sqlalchemy import text
    except Exception:
        return

    required_columns = {
        'episode_id': 'episode_id INTEGER',
        'episode_url': 'episode_url VARCHAR(1024)',
        'saved_for_later': 'saved_for_later BOOLEAN DEFAULT 0',
        'snoozed_until': 'snoozed_until DATETIME',
        'dismissed_at': 'dismissed_at DATETIME',
        'created_at': 'created_at DATETIME DEFAULT CURRENT_TIMESTAMP',
        'updated_at': 'updated_at DATETIME DEFAULT CURRENT_TIMESTAMP',
    }
    try:
        with engine.begin() as conn:
            columns_info = conn.execute(text('PRAGMA table_info(episode_inbox_state)')).fetchall()
            if not columns_info:
                return
            columns = {col[1]: {'notnull': col[3], 'default': col[4]} for col in columns_info}

            added_episode_url_column = False
            if 'episode_url' not in columns:
                conn.execute(text('ALTER TABLE episode_inbox_state ADD COLUMN episode_url VARCHAR(1024)'))
                added_episode_url_column = True

            if 'episode_url' in columns or added_episode_url_column:
                missing_url_rows = conn.execute(text("""
                    SELECT COUNT(*)
                    FROM episode_inbox_state
                    WHERE episode_url IS NULL OR TRIM(episode_url) = ''
                """)).scalar_one()
                if missing_url_rows:
                    raise RuntimeError(
                        'Cannot backfill episode_inbox_state.episode_url safely because '
                        f'{missing_url_rows} existing row(s) are missing episode_url values.'
                    )
                columns['episode_url'] = {'notnull': 0, 'default': None}

            for column_name, ddl in required_columns.items():
                if column_name not in columns:
                    conn.execute(text(f'ALTER TABLE episode_inbox_state ADD COLUMN {ddl}'))

            duplicate_url_rows = conn.execute(text("""
                SELECT episode_url, COUNT(*) AS row_count
                FROM episode_inbox_state
                WHERE episode_url IS NOT NULL AND TRIM(episode_url) != ''
                GROUP BY episode_url
                HAVING COUNT(*) > 1
            """)).fetchall()
            if duplicate_url_rows:
                raise RuntimeError(
                    'Cannot create unique inbox index because duplicate episode_url values already exist.'
                )

            for statement in [
                'CREATE UNIQUE INDEX IF NOT EXISTS uq_episode_inbox_state_episode_url ON episode_inbox_state(episode_url)',
                'CREATE INDEX IF NOT EXISTS ix_episode_inbox_state_saved ON episode_inbox_state(saved_for_later, updated_at)',
                'CREATE INDEX IF NOT EXISTS ix_episode_inbox_state_snoozed ON episode_inbox_state(snoozed_until)',
            ]:
                conn.execute(text(statement))
    except Exception:
        logger.exception('Failed to ensure episode_inbox_state schema')
