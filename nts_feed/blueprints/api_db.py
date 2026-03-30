"""Database, taxonomy, and sync management API routes."""

import json
import threading
import time
import uuid
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from ..ext.tasks import get_executor
from ..scrape import load_episodes, load_shows, save_episodes, slugify
from .helpers import get_db, get_ext, get_lastfm

bp = Blueprint('api_db', __name__)

# --- Background DB rebuild job management ---
_rebuild_jobs = {}
_rebuild_jobs_lock = threading.Lock()

# --- Unified sync job management ---
_sync_jobs = {}
_sync_jobs_lock = threading.Lock()

# --- Taxonomy build state ---
_taxonomy_build_status = {
    'running': False, 'progress': 0,
    'message': '', 'error': None, 'result': None,
}
_taxonomy_build_lock = threading.Lock()


def _start_db_rebuild_job():
    """Start a background DB rebuild and return the job_id."""
    rebuild_database_from_json = get_ext('rebuild_database_from_json')
    db_sessionmaker = get_db()
    job_id = str(uuid.uuid4())

    with _rebuild_jobs_lock:
        _rebuild_jobs[job_id] = {
            'status': 'running', 'started_at': time.time(),
            'ended_at': None, 'stats': None, 'error': None,
        }

    def _run():
        try:
            stats = rebuild_database_from_json(db_sessionmaker)
            with _rebuild_jobs_lock:
                _rebuild_jobs[job_id]['status'] = 'completed'
                _rebuild_jobs[job_id]['stats'] = stats
                _rebuild_jobs[job_id]['ended_at'] = time.time()
        except Exception as e:
            with _rebuild_jobs_lock:
                _rebuild_jobs[job_id]['status'] = 'failed'
                _rebuild_jobs[job_id]['error'] = str(e)
                _rebuild_jobs[job_id]['ended_at'] = time.time()

    try:
        get_executor().submit(_run)
    except Exception:
        t = threading.Thread(target=_run, name=f"db-rebuild-{job_id}", daemon=True)
        t.start()
    return job_id


def start_sync_job(trigger='manual'):
    """Start a unified database sync job. Returns job_id."""
    rebuild_database_from_json = get_ext('rebuild_database_from_json')
    db_sessionmaker = get_db()
    logger = current_app.logger

    job_id = str(uuid.uuid4())
    with _sync_jobs_lock:
        _sync_jobs[job_id] = {
            'status': 'running', 'phase': 'starting',
            'trigger': trigger, 'started_at': time.time(),
            'ended_at': None, 'db_stats': None,
            'error': None,
        }

    def _run():
        try:
            with _sync_jobs_lock:
                _sync_jobs[job_id]['phase'] = 'rebuilding_database'

            db_stats = {}
            if rebuild_database_from_json and db_sessionmaker:
                db_stats = rebuild_database_from_json(db_sessionmaker)
                with _sync_jobs_lock:
                    _sync_jobs[job_id]['db_stats'] = db_stats

            with _sync_jobs_lock:
                _sync_jobs[job_id]['status'] = 'completed'
                _sync_jobs[job_id]['phase'] = 'completed'
                _sync_jobs[job_id]['ended_at'] = time.time()

            logger.info('Sync job %s completed: db=%s', job_id, db_stats)
        except Exception as e:
            logger.exception('Sync job %s failed', job_id)
            with _sync_jobs_lock:
                _sync_jobs[job_id]['status'] = 'failed'
                _sync_jobs[job_id]['phase'] = 'failed'
                _sync_jobs[job_id]['error'] = str(e)
                _sync_jobs[job_id]['ended_at'] = time.time()

    try:
        get_executor().submit(_run)
    except Exception:
        t = threading.Thread(target=_run, name=f"sync-{job_id}", daemon=True)
        t.start()
    return job_id


def get_running_sync_job():
    """Check if a sync job is already running and return its ID."""
    with _sync_jobs_lock:
        for jid, info in _sync_jobs.items():
            if info.get('status') == 'running':
                return jid
    return None


# ---------------------------------------------------------------------------
# DB Rebuild Routes
# ---------------------------------------------------------------------------

