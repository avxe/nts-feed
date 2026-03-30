"""Likes and playlists API routes.

Provides endpoints for track likes, episode likes, and user playlist
management, extracted from the monolithic ``app.py``.
"""

from flask import Blueprint, current_app, jsonify, request

from ..validation import (
    ValidationError,
    validate_track_data,
    validate_episode_like_data,
    validate_playlist_data,
    validate_list,
    validate_id_list,
    MAX_TRACKS_BATCH,
    MAX_IDS_BATCH,
    MAX_BATCH_SIZE,
)
from .helpers import db_available, get_db

bp = Blueprint('api_likes', __name__)


# ==================== Track Likes API ====================


@bp.route('/api/likes', methods=['GET'])
def api_list_likes():
    """List liked tracks with optional pagination.

    Query params:
    - page: Page number (default: 1, or 0 for all results)
    - per_page: Items per page (default: 100, max: 500)
    - all: If '1' or 'true', return all results without pagination
    """
    if not db_available():
        return jsonify({'success': True, 'likes': [], 'total': 0})
    try:
        from ..db.models import LikedTrack
        from sqlalchemy import func

        # Parse pagination params
        return_all = request.args.get('all', '').lower() in ('1', 'true')
        page = max(int(request.args.get('page', 1)), 1) if not return_all else 0
        per_page = min(max(int(request.args.get('per_page', 100)), 1), 500)

        with get_db()() as session:
            # Get total count
            total = session.query(func.count(LikedTrack.id)).scalar() or 0

            # Build query
            query = session.query(LikedTrack).order_by(LikedTrack.created_at.desc())

            # Apply pagination unless all requested
            if not return_all and page > 0:
                query = query.offset((page - 1) * per_page).limit(per_page)

            rows = query.all()
            likes = []
            for lt in rows:
                # Extract show_url from episode_url
                # Format: https://www.nts.live/shows/{slug}/episodes/{alias}
                show_url = None
                if lt.episode_url and '/episodes/' in lt.episode_url:
                    show_url = lt.episode_url.split('/episodes/')[0]
                likes.append({
                    'id': lt.id,
                    'artist': lt.artist,
                    'title': lt.title,
                    'track_id': lt.track_id,
                    'episode_url': lt.episode_url,
                    'episode_title': lt.episode_title,
                    'show_title': lt.show_title,
                    'show_url': show_url,
                    'created_at': lt.created_at.isoformat() if lt.created_at else None,
                })

            response = {'success': True, 'likes': likes, 'total': total}
            if not return_all and page > 0:
                response['page'] = page
                response['per_page'] = per_page
                response['has_more'] = (page * per_page) < total
            return jsonify(response)
    except Exception as e:
        current_app.logger.exception('List likes failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/likes', methods=['POST'])
def api_add_like():
    """Like a track."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import LikedTrack, Track
        data = request.get_json(silent=True) or {}

        # Validate input
        try:
            validated = validate_track_data(data)
            artist = validated['artist']
            title = validated['title']
            episode_url = validated['episode_url']
            episode_title = validated['episode_title']
            show_title = validated['show_title']
        except ValidationError as e:
            return jsonify({'success': False, 'message': e.message, 'field': e.field}), 400

        with get_db()() as session:
            # Check if already liked
            existing = session.query(LikedTrack).filter(
                LikedTrack.artist == artist,
                LikedTrack.title == title
            ).first()
            if existing:
                return jsonify({'success': True, 'id': existing.id, 'already_liked': True})

            # Try to find matching track in DB
            track_id = None
            try:
                from sqlalchemy import or_
                from ..db.models import Artist
                track = (
                    session.query(Track)
                    .outerjoin(Track.artists)
                    .filter(
                        or_(
                            Track.title_norm.ilike(f"%{title.lower()}%"),
                            Track.title_original.ilike(f"%{title}%"),
                        ),
                        Artist.name.ilike(f"%{artist}%")
                    )
                    .first()
                )
                if track:
                    track_id = track.id
            except Exception:
                pass

            lt = LikedTrack(
                artist=artist,
                title=title,
                track_id=track_id,
                episode_url=episode_url,
                episode_title=episode_title,
                show_title=show_title,
            )
            session.add(lt)
            session.commit()
            return jsonify({'success': True, 'id': lt.id})
    except Exception as e:
        current_app.logger.exception('Add like failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/likes/<int:like_id>', methods=['DELETE'])
def api_remove_like(like_id: int):
    """Unlike a track."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import LikedTrack
        with get_db()() as session:
            lt = session.get(LikedTrack, like_id)
            if not lt:
                return jsonify({'success': False, 'message': 'Not found'}), 404
            session.delete(lt)
            session.commit()
            return jsonify({'success': True})
    except Exception as e:
        current_app.logger.exception('Remove like failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/likes/check', methods=['POST'])
