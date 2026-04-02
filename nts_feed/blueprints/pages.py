"""Page routes - HTML template rendering for all top-level pages."""

import os
from datetime import datetime
from flask import Blueprint, render_template, request

from ..scrape import load_shows, load_episodes, slugify
from .helpers import get_track_manager, get_image_cache, parse_episode_date

bp = Blueprint('pages', __name__)


@bp.route('/')
def index():
    """Homepage with feed of recent episodes across all subscribed shows."""
    shows = load_shows()
    track_manager = get_track_manager()

    all_episodes = []
    episodes_limit = 50

    for url, show_data in shows.items():
        try:
            show_slug = slugify(url)
            episodes_data = load_episodes(show_slug)
            eps = episodes_data.get('episodes', [])
            for ep in eps:
                ep_copy = ep.copy()
                ep_copy['show_url'] = url
                ep_copy['show_title'] = show_data.get('title', 'Unknown Show')
                ep_copy['show_thumbnail'] = show_data.get('thumbnail')
                parsed_date = parse_episode_date(ep.get('date', ''))
                ep_copy['_sort_date'] = parsed_date or datetime.min
                all_episodes.append(ep_copy)
        except Exception:
            continue

    all_episodes.sort(key=lambda x: x['_sort_date'], reverse=True)
    recent_episodes = all_episodes[:episodes_limit]

    for ep in recent_episodes:
        ep.pop('_sort_date', None)

    downloaded_episodes = track_manager.get_downloaded_episodes()
    for ep in recent_episodes:
        ep['is_downloaded'] = ep.get('url') in downloaded_episodes

    return render_template('index.html', episodes=recent_episodes)


@bp.route('/shows')
def shows_page():
    """Render shows list with subscriptions metadata."""
    shows = load_shows()
    image_cache_service = get_image_cache()

    embed_episodes = os.getenv('INDEX_EMBED_EPISODES', 'false').lower() == 'true'

    for url, show_data in shows.items():
        try:
            show_slug = slugify(url)
            episodes_data = load_episodes(show_slug)
            eps = episodes_data.get('episodes', [])

            if eps:
                latest_date = None
                for ep in eps:
                    parsed = parse_episode_date(ep.get('date', ''))
                    if parsed and (latest_date is None or parsed > latest_date):
                        latest_date = parsed
                show_data['latest_episode_date'] = (
                    latest_date.isoformat() if latest_date
                    else show_data.get('last_updated', '1970-01-01T00:00:00')
                )
            else:
                show_data['latest_episode_date'] = show_data.get(
                    'last_updated', '1970-01-01T00:00:00',
                )

            if embed_episodes:
                try:
                    limit = int(os.getenv('INDEX_EPISODES_LIMIT', '0'))
                except Exception:
                    limit = 0
                if isinstance(limit, int) and limit > 0:
                    eps = eps[:limit]
                show_data['episodes'] = eps
        except Exception:
            show_data['latest_episode_date'] = show_data.get(
                'last_updated', '1970-01-01T00:00:00',
            )

    if not embed_episodes:
        for _, show_data in shows.items():
            if isinstance(show_data, dict):
                show_data.pop('episodes', None)

    try:
        if os.getenv('IMAGE_CACHE_WARM_ON_INDEX', 'false').lower() == 'true':
            urls = []
            for _, s in shows.items():
                thumb = (s or {}).get('thumbnail')
                if thumb:
                    urls.append(thumb)
                if embed_episodes:
                    for ep in (s or {}).get('episodes', [])[:100]:
                        t = ep.get('image_url')
                        if t:
                            urls.append(t)
            if urls and image_cache_service:
                image_cache_service.prefetch_many(urls, concurrency=8)
    except Exception:
        pass

    return render_template('shows.html', subscriptions=shows)


@bp.route('/show/<path:url>')
def show(url):
    """Render show page with paginated episodes."""
    shows = load_shows()
    show_data = shows.get(url, {})
    if not show_data:
        from werkzeug.exceptions import abort
        return abort(404)

    track_manager = get_track_manager()
    image_cache_service = get_image_cache()

    show_slug = slugify(url)
    episodes_data = load_episodes(show_slug)

    per_page = 20
    all_episodes = episodes_data.get('episodes', [])
    total_episodes = len(all_episodes)
    first_page_episodes = all_episodes[:per_page]

    new_episodes = sum(1 for ep in all_episodes if ep.get('is_new', False))
    show_data['new_episodes'] = new_episodes

    if 'auto_download' not in show_data:
        show_data['auto_download'] = False

    downloaded_episodes = track_manager.get_downloaded_episodes()
    for episode in first_page_episodes:
        episode['is_downloaded'] = episode.get('url') in downloaded_episodes

    show_data['episodes'] = first_page_episodes
    show_data['total_episodes'] = total_episodes
    show_data['per_page'] = per_page

    try:
        if show_data.get('thumbnail') and image_cache_service:
            image_cache_service.get_or_fetch(show_data['thumbnail'])
        for ep in first_page_episodes:
            thumb = ep.get('image_url')
            if thumb and image_cache_service:
                image_cache_service.get_or_fetch(thumb)
    except Exception:
        pass

    return render_template('show.html', show_data=show_data, show_url=url)


@bp.route('/mixtape')
@bp.route('/discover')
def mixtape_page():
    """Discover page - episode-first recommendations."""
    return render_template('mixtape.html')


@bp.route('/stats')
def stats_page():
    """Render the Stats page."""
    return render_template('stats.html')


@bp.route('/search')
def search_page():
    """Render the dedicated search results page."""
    query = request.args.get('q', '')
    types = request.args.get('types', 'show,episode,track,artist,genre')
    return render_template('search.html', query=query, types=types)


@bp.route('/likes')
def likes_page():
    """Render the Likes/Playlists page."""
    return render_template('likes.html')


@bp.route('/admin')
def admin_page():
    """Render the admin dashboard."""
    return render_template('admin.html')
