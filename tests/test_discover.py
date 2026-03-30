import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from nts_feed.blueprints import api_discover
from nts_feed.blueprints.api_discover import bp as discover_bp
from nts_feed.db.models import (
    Base,
    Artist,
    Episode,
    EpisodeGenre,
    EpisodeTrack,
    Genre,
    LikedTrack,
    ListeningSession,
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


class DiscoverApiTest(unittest.TestCase):
    def setUp(self):
        self.sessionmaker = build_test_sessionmaker()
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.extensions['db_sessionmaker'] = self.sessionmaker
        self.app.extensions['Base'] = Base
        self.app.register_blueprint(discover_bp)
        self.client = self.app.test_client()

    def seed_library(self):
        with self.sessionmaker() as session:
            house = Genre(name='House')
            ambient = Genre(name='Ambient')

            artist_alpha = Artist(name='Alpha Artist')
            artist_beta = Artist(name='Beta Artist')
            track_alpha = Track(
                title_original='Night Pulse',
                title_norm='night pulse',
                canonical_artist_set_hash='alpha-hash',
                artists=[artist_alpha],
            )
            track_beta = Track(
                title_original='Cloud Drift',
                title_norm='cloud drift',
                canonical_artist_set_hash='beta-hash',
                artists=[artist_beta],
            )

            show_alpha = Show(
                url='https://www.nts.live/shows/show-alpha',
                title='Show Alpha',
                description='House and club music',
                thumbnail='https://img.example.com/show-alpha.jpg',
            )
            show_beta = Show(
                url='https://www.nts.live/shows/show-beta',
                title='Show Beta',
                description='Ambient sessions',
                thumbnail='https://img.example.com/show-beta.jpg',
            )

            episode_alpha_new = Episode(
                show=show_alpha,
                url='https://www.nts.live/shows/show-alpha/episodes/alpha-new',
                title='Alpha New',
                date='March 10, 2026',
                image_url='https://img.example.com/alpha-new.jpg',
                audio_url='https://audio.example.com/alpha-new.mp3',
            )
            episode_alpha_old = Episode(
                show=show_alpha,
                url='https://www.nts.live/shows/show-alpha/episodes/alpha-old',
                title='Alpha Old',
                date='January 03, 2026',
                image_url='https://img.example.com/alpha-old.jpg',
                audio_url='https://audio.example.com/alpha-old.mp3',
            )
            episode_beta = Episode(
                show=show_beta,
                url='https://www.nts.live/shows/show-beta/episodes/beta-ambient',
                title='Beta Ambient',
                date='February 18, 2026',
                image_url='https://img.example.com/beta-ambient.jpg',
                audio_url='https://audio.example.com/beta-ambient.mp3',
            )

            session.add_all([
                house,
                ambient,
                artist_alpha,
                artist_beta,
                track_alpha,
                track_beta,
                show_alpha,
                show_beta,
                episode_alpha_new,
                episode_alpha_old,
                episode_beta,
            ])
            session.flush()

            session.add_all([
                EpisodeGenre(episode_id=episode_alpha_new.id, genre_id=house.id),
                EpisodeGenre(episode_id=episode_alpha_old.id, genre_id=house.id),
                EpisodeGenre(episode_id=episode_beta.id, genre_id=ambient.id),
                EpisodeTrack(episode_id=episode_alpha_new.id, track_id=track_alpha.id, track_order=1),
                EpisodeTrack(episode_id=episode_alpha_old.id, track_id=track_alpha.id, track_order=1),
                EpisodeTrack(episode_id=episode_beta.id, track_id=track_beta.id, track_order=1),
                LikedTrack(
                    artist='Alpha Artist',
                    title='Night Pulse',
                    track_id=track_alpha.id,
                    episode_url=episode_alpha_new.url,
                    episode_title=episode_alpha_new.title,
                    show_title=show_alpha.title,
                ),
            ])
            session.commit()

    @patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value={})
    def test_discover_returns_empty_shelves_without_subscriptions(self, _load_shows):
        response = self.client.get('/api/discover')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['sections']['continue_listening'], [])
        self.assertEqual(payload['sections']['because_you_like'], [])
        self.assertEqual(payload['sections']['genre_spotlight'], [])
        self.assertEqual(payload['listening_summary']['episode_listens'], 0)
        self.assertEqual(payload['listening_summary']['track_listens'], 0)
        self.assertEqual(payload['listening_summary']['top_shows'], [])
        self.assertEqual(payload['listening_summary']['top_artists'], [])
        self.assertEqual(payload['listening_summary']['top_genres'], [])

    def test_discover_returns_episode_sections_and_reason_labels(self):
        self.seed_library()
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.get('/api/discover')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('continue_listening', payload['sections'])
        self.assertIn('because_you_like', payload['sections'])
        self.assertIn('genre_spotlight', payload['sections'])
        self.assertEqual(payload['sections']['because_you_like'][0]['reason_label'], 'Matches liked artists')
        self.assertEqual(payload['sections']['genre_spotlight'][0]['genre'], 'House')
        self.assertEqual(payload['sections']['genre_spotlight'][0]['episodes'][0]['matched_genres'], ['House'])

    def test_discover_reuses_cached_response_for_same_subscriptions(self):
        self.seed_library()
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }
        original_build_state = api_discover._build_discover_state
        build_calls = 0

        def counted_build_state(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            return original_build_state(*args, **kwargs)

        with (
            patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed),
            patch('nts_feed.blueprints.api_discover.discover_impl._build_discover_state', side_effect=counted_build_state),
        ):
            first_response = self.client.get('/api/discover')
            second_response = self.client.get('/api/discover')

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(first_response.get_json(), second_response.get_json())
        self.assertEqual(build_calls, 1)

    def test_discover_follow_up_endpoints_reuse_cached_state(self):
        self.seed_library()
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }
        original_build_state = api_discover._build_discover_state
        build_calls = 0

        def counted_build_state(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            return original_build_state(*args, **kwargs)

        with (
            patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed),
            patch('nts_feed.blueprints.api_discover.discover_impl._build_discover_state', side_effect=counted_build_state),
        ):
            discover_response = self.client.get('/api/discover')
            surprise_response = self.client.post('/api/discover/surprise')
            genre_response = self.client.get('/api/discover/genre/house')

        self.assertEqual(discover_response.status_code, 200)
        self.assertEqual(surprise_response.status_code, 200)
        self.assertEqual(genre_response.status_code, 200)
        self.assertEqual(build_calls, 1)

    def test_next_up_state_mutation_clears_discover_cache_namespace(self):
        self.seed_library()
        self.app.extensions['discover_cache'] = {
            'cached-signature': {
                'bundle': {'state': {'candidates': []}, 'payload': {'success': True}},
                'expires_at': datetime.utcnow().timestamp() + 60,
            }
        }
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.post(
                '/api/discover/next-up/state',
                json={
                    'episode_url': 'https://www.nts.live/shows/show-alpha/episodes/alpha-new',
                    'action': 'dismiss',
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.app.extensions['discover_cache'], {})

    def test_load_discover_catalog_returns_overlap_maps_and_genre_profile(self):
        self.seed_library()
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }

        with self.app.app_context():
            with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
                catalog = api_discover._load_discover_catalog(subscribed=subscribed)

        episodes_by_title = {episode['episode_title']: episode for episode in catalog['episodes']}
        alpha_new_id = episodes_by_title['Alpha New']['episode_id']
        alpha_old_id = episodes_by_title['Alpha Old']['episode_id']
        beta_id = episodes_by_title['Beta Ambient']['episode_id']

        self.assertEqual(catalog['liked_artist_overlap_by_episode'][alpha_new_id], {'alpha artist'})
        self.assertEqual(catalog['liked_artist_overlap_by_episode'][alpha_old_id], {'alpha artist'})
        self.assertNotIn(beta_id, catalog['liked_artist_overlap_by_episode'])
        self.assertEqual(catalog['episode_genres_by_id'][alpha_new_id], ['House'])
        self.assertGreater(catalog['genre_profile']['house'], catalog['genre_profile']['ambient'])

    def test_discover_surprise_returns_episode_card(self):
        self.seed_library()
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.post('/api/discover/surprise')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertIsNotNone(payload['episode'])
        self.assertIn(payload['episode']['show_title'], {'Show Alpha', 'Show Beta'})
        self.assertIn('reason_label', payload['episode'])

    def test_discover_summarizes_recent_meaningful_listening_sessions(self):
        self.seed_library()
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }

        with self.sessionmaker() as session:
            beta_episode = session.query(Episode).filter(Episode.title == 'Beta Ambient').one()
            beta_show = session.query(Show).filter(Show.title == 'Show Beta').one()
            beta_track = session.query(Track).filter(Track.title_original == 'Cloud Drift').one()
            now = datetime.utcnow()
            session.add_all([
                ListeningSession(
                    session_token='episode-listen-1',
                    kind='episode',
                    player='nts_audio',
                    show_id=beta_show.id,
                    episode_id=beta_episode.id,
                    show_url=beta_show.url,
                    episode_url=beta_episode.url,
                    started_at=now - timedelta(minutes=5),
                    last_event_at=now - timedelta(minutes=1),
                    listened_seconds=900,
                    duration_seconds=3600,
                    max_position_seconds=900,
                    completion_ratio=0.25,
                    is_meaningful=True,
                    is_completed=False,
                ),
                ListeningSession(
                    session_token='track-listen-1',
                    kind='track',
                    player='youtube',
                    show_id=beta_show.id,
                    episode_id=beta_episode.id,
                    track_id=beta_track.id,
                    show_url=beta_show.url,
                    episode_url=beta_episode.url,
                    artist_name='Beta Artist',
                    track_title='Cloud Drift',
                    started_at=now - timedelta(minutes=15),
                    last_event_at=now - timedelta(minutes=2),
                    listened_seconds=240,
                    duration_seconds=300,
                    max_position_seconds=240,
                    completion_ratio=0.8,
                    is_meaningful=True,
                    is_completed=False,
                ),
            ])
            session.commit()

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.get('/api/discover')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        summary = payload['listening_summary']
        self.assertEqual(summary['episode_listens'], 1)
        self.assertEqual(summary['track_listens'], 1)
        self.assertEqual(summary['top_shows'][0]['name'], 'Show Beta')
        self.assertEqual(summary['top_artists'][0]['name'], 'Beta Artist')
        self.assertEqual(summary['top_genres'][0]['name'], 'Ambient')

    def test_discover_excludes_completed_episodes_from_because_you_like(self):
        self.seed_library()
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }

        with self.sessionmaker() as session:
            ambient = session.query(Genre).filter(Genre.name == 'Ambient').one()
            beta_show = session.query(Show).filter(Show.title == 'Show Beta').one()
            beta_track = session.query(Track).filter(Track.title_original == 'Cloud Drift').one()
            listened_episode = Episode(
                show=beta_show,
                url='https://www.nts.live/shows/show-beta/episodes/beta-return',
                title='Beta Return',
                date='March 19, 2026',
                image_url='https://img.example.com/beta-return.jpg',
                audio_url='https://audio.example.com/beta-return.mp3',
            )
            session.add(listened_episode)
            session.flush()
            session.add_all([
                EpisodeGenre(episode_id=listened_episode.id, genre_id=ambient.id),
                EpisodeTrack(episode_id=listened_episode.id, track_id=beta_track.id, track_order=1),
            ])
            now = datetime.utcnow()
            session.add_all([
                ListeningSession(
                    session_token='episode-listen-repeat',
                    kind='episode',
                    player='nts_audio',
                    show_id=beta_show.id,
                    episode_id=listened_episode.id,
                    show_url=beta_show.url,
                    episode_url=listened_episode.url,
                    started_at=now - timedelta(minutes=45),
                    last_event_at=now - timedelta(minutes=5),
                    listened_seconds=3200,
                    duration_seconds=3600,
                    max_position_seconds=3400,
                    completion_ratio=0.89,
                    is_meaningful=True,
                    is_completed=True,
                ),
            ])
            session.commit()

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.get('/api/discover')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        because_titles = [item['episode_title'] for item in payload['sections']['because_you_like']]
        self.assertNotIn('Beta Return', because_titles)
        genre_spotlight = payload['sections']['genre_spotlight']
        ambient_shelf = next((s for s in genre_spotlight if s['genre'] == 'Ambient'), None)
        self.assertIsNotNone(ambient_shelf)
        ambient_titles = [ep['episode_title'] for ep in ambient_shelf['episodes']]
        self.assertIn('Beta Return', ambient_titles)

    def test_discover_excludes_liked_source_episodes_from_because_you_like(self):
        """Episodes you liked a track FROM should not appear in because_you_like.

        seed_library creates a LikedTrack for 'Night Pulse' with
        episode_url pointing to 'Alpha New'. Alpha New should be filtered
        from because_you_like, while Alpha Old (same artist/track, different
        episode) should still appear.
        """
        self.seed_library()
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.get('/api/discover')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        because_titles = [
            item['episode_title']
            for item in payload['sections']['because_you_like']
        ]
        self.assertNotIn('Alpha New', because_titles)
        self.assertIn('Alpha Old', because_titles)

    def test_discover_ranks_fresh_above_partial_and_excludes_completed(self):
        """Three ambient episodes from Show Beta: fresh, partially-listened, completed.

        because_you_like should contain fresh and partial (fresh ranked higher),
        completed should be absent from because_you_like but still present in
        the genre_spotlight shelf.
        """
        self.seed_library()
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }

        with self.sessionmaker() as session:
            ambient = session.query(Genre).filter(Genre.name == 'Ambient').one()
            beta_show = session.query(Show).filter(Show.title == 'Show Beta').one()
            beta_track = session.query(Track).filter(Track.title_original == 'Cloud Drift').one()

            ep_fresh = Episode(
                show=beta_show,
                url='https://www.nts.live/shows/show-beta/episodes/beta-fresh',
                title='Beta Fresh',
                date='March 20, 2026',
                image_url='https://img.example.com/beta-fresh.jpg',
                audio_url='https://audio.example.com/beta-fresh.mp3',
            )
            ep_partial = Episode(
                show=beta_show,
                url='https://www.nts.live/shows/show-beta/episodes/beta-partial',
                title='Beta Partial',
                date='March 18, 2026',
                image_url='https://img.example.com/beta-partial.jpg',
                audio_url='https://audio.example.com/beta-partial.mp3',
            )
            ep_completed = Episode(
                show=beta_show,
                url='https://www.nts.live/shows/show-beta/episodes/beta-completed',
                title='Beta Completed',
                date='March 15, 2026',
                image_url='https://img.example.com/beta-completed.jpg',
                audio_url='https://audio.example.com/beta-completed.mp3',
            )
            session.add_all([ep_fresh, ep_partial, ep_completed])
            session.flush()
            session.add_all([
                EpisodeGenre(episode_id=ep_fresh.id, genre_id=ambient.id),
                EpisodeGenre(episode_id=ep_partial.id, genre_id=ambient.id),
                EpisodeGenre(episode_id=ep_completed.id, genre_id=ambient.id),
                EpisodeTrack(episode_id=ep_fresh.id, track_id=beta_track.id, track_order=1),
                EpisodeTrack(episode_id=ep_partial.id, track_id=beta_track.id, track_order=1),
                EpisodeTrack(episode_id=ep_completed.id, track_id=beta_track.id, track_order=1),
            ])
            now = datetime.utcnow()
            session.add_all([
                ListeningSession(
                    session_token='partial-listen',
                    kind='episode',
                    player='nts_audio',
                    show_id=beta_show.id,
                    episode_id=ep_partial.id,
                    show_url=beta_show.url,
                    episode_url=ep_partial.url,
                    started_at=now - timedelta(hours=2),
                    last_event_at=now - timedelta(hours=1),
                    listened_seconds=900,
                    duration_seconds=3600,
                    max_position_seconds=950,
                    completion_ratio=0.25,
                    is_meaningful=True,
                    is_completed=False,
                ),
                ListeningSession(
                    session_token='completed-listen',
                    kind='episode',
                    player='nts_audio',
                    show_id=beta_show.id,
                    episode_id=ep_completed.id,
                    show_url=beta_show.url,
                    episode_url=ep_completed.url,
                    started_at=now - timedelta(hours=3),
                    last_event_at=now - timedelta(hours=2),
                    listened_seconds=3200,
                    duration_seconds=3600,
                    max_position_seconds=3400,
                    completion_ratio=0.89,
                    is_meaningful=True,
                    is_completed=True,
                ),
            ])
            session.commit()

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.get('/api/discover')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        because_titles = [
            item['episode_title']
            for item in payload['sections']['because_you_like']
        ]
        self.assertIn('Beta Fresh', because_titles)
        self.assertIn('Beta Partial', because_titles)
        self.assertNotIn('Beta Completed', because_titles)
        self.assertLess(
            because_titles.index('Beta Fresh'),
            because_titles.index('Beta Partial'),
        )

        genre_spotlight = payload['sections']['genre_spotlight']
        ambient_shelf = next((s for s in genre_spotlight if s['genre'] == 'Ambient'), None)
        self.assertIsNotNone(ambient_shelf)
        ambient_titles = [ep['episode_title'] for ep in ambient_shelf['episodes']]
        self.assertIn('Beta Completed', ambient_titles)

    def test_discover_genre_returns_matching_episodes(self):
        self.seed_library()
        subscribed = {
            'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
            'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
        }

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.get('/api/discover/genre/house')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['genre'], 'House')
        self.assertGreaterEqual(len(payload['episodes']), 1)
        self.assertEqual(payload['episodes'][0]['matched_genres'], ['House'])
