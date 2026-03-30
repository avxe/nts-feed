from flask import Blueprint, Response, current_app, jsonify
import json
import queue

from .helpers import get_update_service
from ..scrape import load_shows

bp = Blueprint('updates', __name__)
update_service = None


@bp.route('/update_async', methods=['POST'])
def update_async():
    """Start background update with progress tracking for all shows."""
    try:
        update_service = get_update_service()
        update_service.cleanup_completed_updates()
        update_id = update_service.start_update(enable_auto_download=True)

        # Best-effort small cache warm for show thumbnails
        try:
            shows = load_shows()
            urls = [s.get('thumbnail') for s in shows.values() if isinstance(s, dict) and s.get('thumbnail')]
            image_cache_service = current_app.extensions.get('image_cache_service')
            if image_cache_service and urls:
                image_cache_service.prefetch_many(urls[:400], concurrency=8)
        except Exception:
            pass

        return jsonify({'success': True, 'update_id': update_id, 'message': 'Update started'})
    except Exception as e:
        current_app.logger.exception('Failed to start async update')
        return jsonify({'success': False, 'message': f'Failed to start update: {str(e)}'}), 500


@bp.route('/update_progress/<update_id>')
def update_progress(update_id):
    """SSE stream for update progress."""
    update_service = get_update_service()
    q = update_service.get_progress_queue(update_id)
    if not q:
        return Response(f"data: {json.dumps({'type':'error','message':'Update not found'})}\n\n", mimetype='text/event-stream')

    def generate():
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    if msg is None:
                        final = update_service.get_progress(update_id)
                        if final:
                            yield f"data: {json.dumps({'type':'final','status': final.status,'total_new_episodes': final.total_new_episodes,'total_auto_downloaded': final.total_auto_downloaded,'elapsed_time': final.elapsed_time})}\n\n"
                        break
                    yield f"data: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type':'heartbeat'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')
