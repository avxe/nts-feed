from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask

from nts_feed.blueprints.shows_mgmt import bp as shows_mgmt_bp


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)
        return None


class SubscribeFlowTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.register_blueprint(shows_mgmt_bp)
        self.client = self.app.test_client()

    @patch("nts_feed.blueprints.shows_mgmt.time.sleep", return_value=None)
    @patch("nts_feed.blueprints.shows_mgmt.get_executor", return_value=_ImmediateExecutor())
    @patch("nts_feed.blueprints.shows_mgmt.get_image_cache", return_value=None)
    @patch("nts_feed.blueprints.shows_mgmt.save_episodes")
    @patch("nts_feed.blueprints.shows_mgmt.save_shows")
    @patch("nts_feed.blueprints.shows_mgmt.load_shows", return_value={})
    @patch("nts_feed.blueprints.shows_mgmt.scrape_nts_show_progress")
    @patch("nts_feed.blueprints.api_db.get_running_sync_job", return_value=None)
    @patch("nts_feed.blueprints.api_db.start_sync_job", return_value="sync-job-1")
    @patch("nts_feed.blueprints.api_db.get_sync_job_info")
    def test_subscribe_async_waits_for_sync_completion_before_stream_finishes(
        self,
        mock_get_sync_job_info,
        _mock_start_sync_job,
        _mock_get_running_sync_job,
        _mock_scrape,
        _mock_load_shows,
        _mock_save_shows,
        _mock_save_episodes,
        _mock_get_image_cache,
        _mock_get_executor,
        _mock_sleep,
    ):
        def _fake_scrape(_url, *, on_progress=None, defer_tracklists=False):
            del defer_tracklists
            if on_progress:
                on_progress({
                    "type": "started",
                    "show_title": "Test Show",
                    "total_episodes": 1,
                })
                on_progress({
                    "type": "progress",
                    "current": 1,
                    "total": 1,
                    "episode_title": "Episode One",
                })
                on_progress({"type": "completed", "total": 1})
            return {
                "title": "Test Show",
                "description": "A show for testing",
                "thumbnail": "https://img.example.com/show.jpg",
                "episodes": [
                    {
                        "title": "Episode One",
                        "url": "https://www.nts.live/shows/test-show/episodes/episode-one",
                        "audio_url": "https://audio.example.com/episode-one.mp3",
                    }
                ],
            }

        _mock_scrape.side_effect = _fake_scrape
        mock_get_sync_job_info.side_effect = [
            {"status": "running", "phase": "rebuilding_database"},
            {
                "status": "completed",
                "phase": "completed",
                "db_stats": {"shows": 1, "episodes": 1},
            },
        ]

        response = self.client.post(
            "/subscribe_async",
            json={"url": "https://www.nts.live/shows/test-show"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        subscribe_id = payload["subscribe_id"]
        progress_queue = self.app.subscribe_queues[subscribe_id]

        events = []
        while True:
            item = progress_queue.get_nowait()
            if item is None:
                break
            events.append(item)

        sync_events = [event for event in events if event.get("type") == "sync_status"]
        completed_events = [event for event in events if event.get("type") == "completed"]
        self.assertGreaterEqual(len(sync_events), 2)
        self.assertEqual(sync_events[0]["sync"]["status"], "started")
        self.assertEqual(sync_events[-1]["sync"]["status"], "completed")
        self.assertEqual(sync_events[-1]["sync"]["db_stats"], {"shows": 1, "episodes": 1})
        self.assertEqual(len(completed_events), 1)
        self.assertEqual(events[-2]["type"], "sync_status")
        self.assertEqual(events[-2]["sync"]["status"], "completed")
        self.assertEqual(events[-1]["type"], "completed")
        self.assertEqual(events[-1]["total"], 1)


if __name__ == "__main__":
    unittest.main()
