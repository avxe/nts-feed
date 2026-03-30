import os
from flask import Blueprint, jsonify, request, redirect, send_file, current_app

bp = Blueprint('thumbs', __name__)


@bp.route('/thumbnail')
def cached_thumbnail():
    """Proxy and cache remote episode/show thumbnails locally.

    Usage: /thumbnail?url=<remote_image_url>
    
    Thumbnails are cached on disk and served with long cache headers.
    nginx proxy_cache caches the full response for 7 days, so after first request
    subsequent requests are served entirely by nginx without hitting Flask.
    """
    image_url = request.args.get('url', '').strip()
    if not image_url:
        return jsonify({'success': False, 'message': 'Missing url'}), 400
    try:
        image_cache_service = current_app.extensions.get('image_cache_service')
        cached_path = image_cache_service.get_or_fetch(image_url) if image_cache_service else None
        if not cached_path:
            # Fallback: redirect to original so UI doesn't break while cache warms
            return redirect(image_url, code=302)
        
        # Infer mimetype from extension
        ext = os.path.splitext(str(cached_path))[-1].lower()
        mimetype = 'image/jpeg'
        if ext == '.png':
            mimetype = 'image/png'
        elif ext == '.webp':
            mimetype = 'image/webp'
        elif ext == '.gif':
            mimetype = 'image/gif'
        
        # Use conditional=True to enable 304 responses
        # Cache-Control tells browsers to cache for 1 year
        # nginx proxy_cache (configured for 7 days) will cache the full response
        resp = send_file(str(cached_path), mimetype=mimetype, conditional=True)
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return resp
        
    except Exception as e:
        current_app.logger.warning(f'Thumbnail error for {image_url}: {e}')
        # Final fallback: redirect to original
        return redirect(image_url, code=302)