def api_check_likes():
    """Check if tracks are liked (batch). Body: { tracks: [{artist, title}, ...] }

    Limited to MAX_TRACKS_BATCH items per request to prevent resource exhaustion.
    """
    if not db_available():
        return jsonify({'success': True, 'liked': {}})
    try:
        from ..db.models import LikedTrack
        data = request.get_json(silent=True) or {}

        # Validate batch size
        try:
            tracks = validate_list(data.get('tracks'), 'tracks', max_length=MAX_TRACKS_BATCH)
        except ValidationError as e:
            return jsonify({'success': False, 'message': e.message, 'field': e.field}), 400

        if not tracks:
            return jsonify({'success': True, 'liked': {}})

        with get_db()() as session:
            from sqlalchemy import func, or_, and_

            # Build list of (artist, title) pairs to check
            track_pairs = []
            for t in tracks:
                a = (t.get('artist') or '').strip().lower()
                n = (t.get('title') or '').strip().lower()
                if a and n:
                    track_pairs.append((a, n))

            if not track_pairs:
                return jsonify({'success': True, 'liked': {}})

            # Query only the specific tracks we're checking (avoid loading all likes)
            # Use case-insensitive matching with func.lower()
            conditions = [
                and_(
                    func.lower(LikedTrack.artist) == pair[0],
                    func.lower(LikedTrack.title) == pair[1]
                )
                for pair in track_pairs
            ]

            matching_likes = session.query(
                LikedTrack.id, LikedTrack.artist, LikedTrack.title
            ).filter(or_(*conditions)).all()

            # Build lookup from results
            likes_set = {
                (lt.artist.lower(), lt.title.lower()): lt.id
                for lt in matching_likes
            }

            result = {}
            for t in tracks:
                a = (t.get('artist') or '').strip().lower()
                n = (t.get('title') or '').strip().lower()
                key = f"{a}|||{n}"
                like_id = likes_set.get((a, n))
                result[key] = {'liked': like_id is not None, 'id': like_id}

            return jsonify({'success': True, 'liked': result})
    except Exception as e:
        current_app.logger.exception('Check likes failed')
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== Episode Likes API ====================


@bp.route('/api/episodes/likes', methods=['GET'])
def api_list_episode_likes():
    """List liked episodes with optional pagination.

    Query params:
    - page: Page number (default: 1, or 0 for all results)
    - per_page: Items per page (default: 100, max: 500)
    - all: If '1' or 'true', return all results without pagination
    """
    if not db_available():
        return jsonify({'success': True, 'episodes': [], 'total': 0})
    try:
        from ..db.models import LikedEpisode
        from sqlalchemy import func

        # Parse pagination params
        return_all = request.args.get('all', '').lower() in ('1', 'true')
        page = max(int(request.args.get('page', 1)), 1) if not return_all else 0
        per_page = min(max(int(request.args.get('per_page', 100)), 1), 500)

        with get_db()() as session:
            # Get total count
            total = session.query(func.count(LikedEpisode.id)).scalar() or 0

            # Build query
            query = session.query(LikedEpisode).order_by(LikedEpisode.created_at.desc())

            # Apply pagination unless all requested
            if not return_all and page > 0:
                query = query.offset((page - 1) * per_page).limit(per_page)

            rows = query.all()
            episodes = [{
                'id': le.id,
                'episode_url': le.episode_url,
                'episode_title': le.episode_title,
                'show_title': le.show_title,
                'show_url': le.show_url,
                'episode_date': le.episode_date,
                'image_url': le.image_url,
                'created_at': le.created_at.isoformat() if le.created_at else None,
            } for le in rows]

            response = {'success': True, 'episodes': episodes, 'total': total}
            if not return_all and page > 0:
                response['page'] = page
                response['per_page'] = per_page
                response['has_more'] = (page * per_page) < total
            return jsonify(response)
    except Exception as e:
        current_app.logger.exception('List episode likes failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/episodes/likes', methods=['POST'])
