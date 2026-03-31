"""Download missed episodes for shows with auto-download enabled."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime

from ..runtime_paths import downloads_dir
from ..downloader import download, download_manager
from ..scrape import load_episodes, load_shows, slugify
from ..track_manager import TrackManager

DOWNLOAD_DIR = downloads_dir()


def backfill_auto_downloads():
    track_manager = TrackManager()
    track_manager.scan_directory()

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    shows = load_shows()

    total_attempted = 0
    total_downloaded = 0
    results = []

    for url, show_data in shows.items():
        if not show_data.get('auto_download', False):
            continue

        show_slug = slugify(url)
        episodes = (load_episodes(show_slug) or {}).get('episodes', [])
        candidates = [ep for ep in episodes if ep.get('is_new', False)]
        to_download = [ep for ep in candidates if not track_manager.is_episode_downloaded(ep.get('url'))]

        attempted = len(to_download)
        downloaded = 0

        for episode in to_download:
            download_id = datetime.now().strftime('%Y%m%d_%H%M%S')
            download_path = DOWNLOAD_DIR / download_id
            try:
                download_path.mkdir(exist_ok=True)
                download(
                    episode['audio_url'],
                    quiet=True,
                    save_dir=str(download_path),
                    download_id=download_id,
                )
                try:
                    for file_name in os.listdir(download_path):
                        if file_name.endswith('.m4a'):
                            shutil.move(str(download_path / file_name), str(DOWNLOAD_DIR / file_name))
                except Exception:
                    pass
                downloaded += 1
            except Exception as exc:
                print(
                    f"Error downloading episode '{episode.get('title', '')}' "
                    f"from show '{show_data.get('title', '')}': {exc}"
                )
            finally:
                shutil.rmtree(download_path, ignore_errors=True)
                download_manager.remove_cancel_event(download_id)

        if attempted:
            results.append({
                'show_url': url,
                'title': show_data.get('title', ''),
                'attempted': attempted,
                'downloaded': downloaded,
            })

        total_attempted += attempted
        total_downloaded += downloaded

    return {
        'attempted': total_attempted,
        'downloaded': total_downloaded,
        'results': results,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Backfill missed auto-download episodes for subscribed shows.')
    return parser.parse_args(argv)


def main(argv=None):
    parse_args(argv)
    print(json.dumps(backfill_auto_downloads(), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
