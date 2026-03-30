import os
import tempfile
import unittest
from unittest.mock import patch

from nts_feed.app import create_app

REMOVED_PROVIDER_KEY = "spoti" + "fy"


class TestTrackInfoApis(unittest.TestCase):
    def setUp(self):
        self.previous_env = {
            "SECRET_KEY": os.environ.get("SECRET_KEY"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
        }
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["SECRET_KEY"] = "test-secret-key-for-api-tracks"  # pragma: allowlist secret
        os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(self.tempdir.name, 'api-tracks.db')}"

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

    @patch("nts_feed.blueprints.api_tracks.get_discogs")
    def test_artist_info_omits_removed_provider_link(self, get_discogs):
        svc = get_discogs.return_value
        svc.search_artist.return_value = []
        svc.search_release.return_value = []

        response = self.client.get("/api/artist_info?name=Burial")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("links", payload)
        self.assertIn("discogs", payload["links"])
        self.assertIn("youtube", payload["links"])
        self.assertNotIn(REMOVED_PROVIDER_KEY, payload["links"])

    @patch("nts_feed.blueprints.api_tracks.get_discogs")
    def test_track_info_omits_removed_provider_link(self, get_discogs):
        svc = get_discogs.return_value
        svc.search_release.return_value = []

        response = self.client.get("/api/track_info?artist=Burial&title=Archangel")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("links", payload)
        self.assertIn("discogs", payload["links"])
        self.assertIn("youtube", payload["links"])
        self.assertNotIn(REMOVED_PROVIDER_KEY, payload["links"])