def api_add_episode_like():
    """Like an episode."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import LikedEpisode, Episode
        data = request.get_json(silent=True) or {}

        # Validate input
        try:
            validated = validate_episode_like_data(data)
            episode_url = validated['episode_url']
            episode_title = validated['episode_title']
            show_title = validated['show_title']
            show_url = validated['show_url']
            episode_date = validated['episode_date']
            image_url = validated['image_url']
        except ValidationError as e:
            return jsonify({'success': False, 'message': e.message, 'field': e.field}), 400

        with get_db()() as session:
            # Check if already liked
            existing = session.query(LikedEpisode).filter(LikedEpisode.episode_url == episode_url).first()
            if existing:
                return jsonify({'success': True, 'message': 'Already liked', 'like_id': existing.id})

            # Try to find matching episode in DB
            episode_id = None
            ep = session.query(Episode).filter(Episode.url == episode_url).first()
            if ep:
                episode_id = ep.id

            le = LikedEpisode(
                episode_url=episode_url,
                episode_title=episode_title,
                show_title=show_title,
                show_url=show_url,
                episode_date=episode_date,
                image_url=image_url,
                episode_id=episode_id,
            )
            session.add(le)
            session.commit()
            return jsonify({'success': True, 'like_id': le.id})
    except Exception as e:
        current_app.logger.exception('Add episode like failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/episodes/likes/<int:like_id>', methods=['DELETE'])
def api_remove_episode_like(like_id: int):
    """Unlike an episode."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import LikedEpisode
        with get_db()() as session:
            le = session.query(LikedEpisode).filter(LikedEpisode.id == like_id).first()
            if le:
                session.delete(le)
                session.commit()
            return jsonify({'success': True})
    except Exception as e:
        current_app.logger.exception('Remove episode like failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/episodes/likes/check', methods=['POST'])
def api_check_episode_likes():
    """Check if episodes are liked (batch). Body: { episode_urls: [url1, url2, ...] }

    Limited to MAX_BATCH_SIZE items per request to prevent resource exhaustion.
    """
    if not db_available():
        return jsonify({'success': True, 'liked': {}})
    try:
        from ..db.models import LikedEpisode
        data = request.get_json(silent=True) or {}

        # Validate batch size
        try:
            episode_urls = validate_list(data.get('episode_urls'), 'episode_urls', max_length=MAX_BATCH_SIZE)
        except ValidationError as e:
            return jsonify({'success': False, 'message': e.message, 'field': e.field}), 400

        if not episode_urls:
            return jsonify({'success': True, 'liked': {}})

        with get_db()() as session:
            # Query only the specific URLs we're checking (avoid loading all likes)
            matching_likes = session.query(
                LikedEpisode.episode_url, LikedEpisode.id
            ).filter(LikedEpisode.episode_url.in_(episode_urls)).all()

            likes_dict = {le.episode_url: le.id for le in matching_likes}

            result = {}
            for url in episode_urls:
                like_id = likes_dict.get(url)
                result[url] = {'liked': like_id is not None, 'id': like_id}

            return jsonify({'success': True, 'liked': result})
    except Exception as e:
        current_app.logger.exception('Check episode likes failed')
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== User Playlists API ====================


