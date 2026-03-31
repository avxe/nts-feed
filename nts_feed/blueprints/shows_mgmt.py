"""Show management routes - subscribe, update, toggle, mark read, delete."""

import json
import os
import queue
import shutil
import time
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, redirect, request, url_for

from ..downloader import download, download_manager
from ..ext.tasks import get_executor
from ..runtime_paths import downloads_dir, episodes_dir
from ..scrape import (
    check_new_episodes,
    load_episodes,
    load_shows,
    save_episodes,
    save_shows,
    scrape_nts_show_progress,
    slugify,
    backfill_tracklists_all,
    backfill_tracklists_for_show,
)
from ..validation import ValidationError, validate_nts_show_url
from .helpers import get_image_cache

bp = Blueprint('shows_mgmt', __name__)

DOWNLOAD_DIR = downloads_dir()
DOWNLOAD_DIR.mkdir(exist_ok=True)

SYNC_POLL_INTERVAL_SECONDS = 0.25
SYNC_POLL_TIMEOUT_SECONDS = 120


def _queue_sync_status(progress_queue, *, status, sync_job_id=None, phase=None, db_stats=None, error=None, message=None):
    sync_payload = {'status': status}
    if sync_job_id:
        sync_payload['sync_job_id'] = sync_job_id
    if phase:
        sync_payload['phase'] = phase
    if db_stats is not None:
        sync_payload['db_stats'] = db_stats
    if error:
        sync_payload['error'] = error

    progress_queue.put({
        'type': 'sync_status',
        'sync': sync_payload,
        'message': message,
    })


def _wait_for_sync_completion(progress_queue, sync_job_id):
    from .api_db import get_sync_job_info

    deadline = time.monotonic() + SYNC_POLL_TIMEOUT_SECONDS
    last_signature = None

    while time.monotonic() < deadline:
        sync_info = get_sync_job_info(sync_job_id)
        if not sync_info:
            _queue_sync_status(
                progress_queue,
                status='failed',
                sync_job_id=sync_job_id,
                error='Sync job disappeared before completion',
                message='Database sync status was lost before it finished.',
            )
            return False

        signature = (
            sync_info.get('status'),
            sync_info.get('phase'),
            sync_info.get('error'),
            repr(sync_info.get('db_stats')),
        )
        if signature != last_signature:
            _queue_sync_status(
                progress_queue,
                status=sync_info.get('status', 'unknown'),
                sync_job_id=sync_job_id,
                phase=sync_info.get('phase'),
                db_stats=sync_info.get('db_stats'),
                error=sync_info.get('error'),
                message='Syncing database...' if sync_info.get('status') == 'running' else None,
            )
            last_signature = signature

        status = sync_info.get('status')
        if status == 'completed':
            return True
        if status == 'failed':
            return False

        time.sleep(SYNC_POLL_INTERVAL_SECONDS)

    _queue_sync_status(
        progress_queue,
        status='timed_out',
        sync_job_id=sync_job_id,
        error='Timed out waiting for database sync',
        message='Database sync is still running. Try refreshing Discover in a moment.',
    )
    return False


