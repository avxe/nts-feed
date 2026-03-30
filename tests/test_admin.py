import os
import tempfile
import unittest
from datetime import datetime, timedelta
import json

from nts_feed.app import create_app
from nts_feed.db.models import Episode, EpisodeGenre, Genre, ListeningSession, Show

REMOVED_SETTING_KEYS = (
    "SPOT" + "IFY_CLIENT_ID",
    "SPOT" + "IFY_CLIENT_SECRET",
    "SPOT" + "IFY_REDIRECT_URI",
)


class TestAdminListeningStats(unittest.TestCase):
    def setUp(self):
        self.previous_env = {
            "SECRET_KEY": os.environ.get("SECRET_KEY"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
        }
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["SECRET_KEY"] = "test-secret-key-for-admin"  # pragma: allowlist secret
        os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(self.tempdir.name, 'admin.db')}"

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        with self.app.extensions["db_sessionmaker"]() as session:
            show = Show(
                url="https://www.nts.live/shows/show-alpha",
                title="Show Alpha",
                description="Test show",
                thumbnail="https://img.example.com/show-alpha.jpg",
            )
            episode = Episode(
                show=show,
                url="https://www.nts.live/shows/show-alpha/episodes/alpha-new",
                title="Alpha New",
                date="March 10, 2026",
                image_url="https://img.example.com/alpha-new.jpg",
                audio_url="https://audio.example.com/alpha-new.mp3",
            )
            genre = Genre(name="Ambient")
            session.add_all([show, episode, genre])
            session.flush()
            session.add(EpisodeGenre(episode_id=episode.id, genre_id=genre.id))
            now = datetime.utcnow()
            session.add_all([
                ListeningSession(
                    session_token="admin-episode",
                    kind="episode",
                    player="nts_audio",
                    show_id=show.id,
                    episode_id=episode.id,
                    show_url=show.url,
                    episode_url=episode.url,
                    started_at=now - timedelta(minutes=20),
                    last_event_at=now - timedelta(minutes=3),
                    listened_seconds=600,
                    duration_seconds=3600,
                    max_position_seconds=600,
                    completion_ratio=600 / 3600,
                    is_meaningful=True,
                    is_completed=False,
                ),
                ListeningSession(
                    session_token="admin-track",
                    kind="track",
                    player="youtube",
                    show_id=show.id,
                    episode_id=episode.id,
                    show_url=show.url,
                    episode_url=episode.url,
                    artist_name="Test Artist",
                    track_title="Test Track",
                    started_at=now - timedelta(minutes=18),
                    last_event_at=now - timedelta(minutes=2),
                    listened_seconds=240,
                    duration_seconds=300,
                    max_position_seconds=240,
                    completion_ratio=0.8,
                    is_meaningful=True,
                    is_completed=False,
                ),
                ListeningSession(
                    session_token="admin-episode-old",
                    kind="episode",
                    player="nts_audio",
                    show_id=show.id,
                    episode_id=episode.id,
                    show_url=show.url,
                    episode_url=episode.url,
                    started_at=now - timedelta(days=120, minutes=20),
                    last_event_at=now - timedelta(days=120, minutes=3),
                    listened_seconds=720,
                    duration_seconds=3600,
                    max_position_seconds=720,
                    completion_ratio=0.2,
                    is_meaningful=True,
                    is_completed=False,
                ),
            ])
            session.commit()

    def tearDown(self):
        self.tempdir.cleanup()
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_admin_stats_include_all_time_listening_summary(self):
        response = self.client.get("/api/admin/stats")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("listening_summary", payload)
        summary = payload["listening_summary"]
        self.assertEqual(summary["episode_listens"], 2)
        self.assertEqual(summary["track_listens"], 1)
        self.assertEqual(summary["top_shows"][0]["name"], "Show Alpha")
        self.assertEqual(summary["top_artists"][0]["name"], "Test Artist")
        self.assertEqual(summary["top_genres"][0]["name"], "Ambient")


class TestAdminSettingsApi(unittest.TestCase):
    def setUp(self):
        self.previous_env = {
            "SECRET_KEY": os.environ.get("SECRET_KEY"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "NTS_SETTINGS_PATH": os.environ.get("NTS_SETTINGS_PATH"),
            "TRIM_ENABLED": os.environ.get("TRIM_ENABLED"),
            "TRIM_START_SECONDS": os.environ.get("TRIM_START_SECONDS"),
            "TRIM_END_SECONDS": os.environ.get("TRIM_END_SECONDS"),
            "YOUTUBE_API_KEY": os.environ.get("YOUTUBE_API_KEY"),
        }
        self.tempdir = tempfile.TemporaryDirectory()
        self.settings_path = os.path.join(self.tempdir.name, "settings.json")
        os.environ["SECRET_KEY"] = "test-secret-key-for-admin-settings"  # pragma: allowlist secret
        os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(self.tempdir.name, 'admin-settings.db')}"
        os.environ["NTS_SETTINGS_PATH"] = self.settings_path
        os.environ.pop("TRIM_ENABLED", None)
        os.environ.pop("TRIM_START_SECONDS", None)
        os.environ.pop("TRIM_END_SECONDS", None)
        os.environ.pop("YOUTUBE_API_KEY", None)

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_admin_settings_get_omits_removed_provider_keys(self):
        response = self.client.get("/api/admin/settings")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["settings"]["TRIM_ENABLED"])
        self.assertEqual(payload["settings"]["TRIM_START_SECONDS"], 12)
        self.assertEqual(payload["settings"]["TRIM_END_SECONDS"], 12)
        for key in REMOVED_SETTING_KEYS:
            self.assertNotIn(key, payload["settings"])

    def test_admin_settings_put_does_not_persist_removed_provider_keys(self):
        response = self.client.put(
            "/api/admin/settings",
            json={
                "TRIM_ENABLED": False,
                "YOUTUBE_API_KEY": "yt-key",  # pragma: allowlist secret
                REMOVED_SETTING_KEYS[0]: "removed-id",
                REMOVED_SETTING_KEYS[1]: "removed-secret",
                REMOVED_SETTING_KEYS[2]: "http://localhost/callback",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertFalse(payload["settings"]["TRIM_ENABLED"])
        for key in REMOVED_SETTING_KEYS:
            self.assertNotIn(key, payload["settings"])

        with open(self.settings_path, "r", encoding="utf-8") as fh:
            persisted = json.load(fh)

        self.assertEqual(persisted["YOUTUBE_API_KEY"], "yt-key")
        for key in REMOVED_SETTING_KEYS:
            self.assertNotIn(key, persisted)

    def test_admin_settings_endpoints_require_local_host_by_default(self):
        response = self.client.get(
            "/api/admin/settings",
            base_url="https://example.com",
            environ_base={"REMOTE_ADDR": "203.0.113.1"},
        )

        self.assertEqual(response.status_code, 403)
        payload = response.get_json()
        self.assertFalse(payload["success"])
