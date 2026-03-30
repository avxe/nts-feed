import os
import unittest

from flask import render_template

from nts_feed.app import create_app

REMOVED_PROVIDER_LABEL = 'Spoti' + 'fy'
REMOVED_BUTTON_CLASS = 'btn-' + 'spoti' + 'fy'


class TestPages(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-pages')
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_discover_page_uses_episode_first_sections(self):
        response = self.client.get('/discover')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Surprise me', html)
        self.assertIn('Because you like', html)
        self.assertIn('Genre spotlight', html)
        self.assertNotIn('Vibes', html)
        self.assertNotIn('Bridge Tracks', html)
        self.assertNotIn('Taste Profile', html)
        self.assertNotIn('Meaningful episode listens', html)

    def test_search_page_exposes_artist_and_genre_tabs(self):
        response = self.client.get('/search?q=house')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Artists', html)
        self.assertIn('Genres', html)

    def test_admin_page_no_longer_mentions_vector_features(self):
        response = self.client.get('/admin')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Collection', html)
        self.assertIn('Listening', html)
        self.assertIn('All-Time Listening', html)
        self.assertIn('Episode Listens', html)
        self.assertNotIn(REMOVED_PROVIDER_LABEL, html)
        self.assertNotIn('Meaningful Episode Listens', html)
        self.assertNotIn('Artist Enrichment', html)
        self.assertNotIn('History', html)
        self.assertNotIn('Mixtape DJ', html)
        self.assertNotIn('Semantic Search', html)

    def test_home_page_no_longer_mentions_removed_provider(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn(REMOVED_PROVIDER_LABEL, html)
        self.assertNotIn(REMOVED_BUTTON_CLASS, html)

    def test_likes_page_no_longer_mentions_removed_provider(self):
        response = self.client.get('/likes')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn(REMOVED_PROVIDER_LABEL, html)
        self.assertNotIn(REMOVED_BUTTON_CLASS, html)

    def test_tracks_page_keeps_stats_route_and_uses_tracks_label(self):
        response = self.client.get('/stats')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Tracks - NTS Feed', html)
        self.assertIn('>Tracks</span>', html)
        self.assertIn('> Tracks</h1>', html)
        self.assertIn('id="statsPageRoot"', html)
        self.assertNotIn('>Stats</span>', html)

    def test_episode_list_item_partial_keeps_js_selector_contract(self):
        episode = {
            'url': 'https://www.nts.live/shows/test-show/episodes/test-episode',
            'audio_url': 'https://www.nts.live/shows/test-show/episodes/test-episode',
            'title': 'Episode Title',
            'date': 'March 29, 2026',
            'image_url': 'https://example.com/image.jpg',
            'genres': ['Ambient'],
            'tracklist': [
                {'artist': 'Artist Name', 'name': 'Track Name', 'timestamp': '00:30'},
            ],
            'is_new': True,
            'is_downloaded': False,
        }

        with self.app.test_request_context('/'):
            html = render_template(
                '_episode_list_item.html',
                episode=episode,
                show_link_url='https://www.nts.live/shows/test-show',
                show_link_title='Test Show',
                episode_href=episode['url'],
            )

        self.assertIn('class="episode-item new', html)
        self.assertIn('class="download-button"', html)
        self.assertIn('class="episode-title"', html)
        self.assertIn('class="episode-date"', html)
        self.assertIn('class="episode-tracklist"', html)
        self.assertIn('class="track-info-btn"', html)
        self.assertIn('class="track-youtube-btn"', html)
        self.assertIn('class="track-like-btn"', html)
