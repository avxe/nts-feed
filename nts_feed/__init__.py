"""Top-level package metadata and stable public helpers."""

from .db.engines import get_db_engine, get_optimized_bulk_engine

__version__ = '0.1.0'

__all__ = ['__version__', 'get_db_engine', 'get_optimized_bulk_engine']
