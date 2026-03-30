"""Rebuild the SQL database from the JSON show and episode documents."""

from __future__ import annotations

import argparse
import json

from ..db import init_sessionmaker
from ..db.bootstrap import ensure_runtime_schema
from ..db.engines import get_db_engine
from ..db.ingest import rebuild_database_from_json
from ..db.models import Base


def build_database(database_url: str | None = None):
    engine = get_db_engine(database_url)
    Base.metadata.create_all(engine)
    ensure_runtime_schema(engine)
    session_factory = init_sessionmaker(engine)
    return rebuild_database_from_json(session_factory)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Build or rebuild the SQL database from JSON source files.')
    parser.add_argument('--database-url', help='Override DATABASE_URL for this command.')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    stats = build_database(database_url=args.database_url)
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
