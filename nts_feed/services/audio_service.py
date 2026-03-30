"""Audio Service for extracting streaming URLs from NTS episode pages.

Includes a TTL cache so that repeated plays of the same episode reuse the
already-extracted URL instead of hitting NTS + SoundCloud/Mixcloud every time.
"""

import logging
import re
import threading
import time
from urllib.parse import urlparse, quote_plus

import requests
from bs4 import BeautifulSoup
import yt_dlp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory TTL cache for extracted audio URLs
# ---------------------------------------------------------------------------
# SoundCloud signed URLs are valid for ~1 hour.  We cache for 45 minutes to
# stay safely within that window while avoiding redundant extractions.
_CACHE_TTL = 45 * 60  # seconds
_cache: dict[str, tuple[dict, float]] = {}
_cache_lock = threading.Lock()
_MAX_CACHE_SIZE = 200


def _cache_get(key: str) -> dict | None:
    """Return cached result if present and not expired."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        result, ts = entry
        if time.time() - ts > _CACHE_TTL:
            del _cache[key]
            return None
        return result


def _cache_set(key: str, result: dict) -> None:
    """Store a result in the cache, evicting oldest entries if full."""
    with _cache_lock:
        # Simple size-cap eviction: drop the oldest entry
        if len(_cache) >= _MAX_CACHE_SIZE and key not in _cache:
            oldest_key = min(_cache, key=lambda k: _cache[k][1])
            del _cache[oldest_key]
        _cache[key] = (result, time.time())


class AudioService:
    """Service for extracting audio streaming URLs from NTS episode pages."""

    @staticmethod
    def extract_streaming_url(episode_url: str) -> dict:
        """Extract direct streaming audio URL from an NTS episode page.

        Results are cached for ~45 minutes so repeated plays are instant.
        """
        # Check cache first
        cached = _cache_get(episode_url)
        if cached is not None:
            logger.debug('Audio URL cache hit for %s', episode_url)
            return cached

        logger.info('Extracting audio URL for: %s', episode_url)

        try:
            # Fetch the episode page
            response = requests.get(episode_url, timeout=15)
            response.raise_for_status()
            logger.debug('Fetched episode page (status %d)', response.status_code)

            soup = BeautifulSoup(response.content, 'html.parser')

            # Prefer SoundCloud when available
            soundcloud_url = AudioService._extract_soundcloud_url(soup)
            if soundcloud_url:
                logger.info('Found SoundCloud URL: %s', soundcloud_url)
                direct_url = AudioService._extract_direct_streaming_url(soundcloud_url, 'soundcloud')
                if direct_url:
                    result = {
                        'streaming_url': direct_url,
                        'original_url': soundcloud_url,
                        'platform': 'soundcloud',
                        'episode_url': episode_url,
                        'success': True,
                    }
                    _cache_set(episode_url, result)
                    return result
                # Fallback to embed
                embed_url = AudioService.get_soundcloud_embed_url(soundcloud_url)
                result = {
                    'streaming_url': embed_url,
                    'original_url': soundcloud_url,
                    'platform': 'soundcloud_embed',
                    'episode_url': episode_url,
                    'success': True,
                }
                _cache_set(episode_url, result)
                return result

            # If no SoundCloud, try Mixcloud
            mixcloud_url = AudioService._extract_mixcloud_url(soup)
            if mixcloud_url:
                logger.info('Found Mixcloud URL: %s', mixcloud_url)
                direct_url = AudioService._extract_direct_streaming_url(mixcloud_url, 'mixcloud')
                if direct_url:
                    result = {
                        'streaming_url': direct_url,
                        'original_url': mixcloud_url,
                        'platform': 'mixcloud',
                        'episode_url': episode_url,
                        'success': True,
                    }
                    _cache_set(episode_url, result)
                    return result
                # Fallback to embed
                embed_url = AudioService.get_mixcloud_embed_url(mixcloud_url)
                result = {
                    'streaming_url': embed_url,
                    'original_url': mixcloud_url,
                    'platform': 'mixcloud_embed',
                    'episode_url': episode_url,
                    'success': True,
                }
                _cache_set(episode_url, result)
                return result

            # Try to find direct audio links
            direct_url = AudioService._extract_direct_audio_url(soup)
            if direct_url:
                result = {
                    'streaming_url': direct_url,
                    'platform': 'direct',
                    'episode_url': episode_url,
                    'success': True,
                }
                _cache_set(episode_url, result)
                return result

            # Last resort: Mixcloud API search
            logger.debug('No direct URLs found, trying Mixcloud API search')
            mixcloud_api_url = AudioService._mixcloud_api_search(episode_url, soup)
            if mixcloud_api_url:
                logger.info('Found via Mixcloud API: %s', mixcloud_api_url)
                direct_url = AudioService._extract_direct_streaming_url(mixcloud_api_url, 'mixcloud')
                if direct_url:
                    result = {
                        'streaming_url': direct_url,
                        'original_url': mixcloud_api_url,
                        'platform': 'mixcloud',
                        'episode_url': episode_url,
                        'success': True,
                    }
                    _cache_set(episode_url, result)
                    return result
                embed_url = AudioService.get_mixcloud_embed_url(mixcloud_api_url)
                result = {
                    'streaming_url': embed_url,
                    'original_url': mixcloud_api_url,
                    'platform': 'mixcloud_embed',
                    'episode_url': episode_url,
                    'success': True,
                }
                _cache_set(episode_url, result)
                return result

            logger.warning('No audio URLs found for %s', episode_url)
            return {
                'streaming_url': None,
                'platform': None,
                'episode_url': episode_url,
                'success': False,
                'error': 'No streaming URL found',
            }

        except Exception as e:
            logger.exception('Audio extraction failed for %s', episode_url)
            return {
                'streaming_url': None,
                'platform': None,
                'episode_url': episode_url,
                'success': False,
                'error': str(e),
            }

    @staticmethod
    def _extract_mixcloud_url(soup):
        """Extract Mixcloud URL from NTS episode page."""
        try:
            mixcloud_buttons = soup.select('.mixcloud-btn')
            if not mixcloud_buttons:
                mixcloud_buttons = soup.select('.episode__btn.mixcloud-btn')
            if not mixcloud_buttons:
                mixcloud_buttons = soup.select('[data-src*="mixcloud"]')

            if mixcloud_buttons:
                mixcloud_url = mixcloud_buttons[0].get('data-src')
                if mixcloud_url and 'mixcloud' in mixcloud_url:
                    return mixcloud_url

            # Look for Mixcloud URLs in script tags
            for script in soup.find_all('script'):
                if script.string:
                    match = re.search(r'https://[^"\']*mixcloud\.com/[^"\']*', script.string)
                    if match:
                        return match.group(0)

            # Look for Mixcloud URLs in href attributes
            for link in soup.find_all('a', href=True):
                if 'mixcloud.com' in link['href']:
                    return link['href']

            return None
        except Exception as e:
            logger.debug('Error extracting Mixcloud URL: %s', e)
            return None

    @staticmethod
    def _extract_soundcloud_url(soup):
        """Extract SoundCloud URL from NTS episode page."""
        try:
            soundcloud_buttons = soup.select('.soundcloud-btn')
            if not soundcloud_buttons:
                soundcloud_buttons = soup.select('[data-src*="soundcloud"]')

            if soundcloud_buttons:
                soundcloud_url = soundcloud_buttons[0].get('data-src')
                if soundcloud_url and ('soundcloud.com' in soundcloud_url or 'on.soundcloud.com' in soundcloud_url):
                    return soundcloud_url

            # Look for SoundCloud URLs in script tags
            for script in soup.find_all('script'):
                if script.string:
                    match = re.search(
                        r'https://[^"\']*(?:soundcloud\.com|on\.soundcloud\.com)/[^"\']*',
                        script.string,
                    )
                    if match:
                        return match.group(0)

            # Look for SoundCloud URLs in href attributes
            for link in soup.find_all('a', href=True):
                href = link['href']
                if 'soundcloud.com' in href or 'on.soundcloud.com' in href:
                    return href

            return None
        except Exception as e:
            logger.debug('Error extracting SoundCloud URL: %s', e)
            return None

    @staticmethod
    def _extract_direct_audio_url(soup):
        """Extract direct audio URL from NTS episode page."""
        try:
            for audio in soup.find_all('audio'):
                src = audio.get('src')
                if src:
                    return src
                for source in audio.find_all('source'):
                    src = source.get('src')
                    if src:
                        return src

            for element in soup.find_all(attrs={'data-audio': True}):
                audio_url = element.get('data-audio')
                if audio_url:
                    return audio_url

            return None
        except Exception as e:
            logger.debug('Error extracting direct audio URL: %s', e)
            return None

    @staticmethod
    def get_mixcloud_embed_url(mixcloud_url):
        """Convert a Mixcloud URL to an embeddable URL."""
        try:
            if not mixcloud_url or 'mixcloud.com' not in mixcloud_url:
                return None
            parsed = urlparse(mixcloud_url)
            path = parsed.path.strip('/')
            return f"https://www.mixcloud.com/widget/iframe/?hide_cover=1&feed=%2F{path}%2F"
        except Exception as e:
            logger.debug('Error creating Mixcloud embed URL: %s', e)
            return mixcloud_url

    @staticmethod
    def _extract_direct_streaming_url(audio_url: str, platform: str) -> str | None:
        """Extract direct streaming URL using yt-dlp.

        Uses quiet mode to suppress the noisy SoundCloud client_id refresh
        tracebacks which are normal yt-dlp behavior.
        """
        try:
            logger.debug('yt-dlp extraction for %s: %s', platform, audio_url)
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'format': (
                    'bestaudio[protocol^=http][ext=mp3]/'
                    'bestaudio[protocol^=http][ext=m4a]/'
                    'bestaudio[protocol^=http]/'
                    'bestaudio[protocol!=dash]/'
                    'best[protocol!=dash]'
                ),
                'socket_timeout': 30,
                'retries': 10,
                'simulate': True,
                'listformats': False,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(audio_url, download=False)

                if not info:
                    logger.debug('yt-dlp returned no info for %s', audio_url)
                    return None

                # Check top-level URL first (yt-dlp resolves best format here)
                if 'url' in info:
                    streaming_url = info['url']
                    if AudioService._is_browser_compatible_url(streaming_url, info):
                        logger.info('Extracted %s direct URL (%s)', platform, streaming_url[:80])
                        return streaming_url

                # Fall through to formats array
                if 'formats' in info:
                    audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
                    compatible = [f for f in audio_formats if AudioService._is_browser_compatible_format(f)]
                    candidates = compatible or audio_formats

                    if candidates:
                        best = max(candidates, key=lambda x: x.get('abr', 0) or 0)
                        streaming_url = best.get('url')
                        if streaming_url and AudioService._is_browser_compatible_url(streaming_url, best):
                            logger.info(
                                'Extracted %s format %s @ %skbps',
                                platform, best.get('format_id'), best.get('abr'),
                            )
                            return streaming_url

            return None
        except Exception as e:
            logger.warning('yt-dlp extraction failed for %s (%s): %s', platform, audio_url, e)
            return None

    @staticmethod
    def get_soundcloud_embed_url(soundcloud_url):
        """Convert a SoundCloud URL to an embeddable URL."""
        try:
            if not soundcloud_url or (
                'soundcloud.com' not in soundcloud_url
                and 'on.soundcloud.com' not in soundcloud_url
            ):
                return None

            # Resolve short links
            try:
                if 'on.soundcloud.com' in soundcloud_url:
                    resp = requests.get(soundcloud_url, allow_redirects=True, timeout=10)
                    if resp.status_code in (200, 301, 302) and resp.url:
                        soundcloud_url = resp.url
            except Exception:
                pass

            widget_base = 'https://w.soundcloud.com/player/'
            params = {
                'url': soundcloud_url,
                'color': '#ff5500',
                'auto_play': 'false',
                'hide_related': 'false',
                'show_comments': 'true',
                'show_user': 'true',
                'show_reposts': 'false',
                'show_teaser': 'true',
                'visual': 'true',
            }
            query = '&'.join(f"{k}={quote_plus(v)}" for k, v in params.items())
            return f"{widget_base}?{query}"
        except Exception as e:
            logger.debug('Error creating SoundCloud embed URL: %s', e)
            return soundcloud_url

    @staticmethod
    def _mixcloud_api_search(episode_url, soup):
        """Search Mixcloud API for the episode (like downloader's mixcloud_try)."""
        try:
            title_elements = soup.select('div.episode__header h1')
            if not title_elements:
                title_elements = soup.select('h1')
            if not title_elements:
                return None

            title = title_elements[0].text.strip()

            date_elements = soup.select('span.bio__broadcast-date')
            if not date_elements:
                return None

            date_str = date_elements[0].text.strip()

            import datetime
            try:
                date_obj = datetime.datetime.strptime(date_str, '%d.%m.%y')
            except ValueError:
                logger.debug('Could not parse date: %s', date_str)
                return None

            day = date_obj.strftime('%d')
            if 10 <= int(day) <= 20:
                suffix = 'th'
            else:
                last_digit = int(day) % 10
                suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(last_digit, 'th')

            search_title = f"{title} - {day}{suffix}{date_obj.strftime(' %B %Y')}"
            query = re.sub(r'[-/]', '', search_title)
            query = re.sub(r'\s+', '+', query)
            api_url = f"https://api.mixcloud.com/search/?q={query}&type=cloudcast"

            headers = {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/91.0.4472.124 Safari/537.36'
                ),
            }

            response = requests.get(api_url, headers=headers, timeout=10)
            if response.status_code != 200:
                return None

            data = response.json()
            if not data.get('data'):
                return None

            nts_results = [
                r for r in data['data']
                if r.get('user', {}).get('username') == 'NTSRadio'
            ]

            for result in nts_results:
                if result.get('name') == search_title:
                    return result.get('url')

            if nts_results:
                return nts_results[0].get('url')

            return None
        except Exception as e:
            logger.debug('Mixcloud API search error: %s', e)
            return None

    @staticmethod
    def _is_browser_compatible_format(format_info: dict) -> bool:
        """Check if a yt-dlp format is compatible with HTML5 audio."""
        if not format_info:
            return False

        protocol = (format_info.get('protocol') or '').lower()
        if protocol == 'dash' or 'dash' in protocol:
            return False

        ext = (format_info.get('ext') or '').lower()
        if ext not in ('mp3', 'm4a', 'aac', 'ogg', 'wav', 'webm'):
            return False

        format_id = (format_info.get('format_id') or '').lower()
        if 'dash' in format_id:
            return False

        return True

    @staticmethod
    def _is_browser_compatible_url(url: str, format_info: dict | None = None) -> bool:
        """Check if a streaming URL is compatible with HTML5 audio."""
        if not url:
            return False

        url_lower = url.lower()

        if url_lower.endswith('.mpd') or '.mpd?' in url_lower:
            return False
        if url_lower.endswith('.m3u8') or '.m3u8?' in url_lower:
            return False
        if '/dash' in url_lower or 'dash_' in url_lower:
            return False

        if format_info:
            return AudioService._is_browser_compatible_format(format_info)

        supported_patterns = ['.mp3', '.m4a', '.aac', '.ogg', '.wav']
        return any(pattern in url_lower for pattern in supported_patterns)
