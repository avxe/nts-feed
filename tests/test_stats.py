import unittest

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from nts_feed.blueprints.api_tracks import bp as tracks_bp
from nts_feed.db.models import (
    Base,
    Artist,
    Episode,
    EpisodeTrack,
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


class StatsApiTest(unittest.TestCase):
    def setUp(self):
        self.sessionmaker = build_test_sessionmaker()
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.extensions['db_sessionmaker'] = self.sessionmaker
        self.app.extensions['Base'] = Base
        self.app.register_blueprint(tracks_bp)
        self.client = self.app.test_client()
        self.seed_library()

    def seed_library(self):
        with self.sessionmaker() as session:
            alpha_artist = Artist(name='Alpha Artist')
            alpha_track = Track(
                title_original='Night Pulse',
                title_norm='night pulse',
                canonical_artist_set_hash='alpha-hash',
                artists=[alpha_artist],
            )
            beta_artist = Artist(name='Beta Artist')
            beta_track = Track(
                title_original='Sunrise Echo',
                title_norm='sunrise echo',
                canonical_artist_set_hash='beta-hash',
                artists=[beta_artist],
            )
            alpha_show = Show(
                url='https://www.nts.live/shows/show-alpha',
                title='Show Alpha',
                description='House and club music',
                thumbnail='https://img.example.com/show-alpha.jpg',
            )
            beta_show = Show(
                url='https://www.nts.live/shows/show-beta',
                title='Show Beta',
                description='Ambient and downtempo',
                thumbnail='https://img.example.com/show-beta.jpg',
            )
            session.add_all([
                alpha_artist,
                alpha_track,
                beta_artist,
                beta_track,
                alpha_show,
                beta_show,
            ])
            session.flush()

            for index in range(4):
                episode = Episode(
                    show_id=alpha_show.id,
                    url=f'https://www.nts.live/shows/show-alpha/episodes/alpha-night-{index}',
                    title=f'Alpha Night {index}',
                    date=f'March {10 + index}, 2026',
                    image_url=f'https://img.example.com/alpha-night-{index}.jpg',
                    audio_url=f'https://audio.example.com/alpha-night-{index}.mp3',
                )
                session.add(episode)
                session.flush()
                session.add(EpisodeTrack(
                    episode_id=episode.id,
                    track_id=alpha_track.id,
                    track_order=index + 1,
                ))

            beta_episode = Episode(
                show_id=beta_show.id,
                url='https://www.nts.live/shows/show-beta/episodes/sunrise-session',
                title='Sunrise Session',
                date='March 20, 2026',
                image_url='https://img.example.com/sunrise-session.jpg',
                audio_url='https://audio.example.com/sunrise-session.mp3',
            )
            session.add(beta_episode)
            session.flush()
            session.add(EpisodeTrack(
                episode_id=beta_episode.id,
                track_id=beta_track.id,
                track_order=1,
            ))

            session.commit()

    def test_tracks_api_respects_episode_payload_limit(self):
        response = self.client.get('/api/tracks?per_page=10&episodes_limit=2')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(len(payload['tracks']), 2)
        self.assertEqual(payload['tracks'][0]['title'], 'Night Pulse')
        self.assertEqual(len(payload['tracks'][0]['all_episodes']), 2)

    def test_tracks_api_filters_by_artist_name(self):
        response = self.client.get('/api/tracks?artist_filter=alpha%20artist&per_page=10')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['total'], 1)
        self.assertEqual([track['title'] for track in payload['tracks']], ['Night Pulse'])
        self.assertEqual(payload['tracks'][0]['artists'], ['Alpha Artist'])

    def test_tracks_api_filters_by_show_title(self):
        response = self.client.get('/api/tracks?show_filter=show%20beta&per_page=10')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['total'], 1)
        self.assertEqual([track['title'] for track in payload['tracks']], ['Sunrise Echo'])

    def test_tracks_api_filters_by_track_title(self):
        response = self.client.get('/api/tracks?title_filter=night%20pulse&per_page=10')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['total'], 1)
        self.assertEqual([track['title'] for track in payload['tracks']], ['Night Pulse'])

    def test_tracks_api_filters_by_episode_title(self):
        response = self.client.get('/api/tracks?episode_filter=sunrise%20session&per_page=10')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['total'], 1)
        self.assertEqual([track['title'] for track in payload['tracks']], ['Sunrise Echo'])