@bp.route('/api/user_playlists', methods=['GET'])
def api_list_user_playlists():
    """List all user playlists."""
    if not db_available():
        return jsonify({'success': True, 'playlists': []})
    try:
        from ..db.models import UserPlaylist, UserPlaylistTrack
        with get_db()() as session:
            rows = session.query(UserPlaylist).order_by(UserPlaylist.updated_at.desc()).all()
            playlists = []
            for p in rows:
                count = session.query(UserPlaylistTrack).filter(UserPlaylistTrack.playlist_id == p.id).count()
                playlists.append({
                    'id': p.id,
                    'name': p.name,
                    'description': p.description,
                    'track_count': count,
                    'created_at': p.created_at.isoformat() if p.created_at else None,
                    'updated_at': p.updated_at.isoformat() if p.updated_at else None,
                })
            return jsonify({'success': True, 'playlists': playlists})
    except Exception as e:
        current_app.logger.exception('List playlists failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/user_playlists', methods=['POST'])
def api_create_user_playlist():
    """Create a new user playlist."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import UserPlaylist
        data = request.get_json(silent=True) or {}

        # Validate input
        try:
            validated = validate_playlist_data(data)
            name = validated['name']
            description = validated['description']
        except ValidationError as e:
            return jsonify({'success': False, 'message': e.message, 'field': e.field}), 400

        with get_db()() as session:
            p = UserPlaylist(name=name, description=description)
            session.add(p)
            session.commit()
            return jsonify({'success': True, 'id': p.id, 'name': p.name})
    except Exception as e:
        current_app.logger.exception('Create playlist failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/user_playlists/<int:playlist_id>', methods=['GET'])
def api_get_user_playlist(playlist_id: int):
    """Get a playlist with its tracks."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import UserPlaylist, UserPlaylistTrack, LikedTrack
        with get_db()() as session:
            p = session.get(UserPlaylist, playlist_id)
            if not p:
                return jsonify({'success': False, 'message': 'Not found'}), 404

            tracks_rows = (
                session.query(UserPlaylistTrack, LikedTrack)
                .join(LikedTrack, LikedTrack.id == UserPlaylistTrack.liked_track_id)
                .filter(UserPlaylistTrack.playlist_id == p.id)
                .order_by(UserPlaylistTrack.position.asc())
                .all()
            )
            tracks = []
            for pt, lt in tracks_rows:
                # Extract show_url from episode_url
                show_url = None
                if lt.episode_url and '/episodes/' in lt.episode_url:
                    show_url = lt.episode_url.split('/episodes/')[0]
                tracks.append({
                    'playlist_track_id': pt.id,
                    'liked_track_id': lt.id,
                    'artist': lt.artist,
                    'title': lt.title,
                    'episode_url': lt.episode_url,
                    'episode_title': lt.episode_title,
                    'show_title': lt.show_title,
                    'show_url': show_url,
                    'position': pt.position,
                    'added_at': pt.added_at.isoformat() if pt.added_at else None,
                })

            return jsonify({
                'success': True,
                'playlist': {
                    'id': p.id,
                    'name': p.name,
                    'description': p.description,
                    'created_at': p.created_at.isoformat() if p.created_at else None,
                    'updated_at': p.updated_at.isoformat() if p.updated_at else None,
                    'tracks': tracks,
                }
            })
    except Exception as e:
        current_app.logger.exception('Get playlist failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/user_playlists/<int:playlist_id>', methods=['PUT'])
def api_update_user_playlist(playlist_id: int):
    """Update a playlist (name/description)."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import UserPlaylist
        data = request.get_json(silent=True) or {}
        with get_db()() as session:
            p = session.get(UserPlaylist, playlist_id)
            if not p:
                return jsonify({'success': False, 'message': 'Not found'}), 404

            if 'name' in data:
                p.name = (data['name'] or '').strip() or p.name
            if 'description' in data:
                p.description = (data['description'] or '').strip() or None

            session.commit()
            return jsonify({'success': True, 'name': p.name, 'description': p.description})
    except Exception as e:
        current_app.logger.exception('Update playlist failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/user_playlists/<int:playlist_id>', methods=['DELETE'])
def api_delete_user_playlist(playlist_id: int):
    """Delete a playlist."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import UserPlaylist
        with get_db()() as session:
            p = session.get(UserPlaylist, playlist_id)
            if not p:
                return jsonify({'success': False, 'message': 'Not found'}), 404
            session.delete(p)
            session.commit()
            return jsonify({'success': True})
    except Exception as e:
        current_app.logger.exception('Delete playlist failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/user_playlists/<int:playlist_id>/tracks', methods=['POST'])
