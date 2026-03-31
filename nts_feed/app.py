"""Flask application factory.

This module creates and configures the Flask application. Route handlers
are organised into blueprints under ``nts_feed/blueprints/``.
"""

import argparse
import atexit
import base64
import logging
import os
import pathlib
import random
import threading
import time
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from flask_talisman import Talisman
except Exception:
    Talisman = None

from . import __version__
from .db.bootstrap import bootstrap_database
from .settings import apply_saved_settings_to_env
from .services_init import init_services


def _is_main_worker() -> bool:
    """Return True if background tasks should run in this process.

    With Gunicorn ``--preload``, ``create_app()`` is called once in the master
    process, then workers are forked.  Background threads started in the master
    survive into exactly **one** worker (the first to fork).  However, without
    ``--preload`` or when running Flask dev server, ``create_app()`` is called
    per-worker.  This helper uses a file lock so that only the first process to
    acquire it starts long-lived background tasks (scheduler).
    """
    import fcntl

    lock_path = '/tmp/nts_bg_tasks.lock'
    try:
        # Keep the fd open for the lifetime of the process (intentionally leaked)
        lock_fd = open(lock_path, 'w')  # noqa: SIM115
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Prevent GC from closing the fd (which would release the lock)
        _is_main_worker._lock_fd = lock_fd  # type: ignore[attr-defined]
        return True
    except (IOError, OSError):
        return False


