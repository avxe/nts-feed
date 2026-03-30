import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from nts_feed.blueprints.api_discover import bp as discover_bp
from nts_feed.db.ingest import USER_DATA_TABLES
from nts_feed.db.models import (
    Base,
    Artist,
    Episode,
    EpisodeInboxState,
    EpisodeGenre,
    EpisodeTrack,
    Genre,
    LikedEpisode,
    LikedTrack,
    ListeningSession,
    Show,
    Track,
)
from nts_feed.db.bootstrap import _ensure_episode_inbox_state_schema


def build_test_sessionmaker():
    engine = create_engine(
        'sqlite://',
        future=True,
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


class NextUpApiTest(unittest.TestCase):
    def setUp(self):
        self.sessionmaker = build_test_sessionmaker()
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.extensions['db_sessionmaker'] = self.sessionmaker
        self.app.extensions['Base'] = Base
        self.app.extensions['discover_cache'] = {}
        self.app.register_blueprint(discover_bp)
        self.client = self.app.test_client()

    def seed_library(self):
        with self.sessionmaker() as session:
            house = Genre(name='House')
            ambient = Genre(name='Ambient')
            bridge = Genre(name='Bridge')

            alpha_artist = Artist(name='Alpha Artist')
            bridge_artist = Artist(name='Bridge Artist')
            beta_artist = Artist(name='Beta Artist')

            alpha_track = Track(
                title_original='Night Pulse',
                title_norm='night pulse',
                canonical_artist_set_hash='alpha-hash',
                artists=[alpha_artist],
            )
            bridge_track = Track(
                title_original='Bridge Signal',
                title_norm='bridge signal',
                canonical_artist_set_hash='bridge-hash',
                artists=[bridge_artist],
            )
            beta_track = Track(
                title_original='Cloud Drift',
                title_norm='cloud drift',
                canonical_artist_set_hash='beta-hash',
                artists=[beta_artist],
            )

            show_alpha = Show(
                url='https://www.nts.live/shows/show-alpha',
                title='Show Alpha',
                description='House and club music',
                thumbnail='https://img.example.com/show-alpha.jpg',
            )
            show_bridge = Show(
                url='https://www.nts.live/shows/show-bridge',
                title='Show Bridge',
                description='Bridge between house and ambient',
                thumbnail='https://img.example.com/show-bridge.jpg',
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
            episode_bridge_set = Episode(
                show=show_bridge,
                url='https://www.nts.live/shows/show-bridge/episodes/bridge-set',
                title='Bridge Set',
                date='March 14, 2026',
                image_url='https://img.example.com/bridge-set.jpg',
                audio_url='https://audio.example.com/bridge-set.mp3',
            )
            episode_beta_ambient = Episode(
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
                bridge,
                alpha_artist,
                bridge_artist,
                beta_artist,
                alpha_track,
                bridge_track,
                beta_track,
                show_alpha,
                show_bridge,
                show_beta,
                episode_alpha_new,
                episode_alpha_old,
                episode_bridge_set,
                episode_beta_ambient,
            ])
            session.flush()

            session.add_all([
                EpisodeGenre(episode_id=episode_alpha_new.id, genre_id=house.id),
                EpisodeGenre(episode_id=episode_alpha_old.id, genre_id=house.id),
                EpisodeGenre(episode_id=episode_bridge_set.id, genre_id=house.id),
                EpisodeGenre(episode_id=episode_bridge_set.id, genre_id=ambient.id),
                EpisodeGenre(episode_id=episode_beta_ambient.id, genre_id=ambient.id),
                EpisodeTrack(episode_id=episode_alpha_new.id, track_id=alpha_track.id, track_order=1),
                EpisodeTrack(episode_id=episode_alpha_old.id, track_id=alpha_track.id, track_order=1),
                EpisodeTrack(episode_id=episode_bridge_set.id, track_id=bridge_track.id, track_order=1),
                EpisodeTrack(episode_id=episode_beta_ambient.id, track_id=beta_track.id, track_order=1),
                LikedTrack(
                    artist='Alpha Artist',
                    title='Night Pulse',
                    track_id=alpha_track.id,
                    episode_url=episode_alpha_new.url,
                    episode_title=episode_alpha_new.title,
                    show_title=show_alpha.title,
                ),
                LikedEpisode(
                    episode_url=episode_alpha_old.url,
                    episode_title=episode_alpha_old.title,
                    show_title=show_alpha.title,
                    show_url=show_alpha.url,
                    episode_date=episode_alpha_old.date,
                    image_url=episode_alpha_old.image_url,
                    episode_id=episode_alpha_old.id,
                ),
                ListeningSession(
                    session_token='alpha-new-session',
                    kind='episode',
                    player='nts_audio',
                    show_id=show_alpha.id,
                    episode_id=episode_alpha_new.id,
                    show_url=show_alpha.url,
                    episode_url=episode_alpha_new.url,
                    listened_seconds=180.0,
                    duration_seconds=3600.0,
                    max_position_seconds=240.0,
                    completion_ratio=0.05,
                    is_meaningful=True,
                    is_completed=False,
                    started_at=datetime.utcnow() - timedelta(minutes=12),
                    last_event_at=datetime.utcnow() - timedelta(minutes=2),
                ),
                ListeningSession(
                    session_token='alpha-old-session',
                    kind='episode',
                    player='nts_audio',
                    show_id=show_alpha.id,
                    episode_id=episode_alpha_old.id,
                    show_url=show_alpha.url,
                    episode_url=episode_alpha_old.url,
                    listened_seconds=3600.0,
                    duration_seconds=3600.0,
                    max_position_seconds=3600.0,
                    completion_ratio=1.0,
                    is_meaningful=True,
                    is_completed=True,
                    started_at=datetime.utcnow() - timedelta(hours=2),
                    last_event_at=datetime.utcnow() - timedelta(hours=1),
                    ended_at=datetime.utcnow() - timedelta(hours=1),
                ),
                EpisodeInboxState(
                    episode_id=episode_bridge_set.id,
                    episode_url=episode_bridge_set.url,
                    saved_for_later=True,
                ),
            ])
            dismissed_at = datetime.utcnow() - timedelta(days=1)
            session.add(
                EpisodeInboxState(
                    episode_id=episode_beta_ambient.id,
                    episode_url=episode_beta_ambient.url,
                    dismissed_at=dismissed_at,
                )
            )
            session.commit()

        inbox_state = {
            'saved_for_later': {
                'episode_id': episode_bridge_set.id,
                'episode_url': episode_bridge_set.url,
                'episode_title': episode_bridge_set.title,
                'show_title': show_bridge.title,
                'saved_for_later': True,
                'dismissed_at': None,
            },
            'dismissed': {
                'episode_id': episode_beta_ambient.id,
                'episode_url': episode_beta_ambient.url,
                'episode_title': episode_beta_ambient.title,
                'show_title': show_beta.title,
                'saved_for_later': False,
                'dismissed_at': dismissed_at,
            },
        }

        return {
            'subscribed': {
                'https://www.nts.live/shows/show-alpha': {'title': 'Show Alpha'},
                'https://www.nts.live/shows/show-bridge': {'title': 'Show Bridge'},
                'https://www.nts.live/shows/show-beta': {'title': 'Show Beta'},
            },
            'inbox_state': inbox_state,
        }

    def test_next_up_endpoint_returns_actionable_sections(self):
        fixture = self.seed_library()
        subscribed = fixture['subscribed']
        inbox_state = fixture['inbox_state']

        with self.sessionmaker() as session:
            seeded_rows = session.execute(text("""
                SELECT episode_url, episode_id, saved_for_later, snoozed_until, dismissed_at
                FROM episode_inbox_state
                ORDER BY episode_url
            """)).mappings().all()

        rows_by_url = {row['episode_url']: row for row in seeded_rows}
        self.assertEqual(
            rows_by_url[inbox_state['saved_for_later']['episode_url']],
            {
                'episode_url': inbox_state['saved_for_later']['episode_url'],
                'episode_id': inbox_state['saved_for_later']['episode_id'],
                'saved_for_later': 1,
                'snoozed_until': None,
                'dismissed_at': None,
            },
        )
        self.assertEqual(
            rows_by_url[inbox_state['dismissed']['episode_url']]['episode_id'],
            inbox_state['dismissed']['episode_id'],
        )
        self.assertEqual(rows_by_url[inbox_state['dismissed']['episode_url']]['saved_for_later'], 0)
        self.assertIsNone(rows_by_url[inbox_state['dismissed']['episode_url']]['snoozed_until'])
        self.assertIsNotNone(rows_by_url[inbox_state['dismissed']['episode_url']]['dismissed_at'])

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.get('/api/discover/next-up')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['sections']['continue_listening'][0]['episode_title'], 'Alpha New')
        self.assertEqual(payload['sections']['saved_for_later'][0]['episode_title'], 'Bridge Set')
        self.assertTrue(payload['sections']['curiosity_bridges'][0]['reason_label'].startswith('Bridge'))

    def test_next_up_state_survives_episode_id_remap_by_episode_url(self):
        fixture = self.seed_library()
        subscribed = fixture['subscribed']
        inbox_state = fixture['inbox_state']
        saved_for_later_seed = inbox_state['saved_for_later']

        with self.sessionmaker() as session:
            session.execute(
                text("""
                    UPDATE episode_inbox_state
                    SET saved_for_later = 0, dismissed_at = NULL
                    WHERE episode_url = :episode_url
                """),
                {'episode_url': saved_for_later_seed['episode_url']},
            )
            session.commit()

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            save_response = self.client.post(
                '/api/discover/next-up/state',
                json={
                    'episode_url': 'https://www.nts.live/shows/show-bridge/episodes/bridge-set',
                    'action': 'save',
                },
            )

        self.assertEqual(save_response.status_code, 200)

        with self.sessionmaker() as session:
            session.execute(text('DELETE FROM episodes WHERE url = :url'), {'url': 'https://www.nts.live/shows/show-bridge/episodes/bridge-set'})
            session.commit()

        with self.sessionmaker() as session:
            bridge = session.query(Show).filter_by(url='https://www.nts.live/shows/show-bridge').one()
            bridge_episode = Episode(
                show=bridge,
                url='https://www.nts.live/shows/show-bridge/episodes/bridge-set',
                title='Bridge Set',
                date='March 14, 2026',
                image_url='https://img.example.com/bridge-set.jpg',
                audio_url='https://audio.example.com/bridge-set.mp3',
            )
            session.add_all([bridge, bridge_episode])
            session.commit()

        with self.sessionmaker() as session:
            inbox_row = session.execute(
                text("""
                    SELECT episode_url, episode_id, saved_for_later, snoozed_until, dismissed_at
                    FROM episode_inbox_state
                    WHERE episode_url = :episode_url
                """),
                {'episode_url': saved_for_later_seed['episode_url']},
            ).mappings().one()

        self.assertEqual(inbox_row['episode_url'], saved_for_later_seed['episode_url'])
        self.assertEqual(inbox_row['episode_id'], saved_for_later_seed['episode_id'])
        self.assertEqual(inbox_row['saved_for_later'], 1)
        self.assertIsNone(inbox_row['snoozed_until'])
        self.assertIsNone(inbox_row['dismissed_at'])

        with self.sessionmaker() as session:
            state_values = session.execute(
                text("""
                    SELECT saved_for_later, dismissed_at
                    FROM episode_inbox_state
                    WHERE episode_url = :episode_url
                """),
                {'episode_url': saved_for_later_seed['episode_url']},
            ).mappings().one()

        self.assertEqual(state_values['saved_for_later'], 1)
        self.assertIsNone(state_values['dismissed_at'])

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.get('/api/discover/next-up')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        saved_for_later = payload['sections']['saved_for_later'][0]
        self.assertEqual(saved_for_later['episode_url'], bridge_episode.url)
        self.assertEqual(saved_for_later['episode_id'], bridge_episode.id)

    def test_next_up_recomputes_listening_signals_by_episode_url_after_remap(self):
        fixture = self.seed_library()
        subscribed = fixture['subscribed']

        with self.sessionmaker() as session:
            session.execute(text('DELETE FROM liked_tracks'))
            session.execute(text('DELETE FROM liked_episodes'))
            session.execute(
                text('DELETE FROM episode_inbox_state WHERE episode_url = :episode_url'),
                {'episode_url': 'https://www.nts.live/shows/show-bridge/episodes/bridge-set'},
            )
            session.execute(
                text('DELETE FROM episodes WHERE url = :url'),
                {'url': 'https://www.nts.live/shows/show-alpha/episodes/alpha-new'},
            )
            session.commit()

        with self.sessionmaker() as session:
            show_alpha = session.query(Show).filter_by(url='https://www.nts.live/shows/show-alpha').one()
            recreated = Episode(
                show=show_alpha,
                url='https://www.nts.live/shows/show-alpha/episodes/alpha-new',
                title='Alpha New',
                date='March 10, 2026',
                image_url='https://img.example.com/alpha-new.jpg',
                audio_url='https://audio.example.com/alpha-new.mp3',
            )
            session.add(recreated)
            session.commit()

        with patch('nts_feed.blueprints.api_discover.discover_impl.load_shows', return_value=subscribed):
            response = self.client.get('/api/discover/next-up')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(
            any(
                card['episode_title'] == 'Bridge Set' and card['reason_label'].startswith('Bridge')
                for card in payload['sections']['curiosity_bridges']
            )
        )

    def test_episode_inbox_state_schema_can_be_backfilled(self):
        engine = create_engine(
            'sqlite://',
            future=True,
            connect_args={'check_same_thread': False},
            poolclass=StaticPool,
        )

        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE episode_inbox_state (
                    episode_url TEXT PRIMARY KEY,
                    episode_id INTEGER
                )
            """))

        _ensure_episode_inbox_state_schema(engine)

        with engine.connect() as conn:
            columns = [row[1] for row in conn.execute(text("PRAGMA table_info(episode_inbox_state)")).all()]
            index_names = {
                row[0]
                for row in conn.execute(text("""
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'index' AND tbl_name = 'episode_inbox_state'
                """)).all()
            }

        self.assertTrue(
            {'episode_url', 'episode_id', 'saved_for_later', 'snoozed_until', 'dismissed_at', 'created_at', 'updated_at'}.issubset(columns)
        )
        self.assertIn('uq_episode_inbox_state_episode_url', index_names)
        self.assertIn('ix_episode_inbox_state_saved', index_names)
        self.assertIn('ix_episode_inbox_state_snoozed', index_names)

    def test_episode_inbox_state_schema_can_add_missing_episode_url_column(self):
        engine = create_engine(
            'sqlite://',
            future=True,
            connect_args={'check_same_thread': False},
            poolclass=StaticPool,
        )

        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE episode_inbox_state (
                    id INTEGER PRIMARY KEY,
                    episode_id INTEGER
                )
            """))

        _ensure_episode_inbox_state_schema(engine)

        with engine.connect() as conn:
            columns = [row[1] for row in conn.execute(text("PRAGMA table_info(episode_inbox_state)")).all()]
            index_names = {
                row[0]
                for row in conn.execute(text("""
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'index' AND tbl_name = 'episode_inbox_state'
                """)).all()
            }

        self.assertTrue(
            {'episode_url', 'episode_id', 'saved_for_later', 'snoozed_until', 'dismissed_at', 'created_at', 'updated_at'}.issubset(columns)
        )
        self.assertIn('uq_episode_inbox_state_episode_url', index_names)
        self.assertIn('ix_episode_inbox_state_saved', index_names)
        self.assertIn('ix_episode_inbox_state_snoozed', index_names)

    def test_user_data_tables_preserve_next_up_state_tables(self):
        self.assertTrue({'liked_episodes', 'listening_sessions', 'episode_inbox_state'}.issubset(USER_DATA_TABLES))
