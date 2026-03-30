import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from nts_feed.track_manager import TrackManager


class TestTrackManagerConfig(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.source_path = os.path.join(self.tempdir.name, "episode.m4a")
        self.auto_add_dir = os.path.join(self.tempdir.name, "auto-add")
        self.music_dir = os.path.join(self.tempdir.name, "music")
        os.makedirs(self.auto_add_dir, exist_ok=True)
        os.makedirs(self.music_dir, exist_ok=True)
        with open(self.source_path, "wb") as fh:
            fh.write(b"test")

        self.env_keys = [
            "AUTO_ADD_DIR",
            "TRIM_ENABLED",
            "TRIM_DURATION",
            "TRIM_START_SECONDS",
            "TRIM_END_SECONDS",
        ]
        self.previous_env = {key: os.environ.get(key) for key in self.env_keys}

    def tearDown(self):
        self.tempdir.cleanup()
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @patch("nts_feed.track_manager.shutil.move")
    @patch("nts_feed.track_manager.music_tag.load_file")
    @patch("nts_feed.track_manager.trim_audio_file")
    def test_move_to_music_library_skips_trim_when_disabled(
        self,
        trim_audio_file_mock,
        load_file_mock,
        move_mock,
    ):
        os.environ["AUTO_ADD_DIR"] = self.auto_add_dir
        os.environ["TRIM_ENABLED"] = "false"
        os.environ["TRIM_DURATION"] = "7"

        tag_file = MagicMock()
        comment_value = MagicMock()
        comment_value.value = "https://www.nts.live/shows/test/episodes/test-episode"
        tag_file.__getitem__.return_value = comment_value
        load_file_mock.return_value = tag_file

        manager = TrackManager(music_dir=self.music_dir)
        manager.move_to_music_library(self.source_path)

        trim_audio_file_mock.assert_not_called()
        move_mock.assert_called_once_with(
            self.source_path,
            os.path.join(self.auto_add_dir, "episode.m4a"),
        )

    @patch("nts_feed.track_manager.shutil.move")
    @patch("nts_feed.track_manager.music_tag.load_file")
    @patch("nts_feed.track_manager.trim_audio_file")
    def test_move_to_music_library_uses_env_trim_start_and_end(
        self,
        trim_audio_file_mock,
        load_file_mock,
        move_mock,
    ):
        os.environ["AUTO_ADD_DIR"] = self.auto_add_dir
        os.environ["TRIM_ENABLED"] = "true"
        os.environ["TRIM_START_SECONDS"] = "5"
        os.environ["TRIM_END_SECONDS"] = "3"
        os.environ.pop("TRIM_DURATION", None)

        trim_audio_file_mock.return_value = self.source_path

        tag_file = MagicMock()
        comment_value = MagicMock()
        comment_value.value = "https://www.nts.live/shows/test/episodes/test-episode"
        tag_file.__getitem__.return_value = comment_value
        load_file_mock.return_value = tag_file

        manager = TrackManager(music_dir=self.music_dir)
        manager.move_to_music_library(self.source_path)

        trim_audio_file_mock.assert_called_once_with(
            self.source_path,
            trim_start=5,
            trim_end=3,
        )
        move_mock.assert_called_once_with(
            self.source_path,
            os.path.join(self.auto_add_dir, "episode.m4a"),
        )

    @patch("nts_feed.track_manager.shutil.move")
    @patch("nts_feed.track_manager.music_tag.load_file")
    @patch("nts_feed.track_manager.trim_audio_file")
    def test_move_to_music_library_falls_back_to_legacy_trim_duration(
        self,
        trim_audio_file_mock,
        load_file_mock,
        move_mock,
    ):
        os.environ["AUTO_ADD_DIR"] = self.auto_add_dir
        os.environ["TRIM_ENABLED"] = "true"
        os.environ["TRIM_DURATION"] = "4"
        os.environ.pop("TRIM_START_SECONDS", None)
        os.environ.pop("TRIM_END_SECONDS", None)

        trim_audio_file_mock.return_value = self.source_path

        tag_file = MagicMock()
        comment_value = MagicMock()
        comment_value.value = "https://www.nts.live/shows/test/episodes/test-episode"
        tag_file.__getitem__.return_value = comment_value
        load_file_mock.return_value = tag_file

        manager = TrackManager(music_dir=self.music_dir)
        manager.move_to_music_library(self.source_path)

        trim_audio_file_mock.assert_called_once_with(
            self.source_path,
            trim_start=4,
            trim_end=4,
        )


if __name__ == "__main__":
    unittest.main()
