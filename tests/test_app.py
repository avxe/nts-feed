"""
Basic tests for the NTS Feed application.
"""
import os
import unittest
from nts_feed.app import create_app

REMOVED_PROVIDER = 'spoti' + 'fy'


class TestApp(unittest.TestCase):
    """Test case for the Flask application."""

    def setUp(self):
        """Set up test client."""
        os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-app')
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_index_route(self):
        """Test the index route."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_retired_admin_enrichment_routes_are_not_registered(self):
        self.assertEqual(self.client.get('/api/admin/artists/sample').status_code, 404)
        self.assertEqual(self.client.get('/api/admin/enrichment/status').status_code, 404)

    def test_removed_music_export_routes_are_not_registered(self):
        self.assertEqual(self.client.get('/login').status_code, 404)
        self.assertEqual(self.client.get('/callback').status_code, 404)
        self.assertEqual(self.client.get(f'/api/{REMOVED_PROVIDER}_status').status_code, 404)
        self.assertEqual(self.client.post(f'/create_{REMOVED_PROVIDER}_playlist').status_code, 404)
        self.assertEqual(self.client.post(f'/api/likes/{REMOVED_PROVIDER}').status_code, 404)
        self.assertEqual(self.client.post(f'/api/mixtape/{REMOVED_PROVIDER}').status_code, 404)

if __name__ == '__main__':
    unittest.main()
