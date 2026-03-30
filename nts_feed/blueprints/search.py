from flask import Blueprint, jsonify, request, current_app

bp = Blueprint('search', __name__)


@bp.route('/download_track', methods=['POST'])
def download_track():
    data = request.get_json()
    artist = data.get('artist')
    title = data.get('title')

    if not artist or not title:
        return jsonify({'success': False, 'message': 'Missing artist or title'}), 400

    try:
        current_app.logger.info(f"Searching for track: {artist} - {title}")
        discogs_service = current_app.extensions.get('discogs_service')
        if not discogs_service:
            return jsonify({'success': False, 'message': 'Discogs service unavailable'}), 503
        result = discogs_service.find_track_url(artist, title)
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"Error creating Discogs search URL: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/search_youtube', methods=['POST'])
def search_youtube():
    data = request.get_json()
    artist = data.get('artist')
    title = data.get('title')

    if not artist or not title:
        return jsonify({'success': False, 'message': 'Missing artist or title'}), 400

    try:
        current_app.logger.info(f"Creating YouTube search for: {artist} - {title}")
        youtube_service = current_app.extensions.get('youtube_service')
        db_sessionmaker = current_app.extensions.get('db_sessionmaker')
        if not youtube_service:
            return jsonify({'success': False, 'message': 'YouTube service unavailable'}), 503
        result = youtube_service.find_best_video(artist, title, db_sessionmaker=db_sessionmaker)
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"Error creating YouTube search URL: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

