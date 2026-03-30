import os
import tempfile
import unittest
from collections import defaultdict
from unittest.mock import patch

import queue

from flask import Flask
from sqlalchemy import text

from nts_feed.app import create_app
from nts_feed.blueprints.updates import bp as updates_bp
from nts_feed.services_init import init_services


class _StubUpdateService:
    def __init__(self):
        self.cleanup_calls = 0
        self.start_calls = []
        self.queues = {'job-1': queue.Queue()}

    def cleanup_completed_updates(self, *args, **kwargs):
        self.cleanup_calls += 1

    def start_update(self, enable_auto_download=True):
        self.start_calls.append(enable_auto_download)
        return 'job-1'

    def get_progress_queue(self, update_id):
        return self.queues.get(update_id)

    def get_progress(self, update_id):
        return None


class BackendRuntimeRefactorTest(unittest.TestCase):
    def test_create_app_registers_discover_routes_from_dedicated_blueprint(self):
        previous_secret = os.environ.get('SECRET_KEY')
        os.environ['SECRET_KEY'] = 'test-secret-key-for-route-contracts'  # pragma: allowlist secret
        try:
            app = create_app()
        finally:
            if previous_secret is None:
                os.environ.pop('SECRET_KEY', None)
            else:
                os.environ['SECRET_KEY'] = previous_secret

        endpoints_by_rule = defaultdict(set)
        for rule in app.url_map.iter_rules():
            endpoints_by_rule[rule.rule].add(rule.endpoint)

        self.assertEqual(endpoints_by_rule['/api/discover'], {'api_discover.api_discover'})
        self.assertEqual(
            endpoints_by_rule['/api/discover/surprise'],
            {'api_discover.api_discover_surprise'},
        )
        self.assertEqual(
            endpoints_by_rule['/api/discover/genre/<genre_slug>'],
            {'api_discover.api_discover_genre'},
        )
        self.assertEqual(
            endpoints_by_rule['/api/discover/next-up'],
            {'api_discover.api_discover_next_up'},
        )
        self.assertEqual(
            endpoints_by_rule['/api/discover/next-up/state'],
            {'api_discover.api_discover_next_up_state'},
        )
        self.assertEqual(endpoints_by_rule['/api/mixtape/save'], {'api_mixtape.api_mixtape_save'})
        self.assertEqual(
            endpoints_by_rule['/api/mixtapes'],
            {'api_mixtape.api_list_mixtapes', 'api_mixtape.api_save_mixtape'},
        )

    def test_init_services_registers_update_service_on_app_extensions(self):
        app = Flask(__name__)
        init_services(app)
        self.assertIn('update_service', app.extensions)

    @patch('nts_feed.blueprints.updates.load_shows', return_value={})
    def test_updates_blueprint_uses_app_registered_update_service(self, _load_shows):
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.extensions['update_service'] = _StubUpdateService()
        app.register_blueprint(updates_bp)
        client = app.test_client()

        response = client.post('/update_async')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['update_id'], 'job-1')
        self.assertEqual(app.extensions['update_service'].cleanup_calls, 1)
        self.assertEqual(app.extensions['update_service'].start_calls, [True])

    def test_bootstrap_database_module_initializes_engine_session_and_schema(self):
        from nts_feed.db.bootstrap import bootstrap_database

        previous_db_url = os.environ.get('DATABASE_URL')
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ['DATABASE_URL'] = f"sqlite:///{os.path.join(tmpdir, 'bootstrap.db')}"
            app = Flask(__name__)

            try:
                bootstrap_database(app)
            finally:
                if previous_db_url is None:
                    os.environ.pop('DATABASE_URL', None)
                else:
                    os.environ['DATABASE_URL'] = previous_db_url

            self.assertIn('db_engine', app.extensions)
            self.assertIn('db_sessionmaker', app.extensions)
            self.assertIn('Base', app.extensions)

            engine = app.extensions['db_engine']
            with engine.connect() as connection:
                tracks_columns = {
                    row[1]
                    for row in connection.execute(text('PRAGMA table_info(tracks)')).fetchall()
                }

            self.assertIn('youtube_video_id', tracks_columns)
            self.assertIn('youtube_lookup_attempted_at', tracks_columns)


if __name__ == '__main__':
    unittest.main()
