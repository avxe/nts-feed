from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch


class StoragePathsTest(unittest.TestCase):
    def test_storage_helpers_default_to_storage_root_and_normalize_legacy_env_values(self):
        from nts_feed.storage.paths import (
            get_auto_add_dir,
            get_downloads_dir,
            get_episodes_dir,
            get_image_cache_dir,
            get_music_dir,
            get_shows_backup_path,
            get_shows_path,
            get_storage_data_dir,
            get_storage_root,
            get_thumbnails_dir,
        )

        project_root = Path(__file__).resolve().parents[1]
        expected_root = project_root / "storage"

        with patch.dict(
            os.environ,
            {
                "IMAGE_CACHE_DIR": "thumbnails",
                "MUSIC_DIR": "music_dir",
                "AUTO_ADD_DIR": "/app/auto_add_dir",
            },
            clear=False,
        ):
            self.assertEqual(get_storage_root(), expected_root)
            self.assertEqual(get_shows_path(), expected_root / "shows.json")
            self.assertEqual(get_shows_backup_path(), expected_root / "shows.json.backup")
            self.assertEqual(get_episodes_dir(), expected_root / "episodes")
            self.assertEqual(get_downloads_dir(), expected_root / "downloads")
            self.assertEqual(get_thumbnails_dir(), expected_root / "thumbnails")
            self.assertEqual(get_storage_data_dir(), expected_root / "data")
            self.assertEqual(get_image_cache_dir(), expected_root / "thumbnails")
            self.assertEqual(get_music_dir(), expected_root / "music_dir")
            self.assertEqual(get_auto_add_dir(), expected_root / "auto_add_dir")

    def test_storage_helpers_respect_custom_storage_root(self):
        from nts_feed.storage.paths import (
            get_shows_path,
            get_storage_data_dir,
            get_storage_root,
        )

        project_root = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {"NTS_STORAGE_ROOT": "var/runtime"}, clear=False):
            self.assertEqual(get_storage_root(), project_root / "var" / "runtime")
            self.assertEqual(get_shows_path(), project_root / "var" / "runtime" / "shows.json")
            self.assertEqual(get_storage_data_dir(), project_root / "var" / "runtime" / "data")

    def test_database_url_legacy_default_moves_into_storage_data_directory(self):
        from nts_feed.db.engines import _resolve_database_url
        from nts_feed.storage.paths import get_storage_data_dir

        with patch.dict(
            os.environ,
            {
                "NTS_STORAGE_ROOT": "storage",
                "DATABASE_URL": "sqlite:///data/nts.db",
            },
            clear=False,
        ):
            expected = f"sqlite:///{(get_storage_data_dir() / 'nts.db').resolve()}"
            self.assertEqual(_resolve_database_url(), expected)


if __name__ == "__main__":
    unittest.main()