@bp.route('/subscribe_async', methods=['POST'])
def subscribe_async():
    """Start background subscription for a show and stream progress via SSE."""
    json_data = request.get_json(silent=True) or {}
    url = request.form.get('url') or json_data.get('url')
    if not url:
        return jsonify({'success': False, 'message': 'No URL provided'}), 400

    try:
        url = validate_nts_show_url(url)
    except ValidationError as e:
        return jsonify({'success': False, 'message': e.message, 'field': e.field}), 400

    defer_tracklists_raw = (
        request.form.get('defer_tracklists')
        or request.form.get('fast')
        or json_data.get('defer_tracklists')
        or json_data.get('fast')
    )
    defer_tracklists = str(defer_tracklists_raw).lower() in ('true', '1', 'yes')

    subscribe_id = datetime.now().strftime('%Y%m%d_%H%M%S%f')
    progress_queue = queue.Queue()

    if not hasattr(current_app._get_current_object(), 'subscribe_queues'):
        current_app._get_current_object().subscribe_queues = {}
    current_app._get_current_object().subscribe_queues[subscribe_id] = progress_queue

    image_cache_service = get_image_cache()

    def worker():
        try:
            shows = load_shows()
            if url in shows:
                progress_queue.put({'type': 'completed', 'already_exists': True})
                progress_queue.put(None)
                return

            def on_progress(event):
                event = dict(event)
                if event.get('type') == 'completed':
                    event['type'] = 'scrape_completed'
                event['subscribe_id'] = subscribe_id
                progress_queue.put(event)

            show_data = scrape_nts_show_progress(
                url, on_progress=on_progress, defer_tracklists=defer_tracklists,
            )
            if not show_data:
                progress_queue.put({'type': 'error', 'message': 'Failed to scrape show data'})
                progress_queue.put(None)
                return

            for episode in show_data['episodes']:
                episode['is_new'] = False

            shows[url] = {
                'title': show_data.get('title', ''),
                'description': show_data.get('description', ''),
                'thumbnail': show_data.get('thumbnail') or (
                    show_data['episodes'][0].get('image_url', '') if show_data['episodes'] else ''
                ),
                'total_episodes': len(show_data['episodes']),
                'new_episodes': 0,
                'last_updated': datetime.now().isoformat(),
                'first_seen': datetime.now().isoformat(),
                'auto_download': False,
            }
            save_shows(shows)

            show_slug = slugify(url)
            save_episodes(show_slug, {'episodes': show_data['episodes']})
            progress_queue.put({'type': 'saved', 'total': len(show_data['episodes'])})

            # Warm thumbnail cache
            try:
                urls_to_prefetch = []
                show_thumb = shows[url].get('thumbnail')
                if show_thumb:
                    urls_to_prefetch.append(show_thumb)
                for ep in show_data.get('episodes', [])[:200]:
                    thumb = ep.get('image_url')
                    if thumb:
                        urls_to_prefetch.append(thumb)
                if urls_to_prefetch and image_cache_service:
                    image_cache_service.prefetch_many(urls_to_prefetch, concurrency=8)
            except Exception:
                pass

            # Auto-trigger sync after adding a new show
            try:
                from .api_db import get_running_sync_job, start_sync_job

                existing_sync = get_running_sync_job()
                if existing_sync:
                    _queue_sync_status(
                        progress_queue,
                        status='started',
                        sync_job_id=existing_sync,
                        message='Waiting for in-progress database sync...',
                    )
                    sync_job_id = existing_sync
                else:
                    sync_job_id = start_sync_job(trigger='auto_subscribe')
                    _queue_sync_status(
                        progress_queue,
                        status='started',
                        sync_job_id=sync_job_id,
                        message='Syncing database...',
                    )

                sync_completed = _wait_for_sync_completion(progress_queue, sync_job_id)
                if not sync_completed:
                    progress_queue.put({
                        'type': 'error',
                        'message': 'Show was saved, but the database sync did not finish. Discover, stats, and admin may update after the next successful sync.',
                    })
                    return
            except Exception as sync_err:
                _queue_sync_status(
                    progress_queue,
                    status='failed',
                    error=str(sync_err),
                    message=f'Sync failed: {sync_err}',
                )
                progress_queue.put({
                    'type': 'error',
                    'message': 'Show was saved, but the database sync failed. Discover, stats, and admin may update after the next successful sync.',
                })
                return

            progress_queue.put({'type': 'completed', 'total': len(show_data['episodes'])})

        except Exception as e:
            progress_queue.put({'type': 'error', 'message': str(e)})
        finally:
            progress_queue.put(None)

    get_executor().submit(worker)
    return jsonify({'success': True, 'subscribe_id': subscribe_id})


@bp.route('/subscribe_progress/<subscribe_id>')
def subscribe_progress(subscribe_id):
    """SSE stream for subscribe progress."""
    app = current_app._get_current_object()
    if not hasattr(app, 'subscribe_queues'):
        app.subscribe_queues = {}
    q = app.subscribe_queues.get(subscribe_id)

    def generate():
        try:
            while True:
                try:
                    msg = q.get(timeout=60)
                    if msg is None:
                        break
                    yield f"data: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        finally:
            app.subscribe_queues.pop(subscribe_id, None)

    return Response(generate(), mimetype='text/event-stream')


@bp.route('/update', methods=['POST'])
def update():
    """Legacy synchronous update for all shows."""
    try:
        shows = load_shows()
        total_new_episodes = 0
        auto_downloaded_episodes = 0
        for url, show_data in shows.items():
            show_slug = slugify(url)
            episodes_data = load_episodes(show_slug)
            new_episodes = check_new_episodes(url, episodes_data.get('episodes', []))
            if new_episodes:
                existing_urls = {
                    ep.get('url') or ep.get('audio_url')
                    for ep in episodes_data.get('episodes', [])
                    if ep.get('url') or ep.get('audio_url')
                }
                new_unique = [
                    ep for ep in new_episodes
                    if (ep.get('url') or ep.get('audio_url')) not in existing_urls
                ]
                total_new_episodes += len(new_unique)
                episodes_data['episodes'] = new_unique + episodes_data.get('episodes', [])
                save_episodes(show_slug, episodes_data)
                show_data.update({
                    'total_episodes': len(episodes_data['episodes']),
                    'new_episodes': len(new_unique),
                    'last_updated': datetime.now().isoformat(),
                })
        if total_new_episodes > 0:
            save_shows(shows)
        return jsonify({
            'success': True,
            'new_episodes': total_new_episodes,
            'auto_downloaded': auto_downloaded_episodes,
        })
    except Exception as e:
        current_app.logger.exception('Update failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/update_show/<path:url>', methods=['POST'])
