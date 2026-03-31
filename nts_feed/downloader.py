import datetime
import logging
import os
import re
import sys
import urllib
import json
import threading
import time

import requests
from yt_dlp import YoutubeDL
from cssutils import parseStyle
from bs4 import BeautifulSoup
import music_tag

from .runtime_paths import downloads_dir
from .track_manager import TrackManager

__version__ = '1.3.4'

# Configure module logger
logger = logging.getLogger(__name__)


def _create_http_session():
    """Create a reusable requests session with connection pooling and retry."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36'
    })
    return s


# Module-level session for connection reuse across downloads
_http_session = _create_http_session()


class YtDlpLogger:
    """Custom logger for yt-dlp that filters verbose progress output."""
    
    def __init__(self):
        self._last_frag_log_time = 0
        self._log_interval = 5  # Log progress every 5 seconds max
    
    def debug(self, msg):
        # Filter out fragment download progress spam
        if msg.startswith('[download]') and 'frag' in msg:
            return  # Suppress fragment progress entirely
        if msg.startswith('[download]') and '%' in msg:
            return  # Suppress percentage progress (handled by hook)
        logger.debug(msg)
    
    def info(self, msg):
        logger.info(msg)
    
    def warning(self, msg):
        logger.warning(msg)
    
    def error(self, msg):
        logger.error(msg)


class ProgressThrottler:
    """Throttles progress logging to reduce log spam."""
    
    def __init__(self, interval_seconds: float = 2.0, percent_threshold: float = 5.0):
        self._last_log_time = 0
        self._last_logged_percent = -percent_threshold
        self._interval = interval_seconds
        self._threshold = percent_threshold
    
    def should_log(self, percent: float) -> bool:
        """Returns True if progress should be logged based on time or percentage change."""
        now = time.time()
        time_elapsed = now - self._last_log_time >= self._interval
        percent_changed = percent - self._last_logged_percent >= self._threshold
        
        if time_elapsed or percent_changed:
            self._last_log_time = now
            self._last_logged_percent = percent
            return True
        return False
    
    def reset(self):
        """Reset throttler state for a new download."""
        self._last_log_time = 0
        self._last_logged_percent = -self._threshold

class DownloadManager:
    def __init__(self):
        self._cancel_events = {}
        self._lock = threading.Lock()
        self._active_downloads = set()

    def create_cancel_event(self, download_id):
        with self._lock:
            self._cancel_events[download_id] = threading.Event()
            return self._cancel_events[download_id]

    def get_cancel_event(self, download_id):
        with self._lock:
            return self._cancel_events.get(download_id)

    def remove_cancel_event(self, download_id):
        with self._lock:
            self._cancel_events.pop(download_id, None)

    def cancel_download(self, download_id):
        with self._lock:
            if download_id in self._cancel_events:
                self._cancel_events[download_id].set()

    def cancel_all_downloads(self):
        """Cancel all active downloads"""
        with self._lock:
            for download_id in list(self._cancel_events.keys()):
                self.cancel_download(download_id)

    def cleanup(self):
        """Clean up all downloads and resources"""
        self.cancel_all_downloads()
        with self._lock:
            self._cancel_events.clear()
            self._active_downloads.clear()

download_manager = DownloadManager()

# At the module level, create a single instance
track_manager = TrackManager()

def get_suffix(day):
    if 10 <= day % 100 <= 20:
        suffix = 'th'
    else:
        last_digit = day % 10
        if last_digit == 1:
            suffix = 'st'
        elif last_digit == 2:
            suffix = 'nd'
        elif last_digit == 3:
            suffix = 'rd'
        else:
            suffix = 'th'
    return suffix

def mixcloud_try(parsed):
    try:
        day = parsed['date'].strftime('%d')
        day += get_suffix(int(day))
        title = parsed['title'] + ' - ' + day + parsed['date'].strftime(' %B %Y')
        query = re.sub(r'[-/]', '', title)
        query = re.sub(r'\s+', '+', query)
        query = "https://api.mixcloud.com/search/?q=" + query + "&type=cloudcast"
        
        # Add headers to mimic a browser request
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        reply = _http_session.get(query, headers=headers)
        
        if reply.status_code != 200:
            logger.debug(f"Mixcloud API error: Status {reply.status_code}")
            return None
            
        data = reply.json()
        if not data.get('data'):
            logger.debug("No results found in Mixcloud search")
            return None
            
        reply = data['data']
        reply = list(filter(lambda x: x['user']['username'] == 'NTSRadio', reply))
        
        for resp in reply:
            if resp['name'] == title:
                return resp['url']
                
        return None
        
    except Exception as e:
        logger.debug(f"Mixcloud search error: {e}")
        return None

def download(url, quiet, save_dir, save=True, progress_callback=None, download_id=None):
    logger.info(f"Starting download for: {url}")
    
    try:
        nts_url = url
        page = _http_session.get(url).content
        bs = BeautifulSoup(page, 'html.parser')
        
        # Parse NTS data first
        parsed = parse_nts_data(bs)
        parsed['url'] = nts_url

        # Get mixcloud button and link - add error handling here
        mixcloud_buttons = bs.select('.mixcloud-btn')
        logger.debug(f"Found {len(mixcloud_buttons)} mixcloud buttons")
        if not mixcloud_buttons:
            logger.debug("No mixcloud button found, trying alternative selectors")
            # Try alternative selectors
            mixcloud_buttons = bs.select('.episode__btn.mixcloud-btn')
            if not mixcloud_buttons:
                raise ValueError("Could not find mixcloud button")
        
        button = mixcloud_buttons[0]
        link = button.get('data-src')
        logger.debug(f"Mixcloud link found: {link}")
        
        host = None

        if 'https://mixcloud' not in link:
            logger.debug("Trying mixcloud API...")
            mixcloud_url = mixcloud_try(parsed)
            if mixcloud_url:
                link = mixcloud_url
                host = 'mixcloud'
                logger.debug(f"Found mixcloud URL via API: {link}")

        if 'https://mixcloud' in link:
            host = 'mixcloud'
        elif 'https://soundcloud' in link:
            host = 'soundcloud'
        
        logger.debug(f"Host platform: {host}")

        # Get album art
        page = _http_session.get(link).content
        bs = BeautifulSoup(page, 'html.parser')
        image_type = ''
        image = None

        if host == 'mixcloud' and len(bs.select('div.album-art')) != 0:
            img = bs.select('div.album-art')[0].img
            srcset = img.get('srcset').split()
            img = srcset[-2].split(',')[1]
            image = urllib.request.urlopen(img)
            image_type = image.info().get_content_type()
            image = image.read()
        elif host == 'soundcloud' and len(bs.select('span.image__full')) != 0:
            style = parseStyle(bs.select('.image__full')[0].get('style'))
            image = urllib.request.urlopen(style['background-image'])
            image_type = image.info().get_content_type()
            image = image.read()

        if image is None and len(parsed['image_url']) > 0:
            if '/resize/' in parsed['image_url']:
                parsed['image_url'] = re.sub(r'/resize/\d+x\d+/',
                                           '/resize/1000x1000/',
                                           parsed['image_url'])
            image = urllib.request.urlopen(parsed["image_url"])
            image_type = image.info().get_content_type()
            image = image.read()

        # Rest of your existing download function code...
        file_name = f'{parsed["safe_title"]} - {parsed["date"].year}-{parsed["date"].month}-{parsed["date"].day}'

        cancel_event = None
        partial_files = []
        
        if download_id:
            cancel_event = download_manager.create_cancel_event(download_id)

        # Progress throttler for reduced log spam
        progress_throttler = ProgressThrottler(interval_seconds=3.0, percent_threshold=10.0)
        
        try:
            def hook(d):
                if cancel_event and cancel_event.is_set():
                    for partial in partial_files:
                        try:
                            if os.path.exists(partial):
                                os.remove(partial)
                        except Exception as e:
                            logger.warning(f"Error removing partial file {partial}: {e}")
                    raise Exception("Download cancelled")
                
                if d['status'] == 'downloading':
                    try:
                        if 'filename' in d and d['filename'].endswith('.part'):
                            partial_files.append(d['filename'])
                        
                        percent_str = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_percent_str', '0.0%'))
                        percent = float(percent_str.replace('%', ''))
                        
                        # Throttled console logging - only log significant progress changes
                        if progress_throttler.should_log(percent):
                            speed = d.get('speed', 0)
                            eta = d.get('eta', 0)
                            speed_str = f"{speed / 1024 / 1024:.1f} MiB/s" if speed else "N/A"
                            eta_str = f"{eta}s" if eta else "N/A"
                            logger.info(f"Download progress: {percent:.0f}% | Speed: {speed_str} | ETA: {eta_str}")
                        
                        # Always call the UI progress callback (it handles its own throttling)
                        if progress_callback:
                            progress_callback({
                                'status': 'progress',
                                'percent': percent,
                                'speed': d.get('speed', 0),
                                'eta': d.get('eta', 0),
                                'downloaded_bytes': d.get('downloaded_bytes', 0),
                                'total_bytes': d.get('total_bytes', 0)
                            })
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Progress parsing error: {e}")
                
                elif d['status'] == 'finished':
                    logger.info("Download finished, processing file...")
                    progress_throttler.reset()

            if save:
                ydl_opts = {
                    'quiet': True,  # Suppress default output
                    'no_warnings': quiet,  # Suppress warnings if quiet mode
                    'noprogress': True,  # Disable built-in progress bar (we use hook instead)
                    'logger': YtDlpLogger(),  # Use custom logger to filter output
                    'progress_hooks': [hook],
                    'outtmpl': os.path.join(save_dir, f'{file_name}.%(ext)s'),
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'm4a',
                    }],
                    'socket_timeout': 30,
                    'retries': 10,
                }

                with YoutubeDL(ydl_opts) as ydl:
                    try:
                        ydl.download([link])
                    except Exception as e:
                        if 'Download cancelled' in str(e):
                            return None
                        raise e

                # Set metadata after successful download
                files = os.listdir(save_dir)
                for file in files:
                    if file.startswith(file_name) and file.endswith('.m4a'):
                        file_path = os.path.join(save_dir, file)
                        set_metadata(file_path, parsed, image, image_type)
                        
                        # Move file to Music library
                        try:
                            track_manager.move_to_music_library(file_path)
                        except Exception as e:
                            logger.warning(f"Error moving file to Music library: {e}")
                            # Continue with normal flow even if move fails
                        break

                # Update track database
                track_manager.add_downloaded_episode(
                    episode_url=url,
                    title=get_title(parsed),
                    artist=get_artists(parsed),
                    filename=os.path.basename(file_path)
                )

        except Exception as e:
            if 'Download cancelled' not in str(e):
                logger.error(f"Download error: {e}")
            raise e
        finally:
            if download_id:
                download_manager.remove_cancel_event(download_id)

        logger.info(f"Download completed successfully: {parsed['title']}")
        return parsed

    except Exception as e:
        logger.exception(f"Download failed for {url}: {e}")
        raise


def parse_nts_data(bs):
    logger.debug("Parsing NTS episode data")
    
    try:
        # Find the title box using episode__header
        title_box = bs.select('div.episode__header')
        logger.debug(f"Found {len(title_box)} title box elements")
        
        if not title_box:
            logger.error("No title box found in page")
            raise ValueError("Could not find episode header (div.episode__header)")
        title_box = title_box[0]

        # Parse title and get safe version
        title, safe_title = parse_title(title_box)
        logger.debug(f"Parsed title: {title}")

        # Parse artists in the title
        artists, parsed_artists = parse_artists(title, bs)
        logger.debug(f"Found {len(artists)} artists")

        # Station parsing
        station_span = bs.select('span.bio__broadcast-location')
        if not station_span:
            station = 'London'
        else:
            station = station_span[0].text.strip()
        logger.debug(f"Station: {station}")

        # Image URL parsing
        bg_tag = bs.select('img.profile-image__img')
        image_url = bg_tag[0].get('src') if bg_tag else ''

        # Date parsing (support multiple formats seen on NTS)
        date_span = bs.select('span.bio__broadcast-date')
        if not date_span:
            raise ValueError("Could not find broadcast date")
        raw_date = (date_span[0].text or '').strip()

        def _clean_date(s: str) -> str:
            # Remove ordinal suffixes like '1st', '2nd', '3rd', '4th'
            return re.sub(r'\b(\d{1,2})(st|nd|rd|th)\b', r'\1', s)

        parsed_date = None
        cleaned = _clean_date(raw_date)
        date_formats = [
            '%d.%m.%y',
            '%d.%m.%Y',
            '%d %b %Y',      # 30 Aug 2025
            '%d %B %Y',      # 30 August 2025
            '%d-%m-%Y',
            '%Y-%m-%d',
        ]
        for fmt in date_formats:
            try:
                parsed_date = datetime.datetime.strptime(cleaned, fmt)
                break
            except Exception:
                continue
        if not parsed_date:
            # Final fallback: try parsing common patterns by replacing separators
            try:
                alt = cleaned.replace('/', '.').replace('-', ' ')
                for fmt in ['%d %m %Y', '%d %m %y']:
                    try:
                        parsed_date = datetime.datetime.strptime(alt, fmt)
                        break
                    except Exception:
                        continue
            except Exception:
                pass
        if not parsed_date:
            raise ValueError(f"Unrecognized broadcast date format: '{raw_date}'")
        date = parsed_date
        logger.debug(f"Parsed date: {date}")

        # Parse genres and tracklist
        genres = parse_genres(bs)
        logger.debug(f"Found {len(genres)} genres")

        try:
            tracks = parse_tracklist(bs)
            logger.debug(f"Found {len(tracks)} tracks")
        except Exception as e:
            logger.error(f"Error parsing tracklist: {e}")
            raise
        
        return {
            'safe_title': safe_title,
            'date': date,
            'title': title,
            'artists': artists,
            'parsed_artists': parsed_artists,
            'genres': genres,
            'station': station,
            'tracks': tracks,
            'image_url': image_url,
        }

    except Exception as e:
        logger.exception(f"Error parsing NTS data: {e}")
        raise


def parse_tracklist(bs):
    # tracklist
    tracks = []
    tracks_box = bs.select('.tracklist')[0]
    if tracks_box:
        tracks_box = tracks_box.ul
        if tracks_box:
            tracks_list = tracks_box.select('li.track')
            for track in tracks_list:
                artist = track.select('.track__artist')[0].text.strip()
                name = track.select('.track__title')[0].text.strip()
                tracks.append({'artist': artist, 'name': name})
    return tracks


def parse_genres(bs):
    # genres
    genres = []
    genres_box = bs.select('.episode__genres')[0]
    for anchor in genres_box.find_all('a'):
        genres.append(anchor.text.strip())
    return genres


def parse_artists(title, bs):
    # parse artists in the title
    parsed_artists = re.findall(r'(?:w\/|with)(.+?)(?=and|,|&|\s-\s)', title,
                                re.IGNORECASE)
    if not parsed_artists:
        parsed_artists = re.findall(r'(?:w\/|with)(.+)', title, re.IGNORECASE)
    # strip all
    parsed_artists = [x.strip() for x in parsed_artists]
    # get other artists after the w/
    if parsed_artists:
        more_people = re.sub(r'^.+?(?:w\/|with)(.+?)(?=and|,|&|\s-\s)', '',
                             title, re.IGNORECASE)
        if more_people == title:
            # no more people
            more_people = ''
        if not re.match(r'^\s*-\s', more_people):
            # split if separators are encountered
            more_people = re.split(r',|and|&', more_people, re.IGNORECASE)
            # append to array
            if more_people:
                for mp in more_people:
                    mp.strip()
                    parsed_artists.append(mp)
    parsed_artists = list(filter(None, parsed_artists))
    # artists
    artists = []
    artist_box = bs.select('.bio-artists')
    if artist_box:
        artist_box = artist_box[0]
        for anchor in artist_box.find_all('a'):
            artists.append(anchor.text.strip())
    return artists, parsed_artists


def parse_title(title_box):
    h1_elements = title_box.select('h1')
    if not h1_elements:
        raise ValueError("No h1 element found in title box")
    
    title = h1_elements[0].text.strip()
    
    # remove unsafe characters for the FS
    safe_title = re.sub(r'\/|\:', '-', title)
    
    return title, safe_title


def get_episodes_of_show(show_name):
    offset = 0
    count = 0
    output = []
    while True:
        api_url = f'https://www.nts.live/api/v2/shows/{show_name}/episodes?offset={offset}'
        res = _http_session.get(api_url)
        try:
            res = res.json()
        except json.decoder.JSONDecodeError as e:
            logger.error(f'Error parsing API response JSON: {e}')
            raise
        if count == 0:
            count = int(res['metadata']['resultset']['count'])
        offset += int(res['metadata']['resultset']['limit'])
        if res['results']:
            res = res['results']
            for ep in res:
                if ep['status'] == 'published':
                    alias = ep['episode_alias']
                    output.append(
                        f'https://www.nts.live/shows/{show_name}/episodes/{alias}'
                    )
        if len(output) == count:
            break

    return output

def get_title(parsed):
    return f'{parsed["title"]} - {parsed["date"].day:02d}.{parsed["date"].month:02d}.{parsed["date"].year:02d}'

def get_tracklist(parsed):
    return '\n'.join(list(map(lambda x: f'{x["name"]} by {x["artist"]}', parsed['tracks'])))

def get_date(parsed):
    return f'{parsed["date"].date().isoformat()}'

def get_genres(parsed):
    return '; '.join(parsed['genres'])

def get_artists(parsed):
    join_artists = parsed['artists'] + parsed['parsed_artists']
    all_artists = []
    presence_set = set()
    for aa in join_artists:
        al = aa.lower()
        if al not in presence_set:
            presence_set.add(al)
            all_artists.append(aa)
    return "; ".join(all_artists)

def set_metadata(file_path, parsed, image, image_type):
    f = music_tag.load_file(file_path)

    f['title'] = get_title(parsed)
    f['compilation'] = 1
    f['album'] = 'NTS'
    f['artist'] = get_artists(parsed)
    f.raw['year'] = get_date(parsed)
    f['genre'] = get_genres(parsed)
    tracklist = get_tracklist(parsed)
    if tracklist:
        f['lyrics'] = "Tracklist:\n" + get_tracklist(parsed)
    f['artwork'] = image
    f['comment'] = parsed['url']

    f.save()

def main():
    # Use the global track_manager and correct method name
    track_manager.scan_directory()
    
    # Check command line arguments
    if len(sys.argv) < 2:
        print("Usage: python downloader.py <url_or_file> [download_directory]")
        sys.exit(1)

    # Get input file/URL
    arg = sys.argv[1]
    
    # Set download directory (either from args or default)
    download_dir = sys.argv[2] if len(sys.argv) > 2 else str(downloads_dir())
    os.makedirs(download_dir, exist_ok=True)

    # Process input
    if os.path.exists(arg):
        with open(arg, 'r') as f:
            lines = f.read().splitlines()
        for line in lines:
            download(line, False, download_dir)
    else:
        download(arg, False, download_dir)

if __name__ == '__main__':
    main()
