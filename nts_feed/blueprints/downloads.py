"""Download management routes."""

import json
import os
import queue
import shutil
import threading

from datetime import datetime
from flask import Blueprint, Response, current_app, jsonify, request

from ..downloader import download, download_manager
from ..ext.tasks import get_executor
from ..runtime_paths import downloads_dir
from ..scrape import load_episodes, slugify

bp = Blueprint('downloads', __name__)

DOWNLOAD_DIR = downloads_dir()
DOWNLOAD_DIR.mkdir(exist_ok=True)


def _progress_queues():
    return current_app.extensions['progress_queues']


def _active_downloads():
    return current_app.extensions['active_downloads']


@bp.route('/progress/<download_id>')
def progress(download_id):
    """SSE stream for single/batch download progress."""
    pq = _progress_queues()

    def generate():
        q = queue.Queue()
        pq[download_id] = q
        try:
            while True:
                prog = q.get()
                if prog is None:
                    break
                yield f"data: {json.dumps(prog)}\n\n"
        finally:
            pq.pop(download_id, None)

    return Response(generate(), mimetype='text/event-stream')


@bp.route('/download_episode/<path:url>')
def download_episode(url):
    """Start a single episode download in background."""
    download_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    download_path = DOWNLOAD_DIR / download_id
    download_path.mkdir(exist_ok=True)

    pq = _progress_queues()
    ad = _active_downloads()

    def download_thread():
        try:
            ad[download_id] = threading.current_thread()

            def make_progress_callback(_url):
                def callback(prog):
                    if download_id in pq:
                        pq[download_id].put({
                            'status': 'progress',
                            'percent': prog.get('percent', 0),
                            'speed': prog.get('speed', ''),
                            'eta': prog.get('eta', ''),
                            'downloaded_bytes': prog.get('downloaded_bytes', 0),
                            'total_bytes': prog.get('total_bytes', 0),
                        })
                return callback

            parsed = download(
                url, quiet=True, save_dir=str(download_path),
                progress_callback=make_progress_callback(url),
                download_id=download_id,
            )

            if parsed:
                for file in os.listdir(download_path):
                    if file.endswith('.m4a'):
                        shutil.move(str(download_path / file), str(DOWNLOAD_DIR / file))

            shutil.rmtree(download_path, ignore_errors=True)

            if download_id in pq:
                pq[download_id].put(None)
        except Exception as e:
            if download_id in pq:
                pq[download_id].put({'status': 'error', 'message': str(e)})
        finally:
            ad.pop(download_id, None)

    get_executor().submit(download_thread)
    return jsonify({'download_id': download_id})


@bp.route('/cancel_download/<batch_id>', methods=['POST'])
def cancel_download(batch_id):
    pq = _progress_queues()
    ad = _active_downloads()
    try:
        if batch_id in ad:
            download_manager.cancel_download(batch_id)
            ad.pop(batch_id)

            download_path = DOWNLOAD_DIR / batch_id
            if download_path.exists():
                for file in download_path.glob('*.part*'):
                    try:
                        file.unlink()
                    except Exception:
                        pass
                try:
                    shutil.rmtree(download_path, ignore_errors=True)
                except Exception:
                    pass

            if batch_id in pq:
                pq[batch_id].put({'status': 'cancelled', 'message': 'Download cancelled'})
                pq[batch_id].put(None)
    except Exception as e:
        current_app.logger.error(f'Error during download cancellation: {e}')
    return '', 204


@bp.route('/download_all/<path:url>')
def download_all_episodes(url):
    """Start batch download for all episodes of a show."""
    batch_id = datetime.now().strftime('%Y%m%d%H%M%S')
    download_path = DOWNLOAD_DIR / batch_id
    download_path.mkdir(exist_ok=True)

    pq = _progress_queues()
    ad = _active_downloads()

    def download_thread():
        try:
            ad[batch_id] = {'show_url': url, 'thread': threading.current_thread()}
            episodes_data = load_episodes(slugify(url))
            episodes = episodes_data.get('episodes', [])
            ad[batch_id]['episodes'] = episodes

            if batch_id in pq:
                pq[batch_id].put({'status': 'init', 'total': len(episodes)})

            for i, episode in enumerate(episodes):
                if batch_id not in ad:
                    break
                try:
                    episode_id = f"{batch_id}_ep_{i}"
                    if batch_id in pq:
                        pq[batch_id].put({
                            'status': 'starting', 'current': i + 1,
                            'total': len(episodes),
                            'episode_url': episode.get('url'),
                            'episode_id': episode_id,
                        })

                    def make_progress_callback(ep):
                        def callback(prog):
                            if batch_id in pq:
                                pq[batch_id].put({
                                    'episode_id': episode_id,
                                    'status': 'progress',
                                    'episode_url': ep.get('url'),
                                    'percent': prog.get('percent', 0),
                                    'speed': prog.get('speed', ''),
                                    'eta': prog.get('eta', ''),
                                    'downloaded_bytes': prog.get('downloaded_bytes', 0),
                                    'total_bytes': prog.get('total_bytes', 0),
                                    'message': prog.get('message', ''),
                                })
                        return callback

                    download(
                        episode.get('audio_url'), quiet=True,
                        save_dir=str(download_path),
                        progress_callback=make_progress_callback(episode),
                        download_id=episode_id,
                    )
                except Exception as e:
                    if batch_id in pq:
                        pq[batch_id].put({
                            'status': 'error', 'message': str(e),
                            'episode_url': episode.get('url'),
                        })

            if batch_id in pq:
                pq[batch_id].put(None)
        except Exception as e:
            if batch_id in pq:
                pq[batch_id].put({'status': 'error', 'message': str(e)})
        finally:
            ad.pop(batch_id, None)

    get_executor().submit(download_thread)
    return jsonify({'batch_id': batch_id})


@bp.route('/check_active_downloads')
def check_active_downloads():
    show_url = request.args.get('show_url')
    ad = _active_downloads()
    active_batch_downloads = {}
    try:
        for batch_id, info in ad.items():
            if isinstance(info, dict) and info.get('show_url') == show_url:
                active_batch_downloads[batch_id] = {
                    'type': 'batch', 'show_url': info.get('show_url'),
                }
        return jsonify({'active_downloads': active_batch_downloads})
    except Exception as e:
        current_app.logger.error(f'Error checking active downloads: {e}')
        return jsonify({'active_downloads': {}, 'error': str(e)}), 500