def update_show(url):
    """Update a single show's episodes and optionally auto-download new ones."""
    try:
        shows = load_shows()
        if url not in shows:
            return jsonify({'success': False, 'message': 'Show not found'})

        show_data = shows[url]
        show_slug = slugify(url)
        episodes_data = load_episodes(show_slug)
        image_cache_service = get_image_cache()

        new_episodes = check_new_episodes(url, episodes_data['episodes'])
        if new_episodes:
            existing_urls = {
                ep.get('url') or ep.get('audio_url')
                for ep in episodes_data['episodes']
                if ep.get('url') or ep.get('audio_url')
            }
            new_unique = [
                ep for ep in new_episodes
                if (ep.get('url') or ep.get('audio_url')) not in existing_urls
            ]
            episodes_data['episodes'] = new_unique + episodes_data['episodes']
            save_episodes(show_slug, episodes_data)

            show_data.update({
                'total_episodes': len(episodes_data['episodes']),
                'new_episodes': len(new_unique),
                'last_updated': datetime.now().isoformat(),
            })

            auto_downloaded_episodes = 0
            if show_data.get('auto_download', False):
                for episode in new_unique:
                    try:
                        download_id = datetime.now().strftime('%Y%m%d_%H%M%S')
                        episode_path = DOWNLOAD_DIR / download_id
                        episode_path.mkdir(exist_ok=True)
                        download(
                            episode['audio_url'], quiet=True,
                            save_dir=str(episode_path), download_id=download_id,
                        )
                        try:
                            for f in os.listdir(episode_path):
                                if f.endswith('.m4a'):
                                    shutil.move(str(episode_path / f), str(DOWNLOAD_DIR / f))
                        except Exception:
                            pass
                        shutil.rmtree(episode_path, ignore_errors=True)
                        download_manager.remove_cancel_event(download_id)
                        auto_downloaded_episodes += 1
                    except Exception:
                        download_manager.remove_cancel_event(download_id)

            save_shows(shows)

            try:
                urls_to_prefetch = []
                show_thumb = show_data.get('thumbnail')
                if show_thumb:
                    urls_to_prefetch.append(show_thumb)
                for ep in new_unique:
                    thumb = ep.get('image_url')
                    if thumb:
                        urls_to_prefetch.append(thumb)
                if urls_to_prefetch and image_cache_service:
                    image_cache_service.prefetch_many(urls_to_prefetch, concurrency=6)
            except Exception:
                pass

            return jsonify({
                'success': True,
                'new_episodes': len(new_unique),
                'auto_downloaded': auto_downloaded_episodes,
            })
        return jsonify({'success': True, 'new_episodes': 0, 'auto_downloaded': 0})
    except Exception as e:
        current_app.logger.exception(f'Update failed for show: {url}')
        return jsonify({'success': False, 'message': str(e)})


@bp.route('/toggle_auto_download/<path:show_id>', methods=['POST'])
def toggle_auto_download(show_id):
    try:
        shows = load_shows()
        show_data = shows.get(show_id, {})
        new_status = not show_data.get('auto_download', False)
        show_data['auto_download'] = new_status
        save_shows(shows)
        return jsonify({'success': True, 'auto_download': new_status})
    except Exception as e:
        current_app.logger.error(f'Error toggling auto-download: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/mark_read/<path:url>', methods=['POST'])
def mark_episodes_read(url):
    shows = load_shows()
    if url not in shows:
        return jsonify({'success': False, 'message': 'Show not found'})
    show_slug = slugify(url)
    episodes_data = load_episodes(show_slug)
    for episode in episodes_data.get('episodes', []):
        episode['is_new'] = False
    save_episodes(show_slug, episodes_data)
    shows[url]['new_episodes'] = 0
    save_shows(shows)
    return jsonify({'success': True})


@bp.route('/delete/<path:url>', methods=['POST'])
def delete_show(url):
    shows = load_shows()
    if url in shows:
        show_slug = slugify(url)
        episode_file = str(episodes_dir() / f'{show_slug}.json')
        try:
            if os.path.exists(episode_file):
                os.remove(episode_file)
        except Exception:
            pass
        del shows[url]
        save_shows(shows)
    return redirect(url_for('pages.index'))


@bp.route('/api/backfill_tracklists', methods=['POST'])
def api_backfill_tracklists():
    """Backfill stored episodes' tracklists."""
    try:
        req = request.get_json(silent=True) or {}
        show_url = (req.get('show_url') or '').strip()
        if show_url:
            count = backfill_tracklists_for_show(show_url)
            return jsonify({'success': True, 'total_updated': count, 'per_show': {show_url: count}})
        stats = backfill_tracklists_all()
        return jsonify({'success': True, **stats})
    except Exception as e:
        current_app.logger.exception('Backfill failed')
        return jsonify({'success': False, 'message': str(e)}), 500
