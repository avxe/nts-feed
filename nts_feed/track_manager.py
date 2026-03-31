"""Track database management for downloaded NTS episodes."""

import os
import json
import music_tag
from datetime import datetime
import shutil
import logging

from .trimmer import trim_audio_file

logger = logging.getLogger(__name__)


class TrackManager:
    def __init__(self, music_dir='music_dir'):
        self.music_dir = music_dir
        # Store database file in the local data directory
        self.db_file = 'data/downloaded_tracks.json'
        self.ensure_data_directory()
        self.downloaded_tracks = self.load_database()
        logger.debug("TrackManager initialized")
        self._is_dirty = False
        self._downloaded_episodes_cache = None
        self._last_scan_time = None

    def ensure_data_directory(self):
        """Ensure the data directory exists"""
        os.makedirs(os.path.dirname(self.db_file), exist_ok=True)

    def load_database(self):
        """Load downloaded tracks database from file"""
        try:
            if os.path.exists(self.db_file):
                with open(self.db_file, 'r') as f:
                    data = json.load(f)
                    logger.debug("Track database loaded successfully")
                    return {
                        'tracks': data.get('tracks', []),
                        'episodes': set(data.get('episodes', []))
                    }
            return {'tracks': [], 'episodes': set()}
        except Exception as e:
            logger.error(f"Error loading track database: {e}")
            return {'tracks': [], 'episodes': set()}

    def save_database(self):
        """Save downloaded tracks database to file"""
        try:
            # Convert set to list for JSON serialization
            data = {
                'tracks': self.downloaded_tracks['tracks'],
                'episodes': list(self.downloaded_tracks['episodes'])
            }
            with open(self.db_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info("Track database saved successfully")
        except Exception as e:
            logger.error(f"Error saving track database: {e}")
            raise

    def scan_directory(self):
        """Scan music directory and update database"""
        if not os.path.exists(self.music_dir):
            logger.error(f"Music directory not found: {self.music_dir}")
            return False

        changes_made = False
        current_episodes = set()

        for filename in os.listdir(self.music_dir):
            if filename.endswith(('.mp3', '.m4a', '.wav')):
                file_path = os.path.join(self.music_dir, filename)
                try:
                    f = music_tag.load_file(file_path)
                    episode_url = f['comment'].value

                    if episode_url:
                        current_episodes.add(episode_url)
                        if episode_url not in self.downloaded_tracks['episodes']:
                            self.downloaded_tracks['episodes'].add(episode_url)
                            self.downloaded_tracks['tracks'].append({
                                'title': f['title'].value,
                                'artist': f['artist'].value,
                                'url': episode_url,
                                'filename': filename,
                                'date_added': datetime.now().isoformat()
                            })
                            changes_made = True
                except Exception as e:
                    logger.error(f"Error reading metadata from {filename}: {e}")

        # Check for removed files
        removed_episodes = self.downloaded_tracks['episodes'] - current_episodes
        if removed_episodes:
            self.downloaded_tracks['episodes'] = current_episodes
            self.downloaded_tracks['tracks'] = [
                track for track in self.downloaded_tracks['tracks']
                if track['url'] in current_episodes
            ]
            changes_made = True

        if changes_made:
            self.downloaded_tracks['last_scan'] = datetime.now().isoformat()
            self._is_dirty = True
            self.save_database()
            # Update cache
            self._downloaded_episodes_cache = current_episodes
            self._last_scan_time = datetime.now()

        logger.debug("Directory scan complete")
        return changes_made

    def is_episode_downloaded(self, episode_url):
        """Check if episode has already been downloaded"""
        # First check the database
        if episode_url in self.downloaded_tracks['episodes']:
            return True

        # If not in database, scan directory to ensure database is up-to-date
        self.scan_directory()
        return episode_url in self.downloaded_tracks['episodes']

    def get_downloaded_episodes(self):
        """Return a set of all downloaded episode URLs with caching"""
        current_time = datetime.now()

        # Refresh cache if it's None or if more than 5 minutes have passed
        if (self._downloaded_episodes_cache is None or
            self._last_scan_time is None or
            (current_time - self._last_scan_time).total_seconds() > 300):

            self.scan_directory()
            self._downloaded_episodes_cache = self.downloaded_tracks['episodes']
            self._last_scan_time = current_time

        return self._downloaded_episodes_cache

    def add_downloaded_episode(self, episode_url, title, artist, filename):
        """Add a newly downloaded episode to the database"""
        if episode_url not in self.downloaded_tracks['episodes']:
            self.downloaded_tracks['episodes'].add(episode_url)
            self.downloaded_tracks['tracks'].append({
                'title': title,
                'artist': artist,
                'url': episode_url,
                'filename': filename,
                'date_added': datetime.now().isoformat()
            })
            self._is_dirty = True
            self.save_database()

    def move_to_music_library(self, source_path):
        """Move downloaded file to Music library and update database"""
        try:
            trim_enabled = os.getenv('TRIM_ENABLED', 'true').strip().lower() not in {
                '0', 'false', 'no', 'off'
            }
            trim_start, trim_end = self._resolve_trim_seconds()

            trimmed_path = source_path
            if trim_enabled:
                logger.info(f"Attempting to trim file: {source_path}")
                trimmed_path = trim_audio_file(
                    source_path,
                    trim_start=trim_start,
                    trim_end=trim_end,
                )

                if not trimmed_path:
                    logger.warning("Trimming failed, proceeding with original file")
                    trimmed_path = source_path

            auto_add_dir = os.getenv('AUTO_ADD_DIR', '/app/auto_add_dir')

            # Print debug information
            logger.info(f"Source path: {trimmed_path}")
            logger.info(f"Destination directory: {auto_add_dir}")

            # Verify file existence
            if not os.path.exists(trimmed_path):
                logger.error(f"Source file not found: {trimmed_path}")
                raise FileNotFoundError(f"Source file not found: {trimmed_path}")

            if not os.path.exists(auto_add_dir):
                logger.error(f"Auto-add directory not found: {auto_add_dir}")
                raise FileNotFoundError(f"Auto-add directory not found: {auto_add_dir}")

            # Get file metadata before moving
            f = music_tag.load_file(trimmed_path)
            episode_url = f['comment'].value

            # Move file to auto-add directory using shutil.move
            filename = os.path.basename(trimmed_path)
            dest_path = os.path.join(auto_add_dir, filename)
            logger.info(f"Destination path: {dest_path}")
            shutil.move(trimmed_path, dest_path)

            # Update database with new location
            if episode_url:
                for track in self.downloaded_tracks['tracks']:
                    if track['url'] == episode_url:
                        track['filename'] = filename
                        self.save_database()
                        break

        except Exception as e:
            logger.error(f"Error moving file: {e}")
            raise

    @staticmethod
    def _resolve_trim_seconds():
        """Read trim margins from env; fall back to legacy TRIM_DURATION for both sides."""
        start_env = os.getenv('TRIM_START_SECONDS')
        end_env = os.getenv('TRIM_END_SECONDS')
        if start_env not in (None, '') or end_env not in (None, ''):
            try:
                trim_start = max(0, int(start_env or 0))
            except ValueError:
                trim_start = 12
            try:
                trim_end = max(0, int(end_env or 0))
            except ValueError:
                trim_end = 12
            return trim_start, trim_end

        try:
            legacy = max(0, int(os.getenv('TRIM_DURATION', '12')))
        except ValueError:
            legacy = 12
        return legacy, legacy