@bp.route('/api/db/rebuild', methods=['POST'])
def api_db_rebuild():
    rebuild_fn = get_ext('rebuild_database_from_json')
    if not (rebuild_fn and get_db()):
        return jsonify({'success': False, 'message': 'Database components not available'}), 503
    try:
        with _rebuild_jobs_lock:
            for jid, info in _rebuild_jobs.items():
                if info.get('status') == 'running':
                    return jsonify({'success': True, 'job_id': jid, 'status': 'running'}), 202
        job_id = _start_db_rebuild_job()
        return jsonify({'success': True, 'job_id': job_id, 'status': 'running'}), 202
    except Exception as e:
        current_app.logger.exception('DB rebuild start failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/db/rebuild/<job_id>')
def api_db_rebuild_status(job_id):
    try:
        with _rebuild_jobs_lock:
            info = _rebuild_jobs.get(job_id)
            if not info:
                return jsonify({'success': False, 'message': 'Not found'}), 404
            out = dict(info)
            if out.get('started_at'):
                out['duration_ms'] = int(((out.get('ended_at') or time.time()) - out['started_at']) * 1000)
            return jsonify({'success': True, 'job_id': job_id, 'info': out})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/episodes/deduplicate', methods=['POST'])
def api_episodes_deduplicate():
    """Clean duplicate episodes from all JSON files."""
    try:
        shows = load_shows()
        stats = {
            'files_processed': 0, 'files_with_duplicates': 0,
            'total_duplicates_removed': 0, 'per_show': {},
        }
        for url in shows.keys():
            show_slug = slugify(url)
            try:
                episodes_data = load_episodes(show_slug)
                episodes = episodes_data.get('episodes', [])
                if not episodes:
                    continue

                seen_urls = set()
                unique_episodes = []
                duplicates_removed = 0
                for ep in episodes:
                    ep_url = ep.get('url') or ep.get('audio_url') or ''
                    if not ep_url:
                        unique_episodes.append(ep)
                        continue
                    if ep_url not in seen_urls:
                        seen_urls.add(ep_url)
                        unique_episodes.append(ep)
                    else:
                        duplicates_removed += 1

                stats['files_processed'] += 1
                if duplicates_removed > 0:
                    save_episodes(show_slug, {'episodes': unique_episodes})
                    stats['files_with_duplicates'] += 1
                    stats['total_duplicates_removed'] += duplicates_removed
                    stats['per_show'][show_slug] = duplicates_removed
            except Exception as e:
                stats['per_show'][show_slug] = f"error: {str(e)}"

        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        current_app.logger.exception('Episode deduplication failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/db/incremental', methods=['POST'])
