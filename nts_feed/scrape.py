import requests
from bs4 import BeautifulSoup
import json
import os
import fcntl
from datetime import datetime
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from .services.cache_service import cache_service, with_cache
from dotenv import load_dotenv
from .storage.json_store import get_episodes_store

# Concurrency settings for episode page fetching
EPISODE_FETCH_WORKERS = 15  # Number of concurrent workers for fetching episode pages
# Note: NTS API has a max page size of ~12 episodes regardless of limit requested
# We request more in case they increase the limit, but always use actual count returned for pagination
API_BATCH_SIZE = 50  # Requested limit per API call (actual may be less due to API limits)

def create_session():
    """Create a requests session with retry strategy"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dotenv_path = os.path.join(project_root, '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)

    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    # Identify as a browser; some endpoints may require a UA
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9'
    })
    # Optional cookie pass-through for NTS Supporter timestamps
    nts_cookies = os.environ.get('NTS_COOKIES')
    if nts_cookies:
        # Set raw header for safety
        session.headers['Cookie'] = nts_cookies
        # Also populate cookie jar with scoped cookies
        try:
            for pair in [c.strip() for c in nts_cookies.split(';') if '=' in c]:
                name, value = pair.split('=', 1)
                name = name.strip()
                value = value.strip()
                # Scope to nts.live
                session.cookies.set(name, value, domain='.nts.live', path='/')
                if name.lower() == 'csrftoken':
                    session.headers['X-CSRFToken'] = value
        except Exception:
            pass
    return session

def scrape_nts_show(url):
    session = create_session()
    try:
        response = session.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Get show title - updated selector
        title_elem = soup.find('h1', class_='text-bold')
        title = title_elem.text.strip() if title_elem else url.split('/')[-1]
        
        # Get show description - updated selector
        desc_elem = soup.find('div', class_='description')
        description = desc_elem.find('h3').text.strip() if desc_elem else ''
        
        # Improved show image extraction
        image_selectors = [
            'div.profile-image img.profile-image__img',
            'meta[property="og:image"]',
            'meta[name="twitter:image"]'
        ]
        
        thumbnail = None
        for selector in image_selectors:
            elem = soup.select_one(selector)
            if elem:
                thumbnail = elem.get('src') or elem.get('content')
                if thumbnail:
                    thumbnail = thumbnail.replace('resize/100x100', 'resize/1000x1000')
                    break
        
        # Extract show slug from URL for API calls
        show_slug = url.split('/')[-1]
        
        # Get episodes
        episodes = []
        offset = 0
        total_count = None
        
        while True:
            api_url = f'https://www.nts.live/api/v2/shows/{show_slug}/episodes?offset={offset}&limit={API_BATCH_SIZE}'
            print(f"Fetching: {api_url}")
            
            try:
                response = session.get(api_url)
                data = response.json()
                
                if total_count is None:
                    total_count = int(data['metadata']['resultset']['count'])
                    print(f"Total episodes: {total_count}")
                
                results = data.get('results') or []
                if not results:
                    break
                
                # Count results before filtering to correctly advance offset
                results_count = len(results)
                    
                for ep in results:
                    if ep['status'] == 'published':
                        try:
                            episode_alias = ep['episode_alias']
                            date_parts = episode_alias.split('-')[-3:]
                            day = date_parts[0].lower().replace('st', '').replace('nd', '').replace('rd', '').replace('th', '')
                            month = date_parts[1]
                            year = date_parts[2]
                            date_obj = datetime.strptime(f"{day} {month} {year}", "%d %B %Y")
                            date = date_obj.strftime("%B %d, %Y")
                        except Exception:
                            date = '-'.join(date_parts).title()

                        episode_url = f"https://www.nts.live/shows/{show_slug}/episodes/{ep['episode_alias']}"

                        # Use the same cached page parser used elsewhere to ensure consistency and full timestamps
                        page_data = _fetch_episode_page(episode_url)
                        tracks = page_data.get('tracklist', []) if page_data else []
                        genres = page_data.get('genres', ["Electronic"]) if page_data else ["Electronic"]

                        episode = {
                            'title': ep.get('name', ''),
                            'date': date,
                            'audio_url': episode_url,
                            'genres': genres,
                            'tracklist': tracks,
                            'image_url': ep.get('media', {}).get('background_medium', ''),
                            'url': episode_url,
                            'is_new': True
                        }
                        episodes.append(episode)
                        print(f"Found episode: {episode['title']} ({episode['date']})")
                
                # CRITICAL: Increment offset by actual results returned, not requested limit
                offset += results_count
                
                # Stop when we've reached the end
                if offset >= total_count:
                    break
                    
            except Exception as e:
                print(f"Error fetching episodes: {e}")
                break
        
        return {
            'title': title,
            'description': description,
            'thumbnail': thumbnail,
            'episodes': episodes
        }
        
    except Exception as e:
        print(f"Error scraping show: {e}")
        return None
    finally:
        session.close()

def scrape_nts_show_progress(url, on_progress=None, defer_tracklists=False):
    """Scrape NTS show with concurrent episode fetching and progress callbacks.

    Args:
        url: NTS show URL
        on_progress: callable(dict) -> None, receives progress dictionaries like:
            { 'type': 'started', 'show_title': str, 'total_episodes': int }
            { 'type': 'progress', 'current': int, 'total': int, 'episode_title': str }
            { 'type': 'completed', 'total': int }
        defer_tracklists: If True, skip fetching tracklists (they load on-demand later).
            This makes subscription near-instant.

    Returns:
        dict with keys: title, description, thumbnail, episodes
    """
    session = create_session()
    try:
        response = session.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        title_elem = soup.find('h1', class_='text-bold')
        title = title_elem.text.strip() if title_elem else url.split('/')[-1]

        desc_elem = soup.find('div', class_='description')
        description = desc_elem.find('h3').text.strip() if desc_elem else ''

        image_selectors = [
            'div.profile-image img.profile-image__img',
            'meta[property="og:image"]',
            'meta[name="twitter:image"]'
        ]
        thumbnail = None
        for selector in image_selectors:
            elem = soup.select_one(selector)
            if elem:
                thumbnail = elem.get('src') or elem.get('content')
                if thumbnail:
                    thumbnail = thumbnail.replace('resize/100x100', 'resize/1000x1000')
                    break

        show_slug = url.split('/')[-1]

        # Phase 1: Collect all episode metadata from API (fast)
        # Industry best practice: Use cursor-based pagination with actual result count
        episode_metadata = []
        offset = 0
        total_count = None

        while True:
            api_url = f'https://www.nts.live/api/v2/shows/{show_slug}/episodes?offset={offset}&limit={API_BATCH_SIZE}'
            try:
                response = session.get(api_url)
                data = response.json()

                if total_count is None:
                    total_count = int(data.get('metadata', {}).get('resultset', {}).get('count', 0))
                    if callable(on_progress):
                        on_progress({
                            'type': 'started',
                            'show_title': title,
                            'total_episodes': total_count
                        })

                results = data.get('results') or []
                if not results:
                    break

                # Count results before filtering to correctly advance offset
                results_count = len(results)

                for ep in results:
                    if ep.get('status') != 'published':
                        continue
                    episode_metadata.append(ep)

                # CRITICAL: Increment offset by actual results returned, not requested limit
                # The API may return fewer results than requested (e.g., API max page size)
                offset += results_count
                
                # Stop when we've reached the end (offset >= total or empty results)
                if total_count is not None and offset >= total_count:
                    break

            except Exception as e:
                if callable(on_progress):
                    on_progress({'type': 'error', 'message': f'Error fetching episode list: {e}', 'current': 0, 'total': total_count or 0})
                break

        if not episode_metadata:
            if callable(on_progress):
                on_progress({'type': 'completed', 'total': 0})
            return {
                'title': title,
                'description': description,
                'thumbnail': thumbnail,
                'episodes': []
            }

        # Phase 2: Build episode objects - either with concurrent tracklist fetching or deferred
        episodes = []
        completed_count = 0
        progress_lock = threading.Lock()

        def parse_episode_metadata(ep):
            """Parse basic episode metadata from API response."""
            try:
                episode_alias = ep['episode_alias']
                date_parts = episode_alias.split('-')[-3:]
                day = date_parts[0].lower().replace('st', '').replace('nd', '').replace('rd', '').replace('th', '')
                month = date_parts[1]
                year = date_parts[2]
                date_obj = datetime.strptime(f"{day} {month} {year}", "%d %B %Y")
                date = date_obj.strftime("%B %d, %Y")
            except Exception:
                date = ''

            episode_url = f"https://www.nts.live/shows/{show_slug}/episodes/{ep['episode_alias']}"
            
            return {
                'ep': ep,
                'episode_alias': ep['episode_alias'],
                'episode_url': episode_url,
                'date': date,
                'title': ep.get('name', ''),
                'image_url': ep.get('media', {}).get('background_medium', ''),
            }

        def fetch_episode_with_tracklist(ep_info, index):
            """Fetch tracklist for a single episode (runs in thread pool)."""
            nonlocal completed_count
            
            try:
                page_data = _fetch_episode_page(ep_info['episode_url'])
                tracks = page_data.get('tracklist', []) if page_data else []
                genres = page_data.get('genres', ["Electronic"]) if page_data else ["Electronic"]
            except Exception:
                tracks = []
                genres = ["Electronic"]

            episode = {
                'title': ep_info['title'],
                'date': ep_info['date'],
                'audio_url': ep_info['episode_url'],
                'genres': genres,
                'tracklist': tracks,
                'image_url': ep_info['image_url'],
                'url': ep_info['episode_url'],
                'is_new': True
            }

            # Thread-safe progress reporting
            with progress_lock:
                completed_count += 1
                if callable(on_progress):
                    on_progress({
                        'type': 'progress',
                        'current': completed_count,
                        'total': len(episode_metadata),
                        'episode_title': episode['title']
                    })

            return (index, episode)

        # Parse all episode metadata first
        parsed_episodes = [parse_episode_metadata(ep) for ep in episode_metadata]

        if defer_tracklists:
            # Fast mode: Skip tracklist fetching, just use metadata
            for i, ep_info in enumerate(parsed_episodes):
                episode = {
                    'title': ep_info['title'],
                    'date': ep_info['date'],
                    'audio_url': ep_info['episode_url'],
                    'genres': [],  # Will be fetched on-demand
                    'tracklist': [],  # Will be fetched on-demand
                    'image_url': ep_info['image_url'],
                    'url': ep_info['episode_url'],
                    'is_new': True
                }
                episodes.append(episode)
                
                if callable(on_progress):
                    on_progress({
                        'type': 'progress',
                        'current': i + 1,
                        'total': len(parsed_episodes),
                        'episode_title': episode['title']
                    })
        else:
            # Concurrent mode: Fetch all episode pages in parallel
            # Pre-allocate results list to maintain order
            results = [None] * len(parsed_episodes)
            
            with ThreadPoolExecutor(max_workers=EPISODE_FETCH_WORKERS) as executor:
                # Submit all fetch tasks
                future_to_index = {
                    executor.submit(fetch_episode_with_tracklist, ep_info, i): i
                    for i, ep_info in enumerate(parsed_episodes)
                }

                # Collect results as they complete
                for future in as_completed(future_to_index):
                    try:
                        index, episode = future.result()
                        results[index] = episode
                    except Exception:
                        # On error, create episode with empty tracklist
                        idx = future_to_index[future]
                        ep_info = parsed_episodes[idx]
                        results[idx] = {
                            'title': ep_info['title'],
                            'date': ep_info['date'],
                            'audio_url': ep_info['episode_url'],
                            'genres': ["Electronic"],
                            'tracklist': [],
                            'image_url': ep_info['image_url'],
                            'url': ep_info['episode_url'],
                            'is_new': True
                        }
                        # Still report progress
                        with progress_lock:
                            completed_count += 1
                            if callable(on_progress):
                                on_progress({
                                    'type': 'progress',
                                    'current': completed_count,
                                    'total': len(episode_metadata),
                                    'episode_title': ep_info['title']
                                })

            # Filter out any None values (shouldn't happen but defensive)
            episodes = [ep for ep in results if ep is not None]

        if callable(on_progress):
            on_progress({'type': 'completed', 'total': len(episodes)})

        return {
            'title': title,
            'description': description,
            'thumbnail': thumbnail,
            'episodes': episodes
        }

    except Exception as e:
        if callable(on_progress):
            on_progress({'type': 'error', 'message': f'Error scraping show: {e}'})
        return None
    finally:
        session.close()
def slugify(url):
    """Convert show URL to a filename-safe slug"""
    path = urlparse(url).path
    show_name = path.split('/')[-1]
    return show_name.replace('/', '-')

def load_shows():
    """Load shows with file locking to prevent race conditions"""
    if not os.path.exists('shows.json'):
        print("Warning: shows.json does not exist, returning empty dict")
        return {}
    
    try:
        with open('shows.json', 'r') as f:
            # Acquire shared lock for reading
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.load(f)
                print(f"Successfully loaded {len(data)} shows from shows.json")
                return data
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, IOError) as e:
        print(f"ERROR: Failed to load shows.json: {e}")
        # If corrupted, try to recover from backup
        if os.path.exists('shows.json.backup'):
            print("Attempting to restore from backup...")
            try:
                with open('shows.json.backup', 'r') as f:
                    data = json.load(f)
                    print(f"Successfully restored {len(data)} shows from backup")
                    # Save the backup as the main file
                    try:
                        import shutil
                        shutil.copy2('shows.json.backup', 'shows.json')
                        print("Restored backup to shows.json")
                    except Exception as copy_err:
                        print(f"Warning: Could not copy backup to main file: {copy_err}")
                    return data
            except Exception as backup_err:
                print(f"ERROR: Backup is also corrupted: {backup_err}")
        else:
            print("ERROR: No backup file found")
        
        print("CRITICAL: Returning empty dict - all shows data lost!")
        print("Run recover-shows to attempt data recovery")
        return {}

def save_shows(shows):
    """Save shows with file locking to prevent corruption.

    Uses write-in-place (open r+, seek, truncate) instead of atomic rename
    because shows.json is a Docker bind-mounted file and os.rename() over a
    bind mount fails with EBUSY on Linux.  Opening with 'r+' avoids
    truncating the file before the write succeeds.
    """
    # Create backup before writing
    if os.path.exists('shows.json'):
        try:
            with open('shows.json', 'r') as src:
                fcntl.flock(src.fileno(), fcntl.LOCK_SH)
                try:
                    with open('shows.json.backup', 'w') as dst:
                        dst.write(src.read())
                finally:
                    fcntl.flock(src.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            print(f"Warning: Could not create backup: {e}")

    # Serialize first so a JSON error won't leave a truncated file
    payload = json.dumps(shows, indent=4)

    # Open in r+ mode (no truncation on open), then seek+truncate+write
    with open('shows.json', 'r+') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.write(payload)
            f.truncate()          # trim any leftover bytes from a longer previous write
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def load_episodes(show_slug):
    """Load episodes with a shared lock using the JsonDocumentStore."""
    store = get_episodes_store(show_slug)
    data = store.read()
    return data if data is not None else {'episodes': []}

def save_episodes(show_slug, episodes_data):
    """Save episodes using JsonDocumentStore with locking and atomic rename."""
    store = get_episodes_store(show_slug)
    store.write(episodes_data)

def backfill_tracklists_for_show(show_url: str) -> int:
    """Re-parse all stored episodes for a show and refresh tracklists using
    the current scraping/merging logic. Returns number of episodes updated.

    This preserves existing episode metadata (title, date, image_url, url),
    only replacing the `tracklist` and `genres` fields if newly parsed data is
    available. It also keeps the `is_new` flag intact.
    """
    try:
        show_slug = slugify(show_url)
        episodes_data = load_episodes(show_slug)
        episodes = episodes_data.get('episodes', [])
        if not episodes:
            return 0

        updated = 0
        for ep in episodes:
            try:
                ep_url = ep.get('url') or ep.get('audio_url')
                if not ep_url:
                    continue
                # Invalidate caches to force fresh parse/merge with latest logic
                try:
                    cache_service.delete('episode_page', ep_url)
                    cache_service.delete('episode_tracks_api', ep_url)
                except Exception:
                    pass
                page = _fetch_episode_page(ep_url)
                if not page:
                    continue
                tl = page.get('tracklist') or []
                genres = page.get('genres') or []
                if tl:
                    ep['tracklist'] = tl
                    updated += 1
                if genres:
                    ep['genres'] = genres
            except Exception:
                # continue best-effort across episodes
                continue

        save_episodes(show_slug, {'episodes': episodes})
        return updated
    except Exception:
        return 0

def backfill_tracklists_all() -> dict:
    """Backfill tracklists for all stored shows. Returns stats dict."""
    shows = load_shows()
    total_updated = 0
    per_show = {}
    for show_url in list(shows.keys()):
        count = backfill_tracklists_for_show(show_url)
        per_show[show_url] = count
        total_updated += count
    return {'total_updated': total_updated, 'per_show': per_show}

def update_shows():
    """Update shows with only new episodes since last check"""
    shows = load_shows()
    
    for show_url, show_data in shows.items():
        print(f"Updating show: {show_url}")
        show_slug = slugify(show_url)
        
        # Get current episodes
        episodes_data = load_episodes(show_slug)
        existing_episodes = episodes_data['episodes']
        
        # Create lookup set of existing episode URLs
        existing_urls = {ep['audio_url'] for ep in existing_episodes}
        
        # Scrape current show state
        new_show_data = scrape_nts_show(show_url)
        if not new_show_data:
            continue
            
        # Find truly new episodes
        new_episodes = []
        for episode in new_show_data['episodes']:
            if episode['audio_url'] not in existing_urls:
                episode['is_new'] = True
                new_episodes.append(episode)
            else:
                # Preserve existing new status for episodes we already have
                matching_episode = next(
                    (ep for ep in existing_episodes if ep['audio_url'] == episode['audio_url']), 
                    None
                )
                episode['is_new'] = matching_episode['is_new'] if matching_episode else False
        
        # Update episodes file
        episodes_data = {'episodes': new_show_data['episodes']}
        save_episodes(show_slug, episodes_data)
        
        # Update show metadata
        show_data.update({
            'total_episodes': len(new_show_data['episodes']),
            'new_episodes': len(new_episodes),
            'last_updated': datetime.now().isoformat()
        })
    
    save_shows(shows)
    return shows

@with_cache('episode_page', ttl=24 * 3600)  # Cache for 24 hours
def _fetch_episode_page(episode_url):
    """Fetch and parse episode page content with caching"""
    session = create_session()
    try:
        response = session.get(episode_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Parse tracklist
        tracks = []
        tracks_box = soup.select('.tracklist')
        if tracks_box:
            tracks_box = tracks_box[0].find('ul')
            if tracks_box:
                for track in tracks_box.select('li.track'):
                    try:
                        artist = track.select('.track__artist')[0].text.strip()
                        # Clean trailing list punctuation that appears in some templates
                        if artist.endswith(','):
                            artist = artist[:-1].strip()
                        name = track.select('.track__title')[0].text.strip()
                        timestamp_nodes = track.select('.track__timestamp')
                        track_obj = {'artist': artist, 'name': name}
                        if timestamp_nodes:
                            ts = (timestamp_nodes[0].text or '').strip()
                            if ts:
                                track_obj['timestamp'] = ts
                        tracks.append(track_obj)
                    except Exception:
                        continue
        
        print(f"[DEBUG] HTML Parser found {len(tracks)} tracks initially.")
        
        # Try augmenting timestamps via internal APIs if available (AJAX-loaded)
        try:
            api_tracks = _fetch_episode_tracks_api(episode_url) or []
            print(f"[DEBUG] API helper returned {len(api_tracks)} tracks.")
            if api_tracks:
                # Merge by artist+title key with Unicode-aware normalization; fill timestamps, and
                # insert any unmatched API tracks by timestamp so ordering mirrors the broadcast.
                import re

                def _norm_text(s: str) -> str:
                    s = str(s or '').lower()
                    s = re.sub(r"[\u00A0\s]+", " ", s)
                    # unify common punctuation variants across scripts
                    s = (s
                         .replace('，', ',').replace('、', ',')
                         .replace('–', '-').replace('—', '-')
                         .replace('＋', '+').replace('＆', '&'))
                    s = re.sub(r"[\s]*[,;:.]+$", "", s).strip()
                    return s

                def _artist_tokens(s: str):
                    # Split common separators; return unique normalized tokens
                    parts = [p for p in re.split(r"\s*(?:,|&|and)\s*", s) if p]
                    uniq = []
                    seen = set()
                    for p in parts:
                        if p and p not in seen:
                            seen.add(p)
                            uniq.append(p)
                    return uniq

                def track_key(t):
                    title_norm = _norm_text(t.get('name', ''))
                    artist_norm = _norm_text(t.get('artist', ''))
                    token_key = ",".join(sorted(_artist_tokens(artist_norm)))
                    return (token_key, title_norm)

                def ts_to_seconds(ts):
                    if ts is None or ts == '':
                        return None
                    if isinstance(ts, (int, float)):
                        return int(ts)
                    txt = str(ts).strip()
                    try:
                        parts = [int(p) for p in txt.split(':')]
                        if len(parts) == 3:
                            return parts[0]*3600 + parts[1]*60 + parts[2]
                        if len(parts) == 2:
                            return parts[0]*60 + parts[1]
                    except Exception:
                        pass
                    return None

                key_to_api = {track_key(t): dict(t) for t in api_tracks}
                # Build lookup by title for approximate matching (subset tokens)
                title_to_html = {}
                for idx, ht in enumerate(tracks):
                    k = track_key(ht)
                    title_to_html.setdefault(k[1], []).append((idx, set(k[0].split(',')) if k[0] else set()))

                # First pass: fill timestamps on HTML tracks from API matches
                merged = []
                matched = 0
                for t in tracks:
                    k = track_key(t)
                    api_t = key_to_api.get(k)
                    if api_t:
                        if api_t.get('timestamp') and not t.get('timestamp'):
                            t['timestamp'] = api_t['timestamp']
                        matched += 1
                    else:
                        # Approximate match by title and subset of artist tokens
                        try:
                            title_norm = _norm_text(t.get('name', ''))
                            html_token_sets = title_to_html.get(title_norm, [])
                            api_tokens = set(track_key({'artist': api_tracks[0].get('artist','') if False else t.get('artist',''), 'name': t.get('name','')})[0].split(','))  # placeholder to satisfy linter
                        except Exception:
                            api_tokens = set()
                        merged.append(t)

                # If poor match rate but similar lengths, fallback to index-based timestamp fill
                if matched < max(1, len(api_tracks) // 2) and abs(len(tracks) - len(api_tracks)) <= 3:
                    for i in range(min(len(tracks), len(api_tracks))):
                        if 'timestamp' not in tracks[i] and api_tracks[i].get('timestamp'):
                            tracks[i]['timestamp'] = api_tracks[i]['timestamp']

                # Insert unmatched API tracks according to timestamp ordering
                html_keys = {track_key(t) for t in tracks}
                # Filter extras by removing those that equal an HTML row by title and subset/superset of artist tokens
                def is_duplicate_extra(extra):
                    e_key = track_key(extra)
                    title_norm = e_key[1]
                    e_tokens = set(e_key[0].split(',')) if e_key[0] else set()
                    for _, html_tokens in title_to_html.get(title_norm, []):
                        if not html_tokens:
                            continue
                        if e_tokens.issuperset(html_tokens) or html_tokens.issuperset(e_tokens):
                            return True
                    return False

                extras = [t for k, t in key_to_api.items() if (k not in html_keys and not is_duplicate_extra(t))]

                def insert_by_timestamp(base_list, extra):
                    target = ts_to_seconds(extra.get('timestamp'))
                    if target is None:
                        base_list.append(extra)
                        return
                    # find first item with timestamp >= target
                    for idx, existing in enumerate(base_list):
                        existing_ts = ts_to_seconds(existing.get('timestamp'))
                        if existing_ts is not None and existing_ts >= target:
                            base_list.insert(idx, extra)
                            return
                    base_list.append(extra)

                for extra in extras:
                    insert_by_timestamp(merged, extra)

                tracks = merged
                print(f"[DEBUG] Merged tracklist now has {len(tracks)} tracks (matched={matched}, extras={len(extras)}).")

                # Safety fallback: if merge produced too few items compared to API, trust API more
                try:
                    if len(tracks) < max(6, int(0.6 * len(api_tracks))):
                        print(f"[DEBUG] Fallback to API-dominant list (html={len(merged)}, api={len(api_tracks)})")
                        # Deduplicate API list by (artist_token_key,title)
                        seen = set()
                        api_dedupe = []
                        for t in api_tracks:
                            k = track_key(t)
                            if k in seen:
                                continue
                            seen.add(k)
                            api_dedupe.append(t)
                        tracks = api_dedupe
                except Exception:
                    pass
        except Exception as e:
            print(f"[DEBUG] Error during API track merge: {e}")
            pass

        # Parse genres
        genres = []
        
        # Try the original selectors first
        genres_box = soup.select('.episode__genres')
        if not genres_box:
            genres_box = soup.select('.episode-genres')
            
        if genres_box:
            genre_links = genres_box[0].find_all('a', class_='episode-genre')
            if not genre_links:
                genre_links = genres_box[0].find_all('a')
            
            genres = [a.text.strip() for a in genre_links]
        
        # If no genres found with the original selectors, try to find genres in other parts of the page
        if not genres:
            # Look for any links that might contain genre information
            potential_genre_links = soup.select('a[href*="genre"]')
            if potential_genre_links:
                genres = [a.text.strip() for a in potential_genre_links]
            
            # If still no genres, check if there's any metadata that might contain genre info
            if not genres:
                meta_description = soup.select('meta[name="description"]')
                if meta_description:
                    description = meta_description[0].get('content', '')
                    # Extract potential genres from description
                    if 'genres:' in description.lower():
                        genre_part = description.lower().split('genres:')[1].split('.')[0]
                        genres = [g.strip() for g in genre_part.split(',')]
        
        # If still no genres, use a default genre
        if not genres:
            genres = ["Electronic"]  # Default genre
        
        return {
            'tracklist': tracks,
            'genres': genres
        }
        
    except Exception as e:
        print(f"Error fetching episode page {episode_url}: {e}")
        return None
    finally:
        if 'session' in locals():
            session.close()

def _find_tracklist_recursive(data):
    """Recursively search for a list that looks like a tracklist in JSON data."""
    if isinstance(data, list) and all(isinstance(i, dict) for i in data):
        if any(any(k in i for k in ['artist', 'title', 'name', 'track_title']) for i in data):
            return data
    if isinstance(data, dict):
        for key in ['results', 'tracklist', 'data', 'items']:
            if key in data and isinstance(data[key], list):
                found = _find_tracklist_recursive(data[key])
                if found:
                    return found
        for value in data.values():
            found = _find_tracklist_recursive(value)
            if found:
                return found
    return None

@with_cache('episode_tracks_api', ttl=24 * 3600)
def _fetch_episode_tracks_api(episode_url):
    """Attempt to fetch full tracklist with timestamps via NTS API endpoints.

    Returns list of { artist, name, timestamp? } or None.
    """
    try:
        # Derive slug and alias from URL
        # Example: https://www.nts.live/shows/{slug}/episodes/{alias}
        parts = episode_url.rstrip('/').split('/')
        if len(parts) < 2:
            return None
        episode_alias = parts[-1]
        show_slug = parts[-3] if len(parts) >= 3 else ''

        # Known episode endpoint patterns (best-effort). Some deployments expose tracklists here.
        candidate_urls = [
            # Prefer the tracklist endpoint first
            f"https://www.nts.live/api/v2/shows/{show_slug}/episodes/{episode_alias}/tracklist",
            f"https://www.nts.live/api/v2/episodes/{episode_alias}/tracklist",
            f"https://www.nts.live/api/v2/shows/{show_slug}/episodes/{episode_alias}",
            f"https://www.nts.live/api/v2/episodes/{episode_alias}",
        ]

        session = create_session()
        session.headers.update({
            'Accept': 'application/json, text/plain, */*',
            'Referer': episode_url,
            'X-Requested-With': 'XMLHttpRequest'
        })
        print(f"[DEBUG] API Fetch for {episode_url}")
        print(f"[DEBUG] Using cookies: {'yes' if session.headers.get('Cookie') else 'no'}")
        try:
            for api_url in candidate_urls:
                try:
                    print(f"[DEBUG] Trying API URL: {api_url}")
                    resp = session.get(api_url)
                    print(f"[DEBUG] Status code: {resp.status_code}")
                    if resp.status_code != 200:
                        try:
                            snippet = (resp.text or '')[:200].replace('\n', ' ')
                            print(f"[DEBUG] Non-200 response body (first 200 chars): {snippet}")
                        except Exception:
                            pass
                        continue

                    try:
                        data = resp.json()
                        print(f"[DEBUG] Received JSON data (keys): {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                    except json.JSONDecodeError:
                        print("[DEBUG] Failed to decode JSON from response.")
                        continue

                    track_list = _find_tracklist_recursive(data)
                    print(f"[DEBUG] Found tracklist via recursive search (length): {len(track_list) if track_list else 0}")
                    possible_lists = [track_list] if track_list else []
                    normalized = []
                    for lst in possible_lists:
                        if not isinstance(lst, list):
                            continue
                        for t in lst:
                            if not isinstance(t, dict):
                                continue
                            artist = t.get('artist') or t.get('artists') or t.get('track_artist') or ''
                            title = t.get('title') or t.get('name') or t.get('track_title') or ''
                            # Timestamps often appear as 'timestamp', 'time', 'start', 'start_time', or 'position'
                            timestamp = (
                                t.get('timestamp') or t.get('time') or t.get('start_time') or t.get('start') or t.get('position') or t.get('offset') or t.get('offset_estimate')
                            )

                            def format_timestamp(ts):
                                if isinstance(ts, (int, float)):
                                    # Assume seconds from start of the mix
                                    total = int(ts)
                                    h = total // 3600
                                    m = (total % 3600) // 60
                                    s = total % 60
                                    return f"{h:01d}:{m:02d}:{s:02d}" if h else f"{m:01d}:{s:02d}"
                                return str(ts).strip()

                            track_obj = {
                                'artist': str(artist).strip(),
                                'name': str(title).strip()
                            }
                            if timestamp not in (None, ''):
                                track_obj['timestamp'] = format_timestamp(timestamp)
                            if track_obj['artist'] or track_obj['name']:
                                normalized.append(track_obj)
                    if normalized:
                        print(f"[DEBUG] Returning {len(normalized)} normalized tracks from API.")
                        return normalized
                except Exception as e:
                    print(f"[DEBUG] Exception in API loop: {e}")
                    continue
        finally:
            session.close()
        return None
    except Exception:
        return None

def parse_episode_cached(episode_data, show_slug):
    """Parse a single episode from the API response with caching"""
    try:
        # Get the episode alias which contains the date
        episode_alias = episode_data['episode_alias']
        
        # Parse the date
        try:
            date_parts = episode_alias.split('-')[-3:]
            day = date_parts[0].lower().replace('st', '').replace('nd', '').replace('rd', '').replace('th', '')
            month = date_parts[1]
            year = date_parts[2]
            date_obj = datetime.strptime(f"{day} {month} {year}", "%d %B %Y")
            date = date_obj.strftime("%B %d, %Y")
        except Exception:
            date = '-'.join(date_parts).title()

        # Build episode URL
        episode_url = f"https://www.nts.live/shows/{show_slug}/episodes/{episode_alias}"
        
        # Get cached episode page content
        page_data = _fetch_episode_page(episode_url)
        
        if page_data:
            tracks = page_data.get('tracklist', [])
            genres = page_data.get('genres', ["Electronic"])
        else:
            # Fallback if cache fails
            tracks = []
            genres = ["Electronic"]
        
        return {
            'title': episode_data.get('name', ''),
            'date': date,
            'audio_url': episode_url,
            'genres': genres,
            'tracklist': tracks,
            'image_url': episode_data.get('media', {}).get('background_medium', ''),
            'url': episode_url,
            'is_new': True
        }
        
    except Exception as e:
        print(f"Error parsing episode {episode_data.get('name')}: {e}")
        return None

def parse_episode(episode_data, show_slug):
    """Parse a single episode from the API response (legacy function for compatibility)"""
    return parse_episode_cached(episode_data, show_slug)

def add_show(url):
    shows = load_shows()
    if url in shows:
        return False
    
    # Scrape initial show data
    show_data = scrape_nts_show(url)
    if not show_data:
        return False
        
    # Create show metadata entry with initial episodes marked as not new
    for episode in show_data['episodes']:
        episode['is_new'] = False
    
    shows[url] = {
        'title': show_data['title'],
        'description': show_data.get('description', ''),
        'thumbnail': show_data.get('thumbnail') or (show_data['episodes'][0].get('image_url', '') if show_data['episodes'] else ''),
        'total_episodes': len(show_data['episodes']),
        'new_episodes': 0,  # Initialize with 0 new episodes
        'last_updated': datetime.now().isoformat(),
        'first_seen': datetime.now().isoformat(),  # Add first seen timestamp
        'auto_download': False  # Initialize auto_download to False
    }
    
    # Save show metadata
    save_shows(shows)
    
    # Save episodes to separate file
    show_slug = slugify(url)
    episodes_data = {'episodes': show_data['episodes']}
    save_episodes(show_slug, episodes_data)
    
    return True

@with_cache('episodes_api', ttl=1800)  # Cache for 30 minutes
def _fetch_episodes_api(show_slug, limit=5):
    """Fetch episodes from NTS API with caching"""
    api_url = f'https://www.nts.live/api/v2/shows/{show_slug}/episodes?offset=0&limit={limit}'
    
    try:
        session = create_session()
        response = session.get(api_url)
        response.raise_for_status()
        data = response.json()
        return data
    except Exception as e:
        print(f"Error fetching episodes API for {show_slug}: {e}")
        return None

def check_new_episodes(show_url, existing_episodes):
    """Check if a show has new episodes without downloading all data"""
    show_slug = show_url.split('/')[-1]
    
    try:
        # Use cached API response
        data = _fetch_episodes_api(show_slug, limit=5)
        
        if not data or not data.get('results'):
            return []
            
        existing_urls = {ep['audio_url'] for ep in existing_episodes}
        new_episodes = []
        
        for ep in data['results']:
            if ep['status'] != 'published':
                continue
                
            episode_url = f"https://www.nts.live/shows/{show_slug}/episodes/{ep['episode_alias']}"
            if episode_url not in existing_urls:
                episode = parse_episode_cached(ep, show_slug)
                if episode:
                    episode['is_new'] = True
                    new_episodes.append(episode)
            else:
                break  # Found existing episode, stop checking
                
        return new_episodes
        
    except Exception as e:
        print(f"Error checking new episodes: {e}")
        return []
