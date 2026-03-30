import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from nts_feed.db.models import Base, Artist, Track
from nts_feed.services.youtube_service import YouTubeService, _artist_set_hash


def build_test_sessionmaker():
    engine = create_engine(
        'sqlite://',
        future=True,
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


class YouTubeServicePersistenceTest(unittest.TestCase):
    def setUp(self):
        self.sessionmaker = build_test_sessionmaker()
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = YouTubeService(api_key='test-key')
        self.service.cache_dir = self.tempdir.name
        self.service.quota_file = f'{self.tempdir.name}/quota.json'
        self.service._reset_quota_data()

    def tearDown(self):
        self.tempdir.cleanup()

    def seed_track(self, **track_updates):
        defaults = {
            'title_original': 'Night Pulse',
            'title_norm': 'night pulse',
            'canonical_artist_set_hash': _artist_set_hash(['Alpha Artist']),
        }
        defaults.update(track_updates)
        with self.sessionmaker() as session:
            artist = Artist(name='Alpha Artist')
            track = Track(artists=[artist], **defaults)
            session.add_all([artist, track])
            session.commit()
            return track.id

    def test_returns_persisted_track_resolution_without_api_search(self):
        self.seed_track(
            youtube_video_id='abc123',
            youtube_video_url='https://www.youtube.com/watch?v=abc123',
            youtube_embed_url='https://www.youtube.com/embed/abc123',
            youtube_title='Alpha Artist - Night Pulse',
            youtube_channel='Alpha Channel',
            youtube_thumbnail='https://img.youtube.com/abc123/hqdefault.jpg',
            youtube_search_only=False,
            youtube_lookup_attempted_at=datetime.utcnow(),
        )

        with patch('nts_feed.services.youtube_service.build') as build_mock:
            result = self.service.find_best_video(
                'Alpha Artist',
                'Night Pulse',
                db_sessionmaker=self.sessionmaker,
            )

        self.assertTrue(result['success'])
        self.assertEqual(result['video_id'], 'abc123')
        build_mock.assert_not_called()

    def test_persists_successful_api_resolution_on_track(self):
        track_id = self.seed_track()

        fake_search = Mock()
        fake_search.list.return_value.execute.return_value = {
            'items': [{
                'id': {'videoId': 'xyz789'},
                'snippet': {
                    'title': 'Night Pulse (Official Audio)',
                    'channelTitle': 'Alpha Channel',
                    'thumbnails': {'high': {'url': 'https://img.youtube.com/xyz789/hqdefault.jpg'}},
                },
            }]
        }
        fake_youtube = Mock()
        fake_youtube.search.return_value = fake_search

        with patch('nts_feed.services.youtube_service.build', return_value=fake_youtube):
            result = self.service.find_best_video(
                'Alpha Artist',
                'Night Pulse',
                db_sessionmaker=self.sessionmaker,
            )

        self.assertTrue(result['success'])
        self.assertEqual(result['video_id'], 'xyz789')

        with self.sessionmaker() as session:
            track = session.get(Track, track_id)
            self.assertEqual(track.youtube_video_id, 'xyz789')
            self.assertEqual(track.youtube_title, 'Night Pulse (Official Audio)')
            self.assertFalse(track.youtube_search_only)
            self.assertIsNotNone(track.youtube_lookup_attempted_at)

    def test_persists_search_only_result_to_avoid_repeat_searches(self):
        track_id = self.seed_track()

        fake_search = Mock()
        fake_search.list.return_value.execute.return_value = {'items': []}
        fake_youtube = Mock()
        fake_youtube.search.return_value = fake_search

        with patch('nts_feed.services.youtube_service.build', return_value=fake_youtube):
            first = self.service.find_best_video(
                'Alpha Artist',
                'Night Pulse',
                db_sessionmaker=self.sessionmaker,
            )

        self.assertTrue(first['success'])
        self.assertTrue(first['search_only'])

        with self.sessionmaker() as session:
            track = session.get(Track, track_id)
            self.assertTrue(track.youtube_search_only)
            self.assertIsNotNone(track.youtube_video_url)
            self.assertIsNotNone(track.youtube_lookup_attempted_at)

        with patch('nts_feed.services.youtube_service.build') as build_mock:
            second = self.service.find_best_video(
                'Alpha Artist',
                'Night Pulse',
                db_sessionmaker=self.sessionmaker,
            )

        self.assertTrue(second['success'])
        self.assertTrue(second['search_only'])
        build_mock.assert_not_called()
