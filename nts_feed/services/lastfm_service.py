import requests
import os
import logging
import hashlib
import time
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional
from dotenv import load_dotenv
# Import the DiscogsService
from .discogs_service import DiscogsService

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class LastFmService:
    """
    Service for interacting with the Last.fm API with caching and parallel processing
    """
    
    def __init__(self):
        self.api_key = os.getenv('LASTFM_API_KEY')
        self.api_secret = os.getenv('LASTFM_API_SECRET')
        self.base_url = 'https://ws.audioscrobbler.com/2.0/'
        self.user_agent = 'NTSFeed/1.0 (https://github.com/avxe/nts-feed)'
        # Initialize the DiscogsService for fetching images and album information
        self.discogs_service = DiscogsService()
        
        # Setup caching
        self.cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'cache', 'lastfm')
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Cache TTL settings (in seconds)
        self.cache_ttl = {
            'artist_info': 24 * 3600,      # 24 hours
            'track_info': 12 * 3600,       # 12 hours  
            'similar_artists': 24 * 3600,  # 24 hours
            'artist_tags': 24 * 3600,      # 24 hours
            'artist_image': 7 * 24 * 3600, # 7 days
            'similar_tags': 7 * 24 * 3600, # 7 days - genre relationships don't change often
        }
        
        # Thread pool for parallel API calls
        self.executor = ThreadPoolExecutor(
            max_workers=int(os.getenv('LASTFM_MAX_WORKERS', '3'))
        )
        
        if not self.api_key:
            logger.warning("Last.fm API key not found in environment variables")
    
    def _get_cache_key(self, cache_type: str, *args) -> str:
        """Generate a cache key from the cache type and arguments"""
        key_data = f"{cache_type}:{':'.join(str(arg) for arg in args)}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key: str) -> str:
        """Get the full path for a cache file"""
        return os.path.join(self.cache_dir, f"{cache_key}.pkl")
    
    def _is_cache_valid(self, cache_path: str, cache_type: str) -> bool:
        """Check if cache file exists and is still valid"""
        if not os.path.exists(cache_path):
            return False
        
        file_age = time.time() - os.path.getmtime(cache_path)
        ttl = self.cache_ttl.get(cache_type, 3600)  # Default 1 hour
        return file_age < ttl
    
    def _get_cached_data(self, cache_type: str, *args) -> Optional[any]:
        """Get data from cache if valid"""
        try:
            cache_key = self._get_cache_key(cache_type, *args)
            cache_path = self._get_cache_path(cache_key)
            
            if self._is_cache_valid(cache_path, cache_type):
                with open(cache_path, 'rb') as f:
                    logger.debug(f"Cache hit for {cache_type}:{args}")
                    return pickle.load(f)
        except Exception as e:
            logger.warning(f"Cache read error for {cache_type}:{args} - {e}")
        
        return None
    
    def _set_cached_data(self, cache_type: str, data: any, *args) -> None:
        """Store data in cache"""
        try:
            cache_key = self._get_cache_key(cache_type, *args)
            cache_path = self._cache_path_for_key(cache_key)
            
            with open(cache_path, 'wb') as f:
                pickle.dump(data, f)
                logger.debug(f"Cache stored for {cache_type}:{args}")
        except Exception as e:
            logger.warning(f"Cache write error for {cache_type}:{args} - {e}")
    
    def _cache_path_for_key(self, cache_key: str) -> str:
        return os.path.join(self.cache_dir, f"{cache_key}.pkl")
    
    def get_artist_image_from_commons(self, mbid: str) -> Optional[str]:
        """
        Get artist image from Wikimedia Commons using MusicBrainz ID
        
        Args:
            mbid: MusicBrainz ID of the artist
            
        Returns:
            URL of the artist image or None if not found
        """
        if not mbid:
            return None
            
        try:
            # Step 1: Get the artist from MusicBrainz API including URL relationships
            mb_headers = {'User-Agent': self.user_agent}
            mb_url = f"https://musicbrainz.org/ws/2/artist/{mbid}?inc=url-rels&fmt=json"
            
            mb_response = requests.get(mb_url, headers=mb_headers)
            mb_response.raise_for_status()
            mb_data = mb_response.json()
            
            # Step 2: Find Wikidata URL
            wikidata_url = None
            if 'relations' in mb_data:
                for relation in mb_data['relations']:
                    if relation.get('type') == 'wikidata' and 'url' in relation and 'resource' in relation['url']:
                        wikidata_url = relation['url']['resource']
                        break
            
            if not wikidata_url:
                return None
                
            # Extract Wikidata ID from URL
            wikidata_id = wikidata_url.split('/')[-1]
            
            # Step 3: Get Wikidata item
            wd_url = f"https://www.wikidata.org/wiki/Special:EntityData/{wikidata_id}.json"
            wd_response = requests.get(wd_url)
            wd_response.raise_for_status()
            wd_data = wd_response.json()
            
            # Step 4: Find Commons category or image
            commons_image = None
            
            if 'entities' in wd_data and wikidata_id in wd_data['entities']:
                entity = wd_data['entities'][wikidata_id]
                
                # Check for image property (P18)
                if 'claims' in entity and 'P18' in entity['claims']:
                    image_claim = entity['claims']['P18'][0]
                    if 'mainsnak' in image_claim and 'datavalue' in image_claim['mainsnak']:
                        image_name = image_claim['mainsnak']['datavalue']['value']
                        
                        # Step 5: Get image URL from Commons
                        commons_url = "https://commons.wikimedia.org/w/api.php"
                        commons_params = {
                            'action': 'query',
                            'titles': f"File:{image_name}",
                            'prop': 'imageinfo',
                            'iiprop': 'url',
                            'format': 'json'
                        }
                        
                        commons_response = requests.get(commons_url, params=commons_params)
                        commons_response.raise_for_status()
                        commons_data = commons_response.json()
                        
                        # Extract image URL
                        if 'query' in commons_data and 'pages' in commons_data['query']:
                            for page_id in commons_data['query']['pages']:
                                page = commons_data['query']['pages'][page_id]
                                if 'imageinfo' in page and len(page['imageinfo']) > 0:
                                    commons_image = page['imageinfo'][0]['url']
                                    break
            
            return commons_image
            
        except Exception as e:
            logger.error(f"Error fetching artist image from Commons: {str(e)}")
            return None
    
    def get_artist_image_from_discogs(self, artist_name: str) -> Optional[str]:
        """
        Get artist image from Discogs with caching
        
        Args:
            artist_name: Name of the artist
            
        Returns:
            URL of the artist image or None if not found
        """
        # Check cache first
        cached_image = self._get_cached_data('artist_image', artist_name)
        if cached_image is not None:
            return cached_image
        
        try:
            # Search for the artist on Discogs
            artist_results = self.discogs_service.search_artist(artist_name, limit=1)
            
            if not artist_results:
                logger.warning(f"No artist found on Discogs for: {artist_name}")
                # Cache the negative result to avoid repeated calls
                self._set_cached_data('artist_image', None, artist_name)
                return None
                
            # Get the first result
            artist_result = artist_results[0]
            image_url = None
            
            # Check if there's a cover image
            if 'cover_image' in artist_result and artist_result['cover_image']:
                image_url = artist_result['cover_image']
            else:
                # If no cover image, try to get the artist detail
                if 'id' in artist_result:
                    artist_detail = self.discogs_service.get_artist_detail(artist_result['id'])
                    if artist_detail and 'images' in artist_detail and artist_detail['images']:
                        # Get the primary image or the first available image
                        for image in artist_detail['images']:
                            if image.get('type') == 'primary':
                                image_url = image['uri']
                                break
                        # If no primary image, return the first one
                        if not image_url and artist_detail['images']:
                            image_url = artist_detail['images'][0]['uri']
            
            # Cache the result (even if None)
            self._set_cached_data('artist_image', image_url, artist_name)
            return image_url
            
        except Exception as e:
            logger.error(f"Error fetching artist image from Discogs: {e}")
            # Cache the error result to avoid repeated failures
            self._set_cached_data('artist_image', None, artist_name)
            return None
    
    def get_similar_artists(self, artist_name: str, limit: int = 6) -> List[Dict]:
        """
        Get similar artists from Last.fm API with caching and parallel processing
        
        Args:
            artist_name: Name of the artist
            limit: Maximum number of similar artists to return (reduced from 9 to 6)
            
        Returns:
            List of similar artists with their information
        """
        # Check cache first
        cached_artists = self._get_cached_data('similar_artists', artist_name, limit)
        if cached_artists is not None:
            return cached_artists
            
        if not self.api_key:
            logger.error("Last.fm API key not available")
            return []
            
        try:
            params = {
                'method': 'artist.getSimilar',
                'artist': artist_name,
                'limit': limit,
                'api_key': self.api_key,
                'format': 'json'
            }
            
            response = requests.get(self.base_url, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if 'similarartists' not in data or 'artist' not in data['similarartists']:
                logger.warning(f"No similar artists found for {artist_name}")
                # Cache empty result
                self._set_cached_data('similar_artists', [], artist_name, limit)
                return []
                
            artists_data = data['similarartists']['artist'][:limit]
            
            # Process artists in parallel using ThreadPoolExecutor
            similar_artists = []
            
            def process_artist(artist):
                """Process a single artist in parallel"""
                try:
                    mbid = artist.get('mbid')
                    
                    # Get artist image from Discogs (cached)
                    image_url = self.get_artist_image_from_discogs(artist['name'])
                    
                    # Get artist top tags (cached)
                    tags = self.get_artist_top_tags(artist['name'], limit=3)  # Reduced from 5 to 3
                    
                    return {
                        'name': artist['name'],
                        'url': artist['url'],
                        'match': float(artist['match']) * 100,  # Convert to percentage
                        'mbid': mbid,
                        'image': image_url,
                        'tags': tags
                    }
                except Exception as e:
                    logger.error(f"Error processing similar artist {artist.get('name', 'unknown')}: {e}")
                    return None
            
            # Submit all tasks to thread pool
            futures = {self.executor.submit(process_artist, artist): artist for artist in artists_data}
            
            # Collect results as they complete, maintaining order
            artist_results = [None] * len(artists_data)
            for future in as_completed(futures):
                artist = futures[future]
                try:
                    result = future.result(timeout=5)  # 5 second timeout per artist
                    if result:
                        # Find the original index to maintain order
                        original_index = next(i for i, a in enumerate(artists_data) if a['name'] == artist['name'])
                        artist_results[original_index] = result
                except Exception as e:
                    logger.error(f"Error getting result for artist {artist.get('name', 'unknown')}: {e}")
            
            # Filter out None results and maintain order
            similar_artists = [result for result in artist_results if result is not None]
            
            # Cache the results
            self._set_cached_data('similar_artists', similar_artists, artist_name, limit)
            
            return similar_artists
            
        except Exception as e:
            logger.error(f"Error fetching similar artists: {e}")
            # Cache empty result on error
            self._set_cached_data('similar_artists', [], artist_name, limit)
            return []
    
    def get_artist_top_tags(self, artist_name: str, limit: int = 5) -> List[str]:
        """
        Get top tags for an artist from Last.fm API with caching
        
        Args:
            artist_name: Name of the artist
            limit: Maximum number of tags to return
            
        Returns:
            List of tag names
        """
        # Check cache first
        cached_tags = self._get_cached_data('artist_tags', artist_name, limit)
        if cached_tags is not None:
            return cached_tags
            
        if not self.api_key:
            logger.error("Last.fm API key not available")
            return []
            
        try:
            params = {
                'method': 'artist.getTopTags',
                'artist': artist_name,
                'api_key': self.api_key,
                'format': 'json'
            }
            
            response = requests.get(self.base_url, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if 'toptags' not in data or 'tag' not in data['toptags']:
                # Cache empty result
                self._set_cached_data('artist_tags', [], artist_name, limit)
                return []
                
            # Extract tag names and filter out empty tags
            tags = [tag['name'] for tag in data['toptags']['tag'] if tag.get('name')]
            
            # Return only the top 'limit' tags
            result_tags = tags[:limit]
            
            # Cache the results
            self._set_cached_data('artist_tags', result_tags, artist_name, limit)
            
            return result_tags
        except Exception as e:
            logger.error(f"Error fetching artist top tags: {e}")
            # Cache empty result on error
            self._set_cached_data('artist_tags', [], artist_name, limit)
            return []
    
    def get_similar_tags(self, tag: str, limit: int = 50) -> List[Dict]:
        """
        Get similar tags/genres using artist.getSimilar for better accuracy.
        
        Approach:
        1. Get top artists for the genre via tag.getTopArtists
        2. For each artist, get similar artists via artist.getSimilar
        3. Collect tags from similar artists, weighted by similarity score
        4. Aggregate into genre similarity scores
        
        This leverages Last.fm's artist similarity which is based on actual
        listening patterns from millions of users.
        
        Args:
            tag: The tag/genre name to find similar tags for
            limit: Maximum number of similar tags to return
            
        Returns:
            List of dicts with 'name' and 'match' (0.0-1.0 similarity score)
        """
        # Check cache first
        cached_tags = self._get_cached_data('similar_tags', tag, limit)
        if cached_tags is not None:
            return cached_tags
        
        if not self.api_key:
            logger.error("Last.fm API key not available")
            return []
        
        try:
            import time
            
            # Step 1: Get top artists for this tag
            params = {
                'method': 'tag.getTopArtists',
                'tag': tag,
                'limit': 10,  # Top 10 artists for the genre
                'api_key': self.api_key,
                'format': 'json'
            }
            
            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'topartists' not in data or 'artist' not in data['topartists']:
                logger.debug(f"No top artists found for tag: {tag}")
                self._set_cached_data('similar_tags', [], tag, limit)
                return []
            
            seed_artists = data['topartists']['artist'][:8]
            
            # Step 2: Get similar artists and their tags
            # Weighted tag accumulator: tag -> weighted count
            tag_weights: Dict[str, float] = {}
            tag_norm_self = tag.lower().strip()
            
            for seed_artist in seed_artists:
                artist_name = seed_artist.get('name')
                if not artist_name:
                    continue
                
                # Get similar artists using artist.getSimilar
                similar_artists = self._get_similar_artists_raw(artist_name, limit=10)
                
                for sim_artist in similar_artists:
                    sim_name = sim_artist.get('name')
                    sim_match = float(sim_artist.get('match', 0))  # 0-1 similarity
                    
                    if not sim_name or sim_match < 0.1:
                        continue
                    
                    # Get tags for this similar artist
                    artist_tags = self.get_artist_top_tags(sim_name, limit=5)
                    
                    for artist_tag in artist_tags:
                        tag_norm = artist_tag.lower().strip()
                        # Skip self-references and generic tags
                        if tag_norm == tag_norm_self or tag_norm in ('seen live', 'favorites', 'my favorite'):
                            continue
                        # Weight by artist similarity
                        tag_weights[tag_norm] = tag_weights.get(tag_norm, 0) + sim_match
                    
                    time.sleep(0.05)  # Respect rate limits
                
                time.sleep(0.1)
            
            # Also get tags from the seed artists themselves
            for seed_artist in seed_artists[:5]:
                artist_name = seed_artist.get('name')
                if artist_name:
                    artist_tags = self.get_artist_top_tags(artist_name, limit=8)
                    for artist_tag in artist_tags:
                        tag_norm = artist_tag.lower().strip()
                        if tag_norm != tag_norm_self and tag_norm not in ('seen live', 'favorites'):
                            tag_weights[tag_norm] = tag_weights.get(tag_norm, 0) + 1.0
            
            # Normalize to 0-1 range
            if not tag_weights:
                self._set_cached_data('similar_tags', [], tag, limit)
                return []
            
            max_weight = max(tag_weights.values())
            similar = []
            for tag_name, weight in sorted(tag_weights.items(), key=lambda x: -x[1]):
                similar.append({
                    'name': tag_name,
                    'match': min(1.0, weight / max_weight),  # Normalize to 0-1
                })
            
            similar = similar[:limit]
            
            # Cache the results
            self._set_cached_data('similar_tags', similar, tag, limit)
            logger.debug(f"Built similar tags for '{tag}': {len(similar)} tags")
            
            return similar
            
        except Exception as e:
            logger.error(f"Error fetching similar tags for '{tag}': {e}")
            self._set_cached_data('similar_tags', [], tag, limit)
            return []
    
    def _get_similar_artists_raw(self, artist_name: str, limit: int = 10) -> List[Dict]:
        """
        Get similar artists from Last.fm artist.getSimilar API.
        
        Uses the match score (0-1) from Last.fm's listening pattern analysis.
        See: https://www.last.fm/api/show/artist.getSimilar
        
        Args:
            artist_name: Name of the artist
            limit: Maximum number of similar artists to return
            
        Returns:
            List of dicts with 'name' and 'match' fields
        """
        if not self.api_key:
            return []
        
        try:
            params = {
                'method': 'artist.getSimilar',
                'artist': artist_name,
                'limit': limit,
                'autocorrect': 1,  # Auto-correct misspellings
                'api_key': self.api_key,
                'format': 'json'
            }
            
            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if 'similarartists' not in data or 'artist' not in data['similarartists']:
                return []
            
            return data['similarartists']['artist'][:limit]
            
        except Exception as e:
            logger.debug(f"Error fetching similar artists for '{artist_name}': {e}")
            return []
    
    def get_tag_info(self, tag: str) -> Optional[Dict]:
        """
        Get information about a tag/genre from Last.fm.
        
        Args:
            tag: The tag/genre name
            
        Returns:
            Dict with tag info including 'reach' (popularity) and 'wiki'
        """
        if not self.api_key:
            return None
        
        try:
            params = {
                'method': 'tag.getInfo',
                'tag': tag,
                'api_key': self.api_key,
                'format': 'json'
            }
            
            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if 'tag' not in data:
                return None
            
            tag_data = data['tag']
            return {
                'name': tag_data.get('name', tag),
                'reach': int(tag_data.get('reach', 0)),
                'total': int(tag_data.get('total', 0)),
                'wiki': tag_data.get('wiki', {}).get('summary', ''),
            }
            
        except Exception as e:
            logger.error(f"Error fetching tag info for '{tag}': {e}")
            return None
    
    def get_artist_info(self, artist_name: str) -> Optional[Dict]:
        """
        Get artist information from Last.fm API with caching and parallel processing
        
        Args:
            artist_name: Name of the artist
            
        Returns:
            Dictionary with artist information or None if not found
        """
        # Check cache first
        cached_info = self._get_cached_data('artist_info', artist_name)
        if cached_info is not None:
            return cached_info
            
        if not self.api_key:
            logger.error("Last.fm API key not available")
            return None
            
        try:
            logger.info(f"Fetching artist info for: {artist_name}")
            
            params = {
                'method': 'artist.getInfo',
                'artist': artist_name,
                'api_key': self.api_key,
                'format': 'json'
            }
            
            response = requests.get(self.base_url, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if 'artist' not in data:
                logger.warning(f"No artist info found for {artist_name}")
                # Cache the negative result
                self._set_cached_data('artist_info', None, artist_name)
                return None
                
            artist = data['artist']
            
            # Extract MBID from the response
            mbid = artist.get('mbid')
            logger.info(f"Found MBID for {artist_name}: {mbid}")

            # Try to resolve a direct Wikipedia URL via MusicBrainz/Wikidata
            wikipedia_url = None
            try:
                if mbid:
                    mb_headers = {'User-Agent': self.user_agent}
                    mb_url = f"https://musicbrainz.org/ws/2/artist/{mbid}?inc=url-rels&fmt=json"
                    mb_response = requests.get(mb_url, headers=mb_headers, timeout=10)
                    mb_response.raise_for_status()
                    mb_data = mb_response.json()

                    wikidata_id = None
                    if 'relations' in mb_data:
                        for relation in mb_data['relations']:
                            # Prefer a direct Wikipedia link when available
                            if relation.get('type') == 'wikipedia' and relation.get('url', {}).get('resource'):
                                wikipedia_url = relation['url']['resource']
                                break
                            # Otherwise capture Wikidata for later resolution
                            if relation.get('type') == 'wikidata' and relation.get('url', {}).get('resource'):
                                wikidata_id = relation['url']['resource'].split('/')[-1]

                    # Resolve Wikipedia from Wikidata sitelinks if needed
                    if not wikipedia_url and wikidata_id:
                        wd_url = f"https://www.wikidata.org/wiki/Special:EntityData/{wikidata_id}.json"
                        wd_response = requests.get(wd_url, timeout=10)
                        wd_response.raise_for_status()
                        wd_data = wd_response.json()
                        entity = (wd_data.get('entities') or {}).get(wikidata_id) or {}
                        sitelinks = entity.get('sitelinks') or {}
                        enwiki = sitelinks.get('enwiki')
                        if enwiki and enwiki.get('title'):
                            title = enwiki['title'].replace(' ', '_')
                            wikipedia_url = f"https://en.wikipedia.org/wiki/{title}"
            except Exception as e:
                logger.debug(f"Wikipedia URL resolution failed for {artist_name}: {e}")
            
            # Process data collection in parallel
            def get_image():
                return self.get_artist_image_from_discogs(artist_name)
            
            def get_tags():
                return self.get_artist_top_tags(artist_name, limit=8)  # Reduced from 10 to 8
            
            def get_similar():
                return self.get_similar_artists(artist_name, limit=6)  # Reduced from 9 to 6
            
            # Submit all tasks in parallel
            futures = {
                'image': self.executor.submit(get_image),
                'tags': self.executor.submit(get_tags), 
                'similar': self.executor.submit(get_similar)
            }
            
            # Collect results with timeout
            results = {}
            for key, future in futures.items():
                try:
                    results[key] = future.result(timeout=10)  # 10 second timeout per task
                except Exception as e:
                    logger.error(f"Error getting {key} for {artist_name}: {e}")
                    results[key] = [] if key in ['tags', 'similar'] else None
            
            # Format the artist info
            artist_info = {
                'name': artist['name'],
                'url': artist['url'],
                'mbid': mbid,
                'image': results['image'],
                'bio': artist.get('bio', {}).get('content', '').replace('\n', '<br>'),
                'bio_summary': artist.get('bio', {}).get('summary', '').replace('\n', '<br>'),
                'tags': results['tags'],
                'similar_artists': results['similar'],
                'listeners': artist.get('stats', {}).get('listeners'),
                'playcount': artist.get('stats', {}).get('playcount'),
                'wikipedia_url': wikipedia_url
            }
            
            # Cache the results
            self._set_cached_data('artist_info', artist_info, artist_name)
            
            return artist_info
        except Exception as e:
            logger.error(f"Error fetching artist info: {e}")
            # Cache the error result
            self._set_cached_data('artist_info', None, artist_name)
            return None
    
    def get_album_info_from_discogs(self, artist_name: str, album_title: str) -> Optional[Dict]:
        """
        Get album information from Discogs
        
        Args:
            artist_name: Name of the artist
            album_title: Title of the album
            
        Returns:
            Dictionary with album information or None if not found
        """
        try:
            # Search for the release on Discogs
            query = f"{artist_name} {album_title}"
            release_results = self.discogs_service.search_release(query, limit=5)
            
            if not release_results:
                logger.warning(f"No release found on Discogs for: {query}")
                return None
                
            # Find the best match
            best_match = None
            for result in release_results:
                # Check if both artist and title match
                if (artist_name.lower() in result.get('title', '').lower() and 
                    album_title.lower() in result.get('title', '').lower()):
                    best_match = result
                    break
            
            # If no exact match found, use the first result
            if not best_match and release_results:
                best_match = release_results[0]
                
            if not best_match:
                return None
                
            # Get detailed release information
            if 'id' in best_match:
                release_detail = self.discogs_service.get_release_detail(best_match['id'])
                if release_detail:
                    # Extract album information
                    album_info = {
                        'title': release_detail.get('title', 'Unknown'),
                        'artist': release_detail.get('artists_sort', artist_name),
                        'year': release_detail.get('year'),
                        'label': release_detail.get('labels', [{}])[0].get('name') if release_detail.get('labels') else None,
                        'catno': release_detail.get('labels', [{}])[0].get('catno') if release_detail.get('labels') else None,
                        'genres': release_detail.get('genres', []),
                        'styles': release_detail.get('styles', []),
                        'url': f"https://www.discogs.com/release/{best_match['id']}",
                        'image': best_match.get('cover_image') or (release_detail.get('images', [{}])[0].get('uri') if release_detail.get('images') else None),
                        'images': [img.get('uri') for img in release_detail.get('images', []) if img.get('uri')] if release_detail.get('images') else []
                    }
                    return album_info
            
            # If detailed info not available, return basic info
            return {
                'title': best_match.get('title', 'Unknown'),
                'artist': artist_name,
                'year': best_match.get('year'),
                'url': best_match.get('uri'),
                'image': best_match.get('cover_image'),
                'images': [best_match.get('cover_image')] if best_match.get('cover_image') else []
            }
        except Exception as e:
            logger.error(f"Error fetching album info from Discogs: {e}")
            return None
    
    def get_track_info(self, artist_name: str, track_name: str) -> Optional[Dict]:
        """
        Get track information from Last.fm API with caching and parallel processing
        
        Args:
            artist_name: Name of the artist
            track_name: Name of the track
            
        Returns:
            Dictionary with track information or None if not found
        """
        # Check cache first
        cached_track = self._get_cached_data('track_info', artist_name, track_name)
        if cached_track is not None:
            return cached_track
            
        if not self.api_key:
            logger.error("Last.fm API key not available")
            return None
            
        try:
            logger.info(f"Fetching track info for: {artist_name} - {track_name}")
            
            params = {
                'method': 'track.getInfo',
                'artist': artist_name,
                'track': track_name,
                'api_key': self.api_key,
                'format': 'json'
            }
            
            response = requests.get(self.base_url, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if 'track' not in data:
                logger.warning(f"No track info found for {artist_name} - {track_name}")
                # Cache the negative result
                self._set_cached_data('track_info', None, artist_name, track_name)
                return None
                
            track = data['track']
            
            # Get album info from Last.fm
            album_title = None
            if 'album' in track and 'title' in track['album']:
                album_title = track['album']['title']
            
            # Process data collection in parallel
            def get_album_info():
                if album_title:
                    return self.get_album_info_from_discogs(artist_name, album_title)
                return None
            
            def get_artist_image():
                return self.get_artist_image_from_discogs(artist_name)
            
            def get_track_tags():
                # Get track tags first
                track_tags = []
                if 'toptags' in track and 'tag' in track['toptags']:
                    track_tags = [tag['name'] for tag in track['toptags']['tag'] if tag.get('name')]
                
                # If no track tags, get artist tags
                if not track_tags:
                    track_tags = self.get_artist_top_tags(artist_name, limit=5)
                
                return track_tags
            
            # Submit tasks in parallel
            futures = {
                'album': self.executor.submit(get_album_info),
                'artist_image': self.executor.submit(get_artist_image),
                'tags': self.executor.submit(get_track_tags)
            }
            
            # Collect results with timeout
            results = {}
            for key, future in futures.items():
                try:
                    results[key] = future.result(timeout=8)  # 8 second timeout per task
                except Exception as e:
                    logger.error(f"Error getting {key} for track {artist_name} - {track_name}: {e}")
                    results[key] = None if key != 'tags' else []
            
            # Use album info from parallel processing or create basic structure
            album = results['album']
            if not album:
                album = {
                    'title': album_title or 'Unknown',
                    'url': None,
                    'image': None,
                    'images': [],
                    'artist': artist_name,
                    'year': None,
                    'label': None,
                    'catno': None,
                    'genres': []
                }
            
            # Format the track info
            track_info = {
                'name': track['name'],
                'artist': {
                    'name': track['artist']['name'],
                    'url': track['artist']['url'] if 'url' in track['artist'] else None,
                    'image': results['artist_image']
                },
                'album': album,
                'url': track['url'],
                'duration': int(track['duration']) if 'duration' in track and track['duration'] else None,
                'listeners': track.get('listeners'),
                'playcount': track.get('playcount'),
                'tags': results['tags']
            }
            
            # Cache the results
            self._set_cached_data('track_info', track_info, artist_name, track_name)
            
            return track_info
        except Exception as e:
            logger.error(f"Error fetching track info: {e}")
            # Cache the error result
            self._set_cached_data('track_info', None, artist_name, track_name)
            return None


