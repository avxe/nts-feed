"""Database engine factories used by the application and maintenance CLIs."""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool


def _resolve_database_url(database_url: str | None = None) -> str:
    db_url = database_url or os.getenv('DATABASE_URL') or f"sqlite:///{os.path.abspath('data/nts.db')}"
    os.makedirs('data', exist_ok=True)
    return db_url


def get_db_engine(database_url: str | None = None):
    """Create the primary SQLAlchemy engine."""
    db_url = _resolve_database_url(database_url)

    if db_url.startswith('sqlite:///'):
        connect_args = {
            'check_same_thread': False,
            'timeout': 120,
        }
        engine = create_engine(
            db_url,
            future=True,
            poolclass=QueuePool,
            pool_size=1,
            max_overflow=2,
            pool_timeout=120,
            pool_pre_ping=True,
            connect_args=connect_args,
        )

        @event.listens_for(engine, 'connect')
        def set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: N802, ARG001
            cursor = dbapi_connection.cursor()
            pragmas = [
                ('journal_mode', 'WAL'),
                ('busy_timeout', '120000'),
                ('foreign_keys', 'ON'),
                ('synchronous', 'NORMAL'),
                ('cache_size', '-65536'),
                ('mmap_size', '268435456'),
                ('temp_store', 'MEMORY'),
                ('page_size', '4096'),
                ('auto_vacuum', 'INCREMENTAL'),
            ]
            for pragma, value in pragmas:
                try:
                    cursor.execute(f'PRAGMA {pragma}={value};')
                except Exception:
                    pass
            cursor.close()

        @event.listens_for(engine, 'checkout')
        def receive_checkout(dbapi_connection, connection_record, connection_proxy):  # noqa: ARG001
            try:
                cursor = dbapi_connection.cursor()
                cursor.execute('PRAGMA query_only=OFF;')
                cursor.close()
            except Exception:
                pass
    else:
        engine = create_engine(
            db_url,
            future=True,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            pool_recycle=3600,
        )

    return engine


def get_optimized_bulk_engine(database_url: str | None = None):
    """Create an engine tuned for bulk rebuild/import work."""
    db_url = _resolve_database_url(database_url)
    if not db_url.startswith('sqlite:///'):
        return get_db_engine(database_url)

    engine = create_engine(
        db_url,
        future=True,
        connect_args={
            'check_same_thread': False,
            'timeout': 300,
        },
    )

    @event.listens_for(engine, 'connect')
    def set_bulk_pragmas(dbapi_connection, connection_record):  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        for pragma, value in [
            ('journal_mode', 'MEMORY'),
            ('synchronous', 'OFF'),
            ('cache_size', '-131072'),
            ('temp_store', 'MEMORY'),
            ('mmap_size', '536870912'),
            ('locking_mode', 'EXCLUSIVE'),
        ]:
            try:
                cursor.execute(f'PRAGMA {pragma}={value};')
            except Exception:
                pass
        cursor.close()

    return engine