def create_app():
    """Create and configure the Flask application."""
    load_dotenv()
    apply_saved_settings_to_env()
    os.makedirs('data', exist_ok=True)

    package_dir = pathlib.Path(__file__).resolve().parent
    project_root = package_dir.parent

    app = Flask(
        __name__,
        template_folder=str(project_root / 'templates'),
        static_folder=str(project_root / 'static'),
    )
    app.secret_key = os.getenv('SECRET_KEY')
    if not app.secret_key:
        raise RuntimeError('SECRET_KEY environment variable must be set')

    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['PREFERRED_URL_SCHEME'] = 'https'
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )

    class HealthCheckFilter(logging.Filter):
        def filter(self, record):
            return 'GET /health' not in record.getMessage()

    logging.getLogger('werkzeug').addFilter(HealthCheckFilter())

    # ------------------------------------------------------------------
    # Security headers (Flask-Talisman)
    # ------------------------------------------------------------------
    security_enabled = os.getenv('ENABLE_TALISMAN', 'true').lower() == 'true'
    if security_enabled:
        # CSP: script-src is strict (nonce-based, no unsafe-inline/eval).
        # style-src keeps 'unsafe-inline' because 186+ JS call-sites use
        # element.style.* for dynamic UI — the security value of blocking
        # inline styles is negligible (no JS execution via CSS in modern
        # browsers). Nonces are auto-generated per-request for inline data
        # scripts (see templates/shows.html {{ csp_nonce() }}).
        csp = {
            'default-src': "'self'",
            'script-src': ["'self'", 'https://www.youtube.com', 'https://s.ytimg.com'],
            'style-src': ["'self'", "'unsafe-inline'", 'https://cdnjs.cloudflare.com'],
            'font-src': ["'self'", 'https://cdnjs.cloudflare.com', 'data:'],
            'img-src': ["'self'", 'data:', 'https:'],
            'media-src': ["'self'", 'blob:', 'https:'],
            'frame-src': [
                'https://www.youtube.com',
                'https://w.soundcloud.com',
                'https://www.mixcloud.com',
            ],
            'connect-src': ["'self'", 'https:'],
        }
        force_https = os.getenv('FORCE_HTTPS', 'false').lower() == 'true'
        permissions_policy = {
            'geolocation': '()', 'microphone': '()', 'camera': '()',
            'payment': '()', 'usb': '()',
        }
        if Talisman is not None:
            Talisman(
                app, content_security_policy=csp,
                content_security_policy_nonce_in=['script-src'],
                permissions_policy=permissions_policy,
                force_https=force_https,
                session_cookie_secure=True, session_cookie_samesite='Lax',
                frame_options='SAMEORIGIN',
            )
        else:
            @app.before_request
            def _fallback_security_nonce():
                g.csp_nonce = base64.b64encode(os.urandom(16)).decode('ascii')

            @app.context_processor
            def inject_csp_nonce():
                return {'csp_nonce': lambda: getattr(g, 'csp_nonce', '')}

            @app.after_request
            def _apply_fallback_security_headers(response):
                nonce = getattr(g, 'csp_nonce', '')
                directives = []
                for key, value in csp.items():
                    if isinstance(value, list):
                        values = list(value)
                    else:
                        values = [value]
                    if key == 'script-src' and nonce:
                        values.append(f"'nonce-{nonce}'")
                    directives.append(f"{key} {' '.join(values)}")

                response.headers.setdefault('Content-Security-Policy', '; '.join(directives))
                response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
                response.headers.setdefault('X-Content-Type-Options', 'nosniff')
                response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
                response.headers.setdefault(
                    'Permissions-Policy',
                    'geolocation=(), microphone=(), camera=(), payment=(), usb=()',
                )
                if force_https:
                    response.headers.setdefault(
                        'Strict-Transport-Security',
                        'max-age=31536000; includeSubDomains',
                    )
                return response
    else:
        @app.context_processor
        def inject_empty_csp_nonce():
            return {'csp_nonce': lambda: ''}

    # ------------------------------------------------------------------
    # Initialize database/runtime services.
    # ------------------------------------------------------------------
    bootstrap_database(app)
    init_services(app)

    # ------------------------------------------------------------------
    # Context processor for SPA support
    # ------------------------------------------------------------------
    @app.context_processor
    def inject_spa_context():
        is_partial = (
            request.args.get('partial') == '1'
            or request.headers.get('X-SPA-Request') == '1'
        )
        return {'is_partial': is_partial, 'cache_bust': random.randint(1, 100000)}

    # ------------------------------------------------------------------
    # Cleanup handler
    # ------------------------------------------------------------------
    @atexit.register
    def cleanup():
        track_manager = app.extensions.get('track_manager')
        if track_manager:
            track_manager.save_database()

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    @app.route('/health')
    def health():
        """Health check endpoint for container orchestration."""
        return jsonify({'status': 'healthy'}), 200

    # ------------------------------------------------------------------
    # Register blueprints
    # ------------------------------------------------------------------
    from .blueprints.pages import bp as pages_bp
    from .blueprints.shows_mgmt import bp as shows_mgmt_bp
    from .blueprints.downloads import bp as downloads_bp
    from .blueprints.updates import bp as updates_bp
    from .blueprints.search import bp as search_bp
    from .blueprints.thumbs import bp as thumbs_bp
    from .blueprints.api_tracks import bp as api_tracks_bp
    from .blueprints.api_search_unified import bp as api_search_unified_bp
    from .blueprints.api_discover import bp as api_discover_bp
    from .blueprints.api_mixtape import bp as api_mixtape_bp
    from .blueprints.api_likes import bp as api_likes_bp
    from .blueprints.api_listening import bp as api_listening_bp
    from .blueprints.api_admin import bp as api_admin_bp
    from .blueprints.api_db import bp as api_db_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(shows_mgmt_bp)
    app.register_blueprint(downloads_bp)
    app.register_blueprint(updates_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(thumbs_bp)
    app.register_blueprint(api_tracks_bp)
    app.register_blueprint(api_search_unified_bp)
    app.register_blueprint(api_discover_bp)
    app.register_blueprint(api_mixtape_bp)
    app.register_blueprint(api_likes_bp)
    app.register_blueprint(api_listening_bp)
    app.register_blueprint(api_admin_bp)
    app.register_blueprint(api_db_bp)

    # ------------------------------------------------------------------
    # Daily update scheduler  (only in the designated background worker)
    # ------------------------------------------------------------------
    if _is_main_worker():
        try:
            daily_update_enabled = os.getenv('DAILY_UPDATE_ENABLED', 'true').lower() == 'true'
            if daily_update_enabled and not hasattr(app, '_daily_update_thread_started'):
                app._daily_update_thread_started = True

                def _start_daily_update_scheduler():
                    try:
                        from zoneinfo import ZoneInfo
                    except Exception:
                        ZoneInfo = None
                    from datetime import timedelta

                    tz_name = os.getenv('DAILY_UPDATE_TZ', 'America/New_York')
                    hhmm = (os.getenv('DAILY_UPDATE_TIME') or '10:00').strip()
                    try:
                        hour_str, minute_str = hhmm.split(':', 1)
                        hour, minute = int(hour_str), int(minute_str)
                    except Exception:
                        hour, minute = 10, 0

                    def _compute_next_run(now_local):
                        target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        if now_local >= target:
                            target += timedelta(days=1)
                        return target

                    def _loop():
                        announced_next_run = False
                        while True:
                            try:
                                now_local = datetime.now(ZoneInfo(tz_name)) if ZoneInfo else datetime.now()
                                next_run = _compute_next_run(now_local)
                                sleep_sec = max(1, int((next_run - now_local).total_seconds()))
                                if not announced_next_run:
                                    app.logger.info(
                                        'Daily update scheduler enabled; next run at %s (%s)',
                                        next_run.isoformat(), tz_name,
                                    )
                                    announced_next_run = True
                                else:
                                    app.logger.debug(
                                        'Daily update scheduler waiting until %s (%s); sleeping %ds',
                                        next_run.isoformat(), tz_name, sleep_sec,
                                    )
                                time.sleep(sleep_sec)
                                update_service = app.extensions.get('update_service')
                                if update_service is None:
                                    continue
                                try:
                                    update_service.cleanup_completed_updates()
                                except Exception:
                                    pass
                                try:
                                    update_id = update_service.start_update(enable_auto_download=True)
                                    app.logger.info('Scheduled daily update started: %s', update_id)
                                except Exception as e:
                                    app.logger.exception('Failed to start scheduled daily update: %s', e)
                            except Exception:
                                time.sleep(60)

                    t = threading.Thread(target=_loop, name='daily-update-scheduler', daemon=True)
                    t.start()

                _start_daily_update_scheduler()
        except Exception:
            pass

    return app


def _build_argument_parser() -> argparse.ArgumentParser:
    """Return the CLI parser for the packaged app entry point."""
    parser = argparse.ArgumentParser(
        prog='nts-feed',
        description='Run the NTS Feed web application.',
    )
    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='Host interface to bind the Flask development server to.',
    )
    parser.add_argument(
        '--port',
        type=int,
        default=5555,
        help='Port to bind the Flask development server to.',
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable Flask debug mode for local development.',
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}',
    )
    return parser


def main(argv=None):
    """Entry point for the application."""
    args = _build_argument_parser().parse_args(argv)
    app = create_app()
    debug_mode = args.debug or os.getenv('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')
    app.run(host=args.host, port=args.port, debug=debug_mode)


if __name__ == '__main__':
    main()
