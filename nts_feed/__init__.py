"""Top-level package metadata and stable public helpers."""

__version__ = '0.1.0'


def get_db_engine(*args, **kwargs):
    from .db.engines import get_db_engine as _get_db_engine

    return _get_db_engine(*args, **kwargs)


def get_optimized_bulk_engine(*args, **kwargs):
    from .db.engines import get_optimized_bulk_engine as _get_optimized_bulk_engine

    return _get_optimized_bulk_engine(*args, **kwargs)


__all__ = ['__version__', 'get_db_engine', 'get_optimized_bulk_engine']