def api_db_incremental():
    """Incrementally update database with new episodes only."""
    incremental_fn = get_ext('incremental_update_from_json')
    db_sessionmaker = get_db()
    if not (incremental_fn and db_sessionmaker):
        return jsonify({'success': False, 'message': 'Database components not available'}), 503

    try:
        payload = request.get_json(silent=True) or {}
        show_slugs = payload.get('show_slugs')
        stats = incremental_fn(db_sessionmaker, show_slugs=show_slugs)

        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        current_app.logger.exception('Incremental update failed')
        return jsonify({'success': False, 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Sync Routes
# ---------------------------------------------------------------------------

@bp.route('/api/sync', methods=['POST'])
def api_sync():
    """Unified sync endpoint - rebuilds the database from JSON."""
    rebuild_fn = get_ext('rebuild_database_from_json')
    if not (rebuild_fn and get_db()):
        return jsonify({'success': False, 'message': 'Database components not available'}), 503
    try:
        existing_job = get_running_sync_job()
        if existing_job:
            return jsonify({
                'success': True, 'job_id': existing_job,
                'status': 'running', 'message': 'Sync already in progress',
            }), 202
        job_id = start_sync_job(trigger='manual')
        return jsonify({
            'success': True, 'job_id': job_id,
            'status': 'running', 'message': 'Sync started',
        }), 202
    except Exception as e:
        current_app.logger.exception('Sync start failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/sync/<job_id>')
def api_sync_status(job_id):
    """Get status of a sync job."""
    try:
        with _sync_jobs_lock:
            info = _sync_jobs.get(job_id)
            if not info:
                return jsonify({'success': False, 'message': 'Job not found'}), 404
            out = dict(info)
            if out.get('started_at'):
                out['duration_ms'] = int(((out.get('ended_at') or time.time()) - out['started_at']) * 1000)
            return jsonify({'success': True, 'job_id': job_id, 'info': out})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Taxonomy Routes
# ---------------------------------------------------------------------------

@bp.route('/api/taxonomy/build', methods=['POST'])
def api_taxonomy_build():
    """Build or rebuild the genre taxonomy from Last.fm (async)."""
    try:
        from ..services.genre_taxonomy_service import GenreTaxonomyService

        data = request.get_json(silent=True) or {}
        force_rebuild = data.get('force', False)

        with _taxonomy_build_lock:
            if _taxonomy_build_status['running']:
                return jsonify({'success': True, 'started': False, 'message': 'Build already in progress'})

            if not force_rebuild:
                from pathlib import Path
                cache_path = Path('data/genre_taxonomy.json')
                if cache_path.exists():
                    taxonomy_service = GenreTaxonomyService(cache_dir='data/')
                    taxonomy = taxonomy_service.get_taxonomy(force_rebuild=False)
                    return jsonify({
                        'success': True, 'started': False,
                        'families': len(taxonomy.families),
                        'incompatibilities': len(taxonomy.incompatibilities),
                        'genre_count': len(taxonomy.genre_to_family),
                        'message': f"Taxonomy already exists with {len(taxonomy.families)} families",
                    })

            _taxonomy_build_status['running'] = True
            _taxonomy_build_status['progress'] = 0
            _taxonomy_build_status['message'] = 'Starting build...'
            _taxonomy_build_status['error'] = None
            _taxonomy_build_status['result'] = None

        def build_taxonomy():
            try:
                def on_progress(status, current, total):
                    with _taxonomy_build_lock:
                        _taxonomy_build_status['progress'] = int((current / total) * 100)
                        _taxonomy_build_status['message'] = status

                lastfm_service = get_lastfm()
                taxonomy_service = GenreTaxonomyService(
                    lastfm_service=lastfm_service, cache_dir='data/',
                )
                if force_rebuild:
                    taxonomy_service.clear_cache()

                taxonomy = taxonomy_service.build_taxonomy(on_progress=on_progress)
                taxonomy_service._save_cache(taxonomy)
                taxonomy_service._taxonomy = taxonomy

                with _taxonomy_build_lock:
                    _taxonomy_build_status['running'] = False
                    _taxonomy_build_status['progress'] = 100
                    _taxonomy_build_status['message'] = 'Build complete!'
                    _taxonomy_build_status['result'] = {
                        'families': len(taxonomy.families),
                        'genres': len(taxonomy.genre_to_family),
                        'incompatibilities': len(taxonomy.incompatibilities),
                    }
            except Exception as e:
                current_app.logger.exception('Taxonomy build failed in background')
                with _taxonomy_build_lock:
                    _taxonomy_build_status['running'] = False
                    _taxonomy_build_status['error'] = str(e)
                    _taxonomy_build_status['message'] = f'Build failed: {e}'

        try:
            get_executor().submit(build_taxonomy)
        except Exception:
            t = threading.Thread(target=build_taxonomy, name="taxonomy-build", daemon=True)
            t.start()

        return jsonify({
            'success': True, 'started': True,
            'message': 'Build started. Poll /api/taxonomy/build/status for progress.',
        })
    except Exception as e:
        with _taxonomy_build_lock:
            _taxonomy_build_status['running'] = False
        current_app.logger.exception('Taxonomy build failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/taxonomy/build/status')
def api_taxonomy_build_status():
    """Get the status of the taxonomy build."""
    with _taxonomy_build_lock:
        return jsonify({
            'running': _taxonomy_build_status['running'],
            'progress': _taxonomy_build_status['progress'],
            'message': _taxonomy_build_status['message'],
            'error': _taxonomy_build_status['error'],
            'result': _taxonomy_build_status['result'],
        })


@bp.route('/api/taxonomy/status')
def api_taxonomy_status():
    """Get current taxonomy status."""
    try:
        from pathlib import Path
        cache_path = Path('data/genre_taxonomy.json')
        if not cache_path.exists():
            return jsonify({
                'available': False,
                'message': 'Taxonomy not built. POST to /api/taxonomy/build to create it.',
            })

        with open(cache_path) as f:
            data = json.load(f)

        built_at = data.get('built_at', 0)
        age_hours = (time.time() - built_at) / 3600

        return jsonify({
            'available': True,
            'families': len(data.get('families', {})),
            'genres': len(data.get('genre_to_family', {})),
            'incompatibility_rules': sum(len(v) for v in data.get('incompatibilities', {}).values()),
            'built_at': built_at,
            'age_hours': round(age_hours, 1),
            'expires_in_hours': max(0, round(7 * 24 - age_hours, 1)),
        })
    except Exception as e:
        current_app.logger.exception('Taxonomy status check failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/taxonomy/lookup/<genre>')
def api_taxonomy_lookup(genre):
    """Look up a genre's family and similar genres."""
    try:
        from ..services.genre_taxonomy_service import GenreTaxonomyService
        from pathlib import Path

        cache_path = Path('data/genre_taxonomy.json')
        if not cache_path.exists():
            return jsonify({'error': 'Taxonomy not available. POST to /api/taxonomy/build first.'}), 404

        taxonomy_service = GenreTaxonomyService(cache_dir='data/')
        family = taxonomy_service.get_genre_family(genre)
        similar = taxonomy_service.get_similar_genres(genre, min_similarity=0.2)

        return jsonify({
            'genre': genre,
            'family': family,
            'similar': [{'name': name, 'score': round(score, 3)} for name, score in similar[:20]],
        })
    except Exception as e:
        current_app.logger.exception(f'Taxonomy lookup failed for {genre}')
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Thumbnail Warming Routes
# ---------------------------------------------------------------------------

@bp.route('/admin/warm_thumbnails', methods=['POST'])
def admin_warm_thumbnails():
    """Start async thumbnail warming."""
    try:
        image_cache_service = get_ext('image_cache_service')
        payload = request.get_json(silent=True) or {}
        include_episodes = bool(payload.get('include_episodes', False))
        limit_per_show = int(payload.get('limit_per_show', 0) or 0)
        concurrency = int(payload.get('concurrency', 8) or 8)
        force = bool(payload.get('force', False))

        warm_id = datetime.now().strftime('%Y%m%d_%H%M%S%f')
        if not hasattr(current_app._get_current_object(), 'thumbnail_warm_jobs'):
            current_app._get_current_object().thumbnail_warm_jobs = {}
        warm_jobs = current_app._get_current_object().thumbnail_warm_jobs

        def _worker():
            try:
                urls = []
                shows = load_shows()
                for show_url, s in shows.items():
                    thumb = (s or {}).get('thumbnail')
                    if thumb:
                        urls.append(thumb)
                    if include_episodes:
                        try:
                            show_slug = slugify(show_url)
                            episodes_data = load_episodes(show_slug)
                            eps = episodes_data.get('episodes', [])
                            if limit_per_show > 0:
                                eps = eps[:limit_per_show]
                            for ep in eps:
                                t = ep.get('image_url')
                                if t:
                                    urls.append(t)
                        except Exception:
                            continue
                if urls and image_cache_service:
                    image_cache_service.prefetch_many(urls, concurrency=concurrency, force=force)
                warm_jobs[warm_id] = {'status': 'completed', 'completed_at': datetime.now().isoformat()}
            except Exception as e:
                warm_jobs[warm_id] = {'status': 'error', 'message': str(e)}

        warm_jobs[warm_id] = {'status': 'running', 'started_at': datetime.now().isoformat()}
        get_executor().submit(_worker)
        return jsonify({'success': True, 'warm_id': warm_id})
    except Exception as e:
        current_app.logger.exception('Thumbnail warm start failed')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/admin/warm_thumbnails/<warm_id>')
def admin_warm_status(warm_id):
    try:
        warm_jobs = getattr(current_app._get_current_object(), 'thumbnail_warm_jobs', {})
        info = warm_jobs.get(warm_id)
        if not info:
            return jsonify({'success': False, 'message': 'Not found'}), 404
        return jsonify({'success': True, 'status': info})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
