import unittest

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from nts_feed.blueprints.api_search_unified import bp as search_bp
from nts_feed.db.models import (
    Base,
    Artist,
    Episode,
    EpisodeGenre,
    EpisodeTrack,
    Genre,
    Show,
    Track,
)


def build_test_sessionmaker():
    engine = create_engine(
        'sqlite://',
        future=True,
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


class SearchApiTest(unittest.TestCase):
    def setUp(self):
        self.sessionmaker = build_test_sessionmaker()
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.extensions['db_sessionmaker'] = self.sessionmaker
        self.app.extensions['Base'] = Base
        self.app.register_blueprint(search_bp)
        self.client = self.app.test_client()
        self.seed_library()

    def seed_library(self):
        with self.sessionmaker() as session:
            genre = Genre(name='House')
            artist = Artist(name='Alpha Artist')
            artist_radiohead = Artist(name='Radiohead')
            track = Track(
                title_original='Night Pulse',
                title_norm='night pulse',
                canonical_artist_set_hash='alpha-hash',
                artists=[artist],
            )
            radiohead_track = Track(
                title_original='Everything In Its Right Place',
                title_norm='everything in its right place',
                canonical_artist_set_hash='radiohead-hash',
                artists=[artist_radiohead],
            )
            show = Show(
                url='https://www.nts.live/shows/show-alpha',
                title='Show Alpha',
                description='House and club music',
                thumbnail='https://img.example.com/show-alpha.jpg',
            )
            episode = Episode(
                show=show,
                url='https://www.nts.live/shows/show-alpha/episodes/alpha-night',
                title='Alpha Night',
                date='March 10, 2026',
                image_url='https://img.example.com/alpha-night.jpg',
                audio_url='https://audio.example.com/alpha-night.mp3',
            )
            radiohead_episode = Episode(
                show=show,
                url='https://www.nts.live/shows/show-alpha/episodes/radiohead-special',
                title='Radiohead Special',
                date='March 12, 2026',
                image_url='https://img.example.com/radiohead-special.jpg',
                audio_url='https://audio.example.com/radiohead-special.mp3',
            )

            session.add_all([genre, artist, artist_radiohead, track, radiohead_track, show, episode, radiohead_episode])
            session.flush()
            session.add_all([
                EpisodeGenre(episode_id=episode.id, genre_id=genre.id),
                EpisodeTrack(episode_id=episode.id, track_id=track.id, track_order=1),
                EpisodeTrack(episode_id=radiohead_episode.id, track_id=radiohead_track.id, track_order=1),
            ])
            session.commit()

    def test_unified_search_returns_all_groups_for_empty_query(self):
        response = self.client.get('/api/search?q=')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['shows'], [])
        self.assertEqual(payload['episodes'], [])
        self.assertEqual(payload['tracks'], [])
        self.assertEqual(payload['artists'], [])
        self.assertEqual(payload['genres'], [])

    def test_unified_search_returns_artist_results_directly(self):
        response = self.client.get('/api/search?q=alpha')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertGreaterEqual(len(payload['artists']), 1)
        self.assertEqual(payload['artists'][0]['name'], 'Alpha Artist')

    def test_unified_search_returns_genre_results_directly(self):
        response = self.client.get('/api/search?q=house')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertGreaterEqual(len(payload['genres']), 1)
        self.assertEqual(payload['genres'][0]['name'], 'House')

    def test_unified_search_keeps_track_episode_and_show_groups(self):
        response = self.client.get('/api/search?q=alpha')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertGreaterEqual(len(payload['shows']), 1)
        self.assertGreaterEqual(len(payload['episodes']), 1)
        self.assertGreaterEqual(len(payload['tracks']), 1)
        self.assertIn('artists', payload)
        self.assertIn('genres', payload)

    def test_unified_search_supports_sqlalchemy_row_id_results(self):
        response = self.client.get('/api/search?q=show')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertGreaterEqual(len(payload['shows']), 1)
        self.assertEqual(payload['shows'][0]['title'], 'Show Alpha')

    def test_unified_search_matches_track_when_query_spans_artist_and_title(self):
        response = self.client.get('/api/search?q=radiohead everything in its right place')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertGreaterEqual(len(payload['tracks']), 1)
        self.assertEqual(payload['tracks'][0]['title'], 'Everything In Its Right Place')
