import os
import tempfile
import time
import unittest

from sqlalchemy import text

from nts_feed.app import create_app
from nts_feed.db.models import Episode, Show


class TestListeningSessionsApi(unittest.TestCase):
    def setUp(self):
        self.previous_env = {
            "SECRET_KEY": os.environ.get("SECRET_KEY"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
        }
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["SECRET_KEY"] = "test-secret-key-for-listening"  # pragma: allowlist secret
        os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(self.tempdir.name, 'listening.db')}"

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        with self.app.extensions["db_sessionmaker"]() as session:
            show = Show(
                url="https://www.nts.live/shows/show-alpha",
                title="Show Alpha",
                description="House and club music",
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
            session.add_all([show, episode])
            session.commit()

        self.app.extensions["discover_cache"] = {
            "seed": {"expires_at": time.time() + 60, "payload": {"sections": {}}}
        }

    def tearDown(self):
        self.tempdir.cleanup()
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_listening_session_upserts_resolves_context_and_clears_discover_cache(self):
        payload = {
            "session_token": "episode-session-1",
            "kind": "episode",
            "player": "nts_audio",
            "episode_url": "https://www.nts.live/shows/show-alpha/episodes/alpha-new",
            "show_url": "https://www.nts.live/shows/show-alpha",
            "duration_seconds": 3600,
            "listened_seconds": 180,
            "max_position_seconds": 240,
            "started_at": "2026-03-22T12:00:00Z",
            "last_event_at": "2026-03-22T12:03:00Z",
            "ended_at": None,
        }

        response = self.client.post("/api/listening/sessions", json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])

        update_response = self.client.post(
            "/api/listening/sessions",
            json={
                **payload,
                "listened_seconds": 420,
                "max_position_seconds": 640,
                "last_event_at": "2026-03-22T12:07:00Z",
            },
        )

        self.assertEqual(update_response.status_code, 200)

        with self.app.extensions["db_engine"].connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        session_token,
                        kind,
                        player,
                        episode_url,
                        show_url,
                        episode_id,
                        show_id,
                        listened_seconds,
                        duration_seconds,
                        max_position_seconds,
                        completion_ratio,
                        is_meaningful,
                        is_completed
                    FROM listening_sessions
                    WHERE session_token = :session_token
                    """
                ),
                {"session_token": "episode-session-1"},
            ).mappings().one()

        self.assertEqual(row["kind"], "episode")
        self.assertEqual(row["player"], "nts_audio")
        self.assertEqual(row["episode_url"], payload["episode_url"])
        self.assertEqual(row["show_url"], payload["show_url"])
        self.assertIsNotNone(row["episode_id"])
        self.assertIsNotNone(row["show_id"])
        self.assertEqual(round(row["listened_seconds"]), 420)
        self.assertEqual(round(row["duration_seconds"]), 3600)
        self.assertEqual(round(row["max_position_seconds"]), 640)
        self.assertAlmostEqual(row["completion_ratio"], 420 / 3600, places=3)
        self.assertEqual(row["is_meaningful"], 1)
        self.assertEqual(row["is_completed"], 0)
        self.assertEqual(self.app.extensions["discover_cache"], {})

    def test_listening_session_accepts_nested_context_and_recomputes_flags(self):
        payload = {
            "session_token": "track-session-nested",
            "kind": "track",
            "player": "youtube",
            "listened_seconds": 30,
            "duration_seconds": 100,
            "max_position_seconds": 95,
            "is_meaningful": False,
            "is_completed": True,
            "context": {
                "show_url": "https://www.nts.live/shows/show-alpha",
                "episode_url": "https://www.nts.live/shows/show-alpha/episodes/alpha-new",
                "track_artist": "Nested Artist",
                "track_title": "Nested Title",
            },
        }

        response = self.client.post("/api/listening/sessions", json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["is_meaningful"])
        self.assertFalse(data["is_completed"])
        self.assertAlmostEqual(data["completion_ratio"], 0.3, places=3)

        with self.app.extensions["db_engine"].connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        show_id,
                        episode_id,
                        artist_name,
                        track_title,
                        completion_ratio,
                        is_meaningful,
                        is_completed
                    FROM listening_sessions
                    WHERE session_token = :session_token
                    """
                ),
                {"session_token": "track-session-nested"},
            ).mappings().one()

        self.assertIsNotNone(row["show_id"])
        self.assertIsNotNone(row["episode_id"])
        self.assertEqual(row["artist_name"], "Nested Artist")
        self.assertEqual(row["track_title"], "Nested Title")
        self.assertAlmostEqual(row["completion_ratio"], 0.3, places=3)
        self.assertEqual(row["is_meaningful"], 1)
        self.assertEqual(row["is_completed"], 0)
