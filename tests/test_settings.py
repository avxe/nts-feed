import json
import os
import tempfile
import unittest


class TestSettingsHelpers(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.settings_path = os.path.join(self.tempdir.name, "settings.json")
        self.env_keys = [
            "NTS_SETTINGS_PATH",
            "TRIM_ENABLED",
            "TRIM_START_SECONDS",
            "TRIM_END_SECONDS",
            "TRIM_DURATION",
            "YOUTUBE_API_KEY",
        ]
        self.previous_env = {key: os.environ.get(key) for key in self.env_keys}
        os.environ["NTS_SETTINGS_PATH"] = self.settings_path
        for key in self.env_keys:
            if key != "NTS_SETTINGS_PATH":
                os.environ.pop(key, None)

    def tearDown(self):
        self.tempdir.cleanup()
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_load_settings_returns_defaults_when_file_missing(self):
        from nts_feed.settings import load_settings

        settings = load_settings()

        self.assertTrue(settings["TRIM_ENABLED"])
        self.assertEqual(settings["TRIM_START_SECONDS"], 12)
        self.assertEqual(settings["TRIM_END_SECONDS"], 12)
        self.assertEqual(settings["YOUTUBE_API_KEY"], "")

    def test_save_settings_persists_supported_values(self):
        from nts_feed.settings import load_raw_settings, save_settings

        saved = save_settings({
            "TRIM_ENABLED": False,
            "TRIM_START_SECONDS": 8,
            "TRIM_END_SECONDS": 2,
            "YOUTUBE_API_KEY": "abc123",  # pragma: allowlist secret
            "UNKNOWN": "ignored",
        })

        self.assertFalse(saved["TRIM_ENABLED"])
        self.assertEqual(saved["TRIM_START_SECONDS"], 8)
        self.assertEqual(saved["TRIM_END_SECONDS"], 2)
        self.assertEqual(saved["YOUTUBE_API_KEY"], "abc123")
        self.assertNotIn("UNKNOWN", saved)

        with open(self.settings_path, "r", encoding="utf-8") as fh:
            persisted = json.load(fh)

        self.assertEqual(persisted, load_raw_settings())
        self.assertNotIn("UNKNOWN", persisted)

    def test_apply_saved_settings_to_env_only_fills_missing_values(self):
        from nts_feed.settings import apply_saved_settings_to_env, save_settings

        save_settings({
            "TRIM_START_SECONDS": 9,
            "TRIM_END_SECONDS": 1,
            "YOUTUBE_API_KEY": "saved-key",  # pragma: allowlist secret
        })

        apply_saved_settings_to_env()

        self.assertEqual(os.environ["TRIM_START_SECONDS"], "9")
        self.assertEqual(os.environ["TRIM_END_SECONDS"], "1")
        self.assertEqual(os.environ["YOUTUBE_API_KEY"], "saved-key")

    def test_legacy_trim_duration_in_file_migrates_to_start_and_end(self):
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            json.dump({"TRIM_DURATION": 7}, fh)

        from nts_feed.settings import load_raw_settings, load_settings

        raw = load_raw_settings()
        self.assertEqual(raw["TRIM_START_SECONDS"], 7)
        self.assertEqual(raw["TRIM_END_SECONDS"], 7)

        settings = load_settings()
        self.assertEqual(settings["TRIM_START_SECONDS"], 7)
        self.assertEqual(settings["TRIM_END_SECONDS"], 7)

    def test_legacy_trim_duration_env_applies_when_start_end_unset(self):
        os.environ["TRIM_DURATION"] = "6"

        from nts_feed.settings import load_settings

        settings = load_settings()
        self.assertEqual(settings["TRIM_START_SECONDS"], 6)
        self.assertEqual(settings["TRIM_END_SECONDS"], 6)

    def test_trim_start_end_env_override_legacy_trim_duration(self):
        os.environ["TRIM_DURATION"] = "6"
        os.environ["TRIM_START_SECONDS"] = "2"
        os.environ["TRIM_END_SECONDS"] = "4"

        from nts_feed.settings import load_settings

        settings = load_settings()
        self.assertEqual(settings["TRIM_START_SECONDS"], 2)
        self.assertEqual(settings["TRIM_END_SECONDS"], 4)


if __name__ == "__main__":
    unittest.main()
