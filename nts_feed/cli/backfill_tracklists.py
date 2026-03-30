"""Refresh stored episode tracklists using the current scraper logic."""

from __future__ import annotations

import argparse
import json

from ..scrape import backfill_tracklists_all, backfill_tracklists_for_show


def run_backfill_tracklists(show_url: str | None = None):
    if show_url:
        count = backfill_tracklists_for_show(show_url)
        return {'success': True, 'total_updated': count, 'per_show': {show_url: count}}
    return {'success': True, **backfill_tracklists_all()}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Backfill stored episode tracklists using current scraper logic.')
    parser.add_argument('--show-url', help='Only backfill a single show by URL.')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    print(json.dumps(run_backfill_tracklists(show_url=args.show_url), indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
