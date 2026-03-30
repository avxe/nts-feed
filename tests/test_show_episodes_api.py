import unittest
from urllib.parse import quote

from flask import Flask

from nts_feed.blueprints import api_tracks as api_tracks_module
from nts_feed.blueprints.api_tracks import bp as tracks_bp


class DummyTrackManager:
    def __init__(self, downloaded):
        self._downloaded = set(downloaded)

    def get_downloaded_episodes(self):
        return set(self._downloaded)


class ShowEpisodesApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.register_blueprint(tracks_bp)
        self.client = self.app.test_client()

        self.show_url = 'https://www.nts.live/shows/the-nts-guide-to'
        self.target_episode_url = 'https://www.nts.live/shows/the-nts-guide-to/episodes/all-blue---an-nts-introduction-to-modal'
        self.episodes = [
            {
                'url': f'https://www.nts.live/shows/the-nts-guide-to/episodes/episode-{index}',
                'title': f'Episode {index}',
                'date': f'2025-01-{index:02d}',
                'image_url': '',
                'audio_url': f'https://audio.example.com/{index}.mp3',
                'genres': [],
                'tracklist': [],
                'is_new': False,
            }
            for index in range(1, 22)
        ]
        self.episodes.append({
            'url': self.target_episode_url,
            'title': 'All Blue - An NTS Introduction to Modal',
            'date': '2025-12-14',
            'image_url': 'https://img.example.com/modal.jpg',
            'audio_url': 'https://audio.example.com/modal.mp3',
            'genres': ['Jazz'],
            'tracklist': [{'artist': 'Miles Davis', 'name': 'All Blues'}],
            'is_new': True,
        })

        self.original_load_shows = api_tracks_module.load_shows
        self.original_load_episodes = api_tracks_module.load_episodes
        self.original_slugify = api_tracks_module.slugify
        self.original_get_track_manager = api_tracks_module.get_track_manager

        api_tracks_module.load_shows = lambda: {
            self.show_url: {'title': 'NTS Guide to…'},
        }
        api_tracks_module.load_episodes = lambda slug: {'episodes': list(self.episodes)}
        api_tracks_module.slugify = lambda value: 'the-nts-guide-to'
        api_tracks_module.get_track_manager = lambda: DummyTrackManager({self.target_episode_url})

    def tearDown(self):
        api_tracks_module.load_shows = self.original_load_shows
        api_tracks_module.load_episodes = self.original_load_episodes
        api_tracks_module.slugify = self.original_slugify
        api_tracks_module.get_track_manager = self.original_get_track_manager

    def test_show_episode_lookup_returns_exact_episode_with_page_metadata(self):
        encoded_show_url = quote(self.show_url, safe='')
        response = self.client.get(
            f'/api/show/{encoded_show_url}/episode',
            query_string={'episode_url': self.target_episode_url, 'per_page': 20},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['episode']['url'], self.target_episode_url)
        self.assertTrue(payload['episode']['is_downloaded'])
        self.assertEqual(payload['page'], 2)
        self.assertEqual(payload['index'], 21)