def api_add_track_to_playlist(playlist_id: int):
    """Add a liked track to a playlist."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import UserPlaylist, UserPlaylistTrack, LikedTrack
        from sqlalchemy import func
        data = request.get_json(silent=True) or {}
        liked_track_id = data.get('liked_track_id')
        if not liked_track_id:
            return jsonify({'success': False, 'message': 'liked_track_id is required'}), 400

        with get_db()() as session:
            p = session.get(UserPlaylist, playlist_id)
            if not p:
                return jsonify({'success': False, 'message': 'Playlist not found'}), 404

            lt = session.get(LikedTrack, liked_track_id)
            if not lt:
                return jsonify({'success': False, 'message': 'Liked track not found'}), 404

            # Check if already in playlist
            existing = session.query(UserPlaylistTrack).filter(
                UserPlaylistTrack.playlist_id == playlist_id,
                UserPlaylistTrack.liked_track_id == liked_track_id
            ).first()
            if existing:
                return jsonify({'success': True, 'already_exists': True, 'id': existing.id})

            # Get max position
            max_pos = session.query(func.max(UserPlaylistTrack.position)).filter(
                UserPlaylistTrack.playlist_id == playlist_id
            ).scalar() or 0

            pt = UserPlaylistTrack(
                playlist_id=playlist_id,
                liked_track_id=liked_track_id,
                position=max_pos + 1
            )
            session.add(pt)
            session.commit()
            return jsonify({'success': True, 'id': pt.id})
    except Exception as e:
        current_app.logger.exception('Add track to playlist failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/user_playlists/<int:playlist_id>/tracks/<int:playlist_track_id>', methods=['DELETE'])
def api_remove_track_from_playlist(playlist_id: int, playlist_track_id: int):
    """Remove a track from a playlist."""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import UserPlaylistTrack
        with get_db()() as session:
            pt = session.query(UserPlaylistTrack).filter(
                UserPlaylistTrack.id == playlist_track_id,
                UserPlaylistTrack.playlist_id == playlist_id
            ).first()
            if not pt:
                return jsonify({'success': False, 'message': 'Not found'}), 404
            session.delete(pt)
            session.commit()
            return jsonify({'success': True})
    except Exception as e:
        current_app.logger.exception('Remove track from playlist failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/user_playlists/<int:playlist_id>/reorder', methods=['POST'])
def api_reorder_playlist_tracks(playlist_id: int):
    """Reorder tracks in a playlist. Body: { track_ids: [id1, id2, ...] }"""
    if not db_available():
        return jsonify({'success': False, 'message': 'Database not available'}), 503
    try:
        from ..db.models import UserPlaylistTrack
        data = request.get_json(silent=True) or {}

        # Validate track_ids list
        try:
            track_ids = validate_id_list(data.get('track_ids'), 'track_ids', max_length=MAX_IDS_BATCH)
        except ValidationError as e:
            return jsonify({'success': False, 'message': e.message, 'field': e.field}), 400

        if not track_ids:
            return jsonify({'success': False, 'message': 'track_ids is required'}), 400

        with get_db()() as session:
            for idx, pt_id in enumerate(track_ids):
                pt = session.query(UserPlaylistTrack).filter(
                    UserPlaylistTrack.id == pt_id,
                    UserPlaylistTrack.playlist_id == playlist_id
                ).first()
                if pt:
                    pt.position = idx
            session.commit()
            return jsonify({'success': True})
    except Exception as e:
        current_app.logger.exception('Reorder playlist tracks failed')
        return jsonify({'success': False, 'message': str(e)}), 500
