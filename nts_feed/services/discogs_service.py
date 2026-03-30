"""
Discogs Service for NTS Feed
------------------------------

This module provides a service for interacting with the Discogs API to find track releases and albums.
It implements sophisticated search and ranking algorithms to find the most relevant and original releases
for a given track, with a focus on avoiding compilations and prioritizing original artist releases.

Author: NTS Feed Team
"""

import os
import logging
import requests
import time
from typing import Dict, List, Any, Optional
from urllib.parse import quote

# Set up logging with a higher level to reduce notifications
logger = logging.getLogger('discogs_service')
logger.setLevel(logging.INFO)  # Change from WARNING to INFO to get more detailed logs

class DiscogsService:
    """
    Service for interacting with the Discogs API to retrieve music metadata.
    """
    
    def __init__(self):
        """
        Initialize the Discogs service with API configuration.
        """
        self.base_url = "https://api.discogs.com"
        # Use environment variables for API keys if available
        self.user_agent = os.environ.get('DISCOGS_USER_AGENT', 'NTSFeed/1.0')
        self.token = os.environ.get('DISCOGS_TOKEN', '')
        
        # Configure rate limiting (Discogs allows 60 requests per minute)
        self.request_delay = 1.0  # seconds between requests
        self.last_request_time = 0
        
        self.logger = logging.getLogger(__name__)
    
    def _get_headers(self):
        """
        Get the headers for Discogs API requests.
        """
        headers = {
            'User-Agent': self.user_agent
        }
        
        # Add token to Authorization header if available
        if self.token:
            headers['Authorization'] = f'Discogs token={self.token}'
        
        return headers
    
    def _get_params(self):
        """
        Get the common parameters for Discogs API requests.
        """
        # No longer adding token as a query parameter
        return {}
    
    def _make_request(self, endpoint, params=None):
        """
        Make a request to the Discogs API with rate limiting.
        
        Args:
            endpoint (str): The API endpoint to request.
            params (dict, optional): Query parameters for the request.
        
        Returns:
            dict: The JSON response from the API, or None if the request failed.
        """
        # Implement rate limiting
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        
        if time_since_last_request < self.request_delay:
            time.sleep(self.request_delay - time_since_last_request)
        
        url = f"{self.base_url}{endpoint}"
        
        # Merge common params with request-specific params
        request_params = self._get_params()
        if params:
            request_params.update(params)
        
        try:
            self.last_request_time = time.time()
            response = requests.get(url, headers=self._get_headers(), params=request_params)
            
            if response.status_code == 200:
                return response.json()
            else:
                self.logger.error(f"Discogs API error: {response.status_code} - {response.text}")
                # Log more details for debugging (redact Authorization header)
                self.logger.error(f"Request URL: {url}")
                try:
                    safe_headers = dict(self._get_headers())
                    if 'Authorization' in safe_headers:
                        safe_headers['Authorization'] = '<redacted>'
                    self.logger.error(f"Request headers: {safe_headers}")
                except Exception:
                    # Fallback if header redaction fails for any reason
                    self.logger.error("Request headers: <unavailable>")
                self.logger.error(f"Request params: {request_params}")
                return None
        
        except Exception as e:
            self.logger.error(f"Error making Discogs API request: {str(e)}")
            return None
    
    def search_artist(self, artist_name, limit=5):
        """
        Search for artists by name.
        
        Args:
            artist_name (str): The name of the artist to search for.
            limit (int, optional): Maximum number of results to return.
        
        Returns:
            list: A list of artist data dictionaries, or an empty list if no results.
        """
        params = {
            'q': artist_name,
            'type': 'artist',
            'per_page': limit
        }
        
        response = self._make_request('/database/search', params)
        
        if response and 'results' in response:
            return response['results']
        
        return []
    
    def get_artist_detail(self, artist_id):
        """
        Get detailed information about an artist.
        
        Args:
            artist_id (int): The Discogs artist ID.
        
        Returns:
            dict: Artist details, or None if not found.
        """
        response = self._make_request(f'/artists/{artist_id}')
        return response
    
    def search_release(self, query, limit=5):
        """
        Search for releases by query string.
        
        Args:
            query (str): The search query (typically artist and title).
            limit (int, optional): Maximum number of results to return.
        
        Returns:
            list: A list of release data dictionaries, or an empty list if no results.
        """
        params = {
            'q': query,
            'type': 'release',
            'per_page': limit
        }
        
        response = self._make_request('/database/search', params)
        
        if response and 'results' in response:
            return response['results']
        
        return []
    
    def get_release_detail(self, release_id):
        """
        Get detailed information about a release.
        
        Args:
            release_id (int): The Discogs release ID.
        
        Returns:
            dict: Release details, or None if not found.
        """
        response = self._make_request(f'/releases/{release_id}')
        return response
    
    def search_master(self, query, limit=5):
        """
        Search for master releases by query string.
        
        Args:
            query (str): The search query (typically artist and title).
            limit (int, optional): Maximum number of results to return.
        
        Returns:
            list: A list of master release data dictionaries, or an empty list if no results.
        """
        params = {
            'q': query,
            'type': 'master',
            'per_page': limit
        }
        
        response = self._make_request('/database/search', params)
        
        if response and 'results' in response:
            return response['results']
        
        return []
    
    def get_master_detail(self, master_id):
        """
        Get detailed information about a master release.
        
        Args:
            master_id (int): The Discogs master release ID.
        
        Returns:
            dict: Master release details, or None if not found.
        """
        response = self._make_request(f'/masters/{master_id}')
        return response

    def find_track_url(self, artist: str, title: str) -> Dict[str, Any]:
        """
        Find the Discogs URL for a track by artist and title.
        
        Implements a multi-strategy search approach with advanced ranking to prioritize
        original releases over compilations.
        
        Args:
            artist: The track's artist name
            title: The track's title
        
        Returns:
            A dictionary containing success status, message, and URL if found
        """
        logger.debug(f"Searching for track: {artist} - {title}")  # Changed from info to debug
        
        # Check for special case handling first
        special_case_url = self._check_special_cases(artist, title)
        if special_case_url:
            logger.debug(f"Found special case match: {special_case_url}")  # Changed from info to debug
            return {
                'success': True,
                'message': 'Found specific release on Discogs (special case)',
                'url': special_case_url
            }
        
        try:
            # Prepare authentication parameters
            auth_params = {}
            if self.token:
                auth_params = {
                    'token': self.token
                }
            
            # Execute search strategies and gather results
            all_search_results = self._execute_search_strategies(artist, title, auth_params)
            
            # If we have results, process and rank them
            if all_search_results:
                # Remove duplicates
                unique_results = self._remove_duplicate_results(all_search_results)
                logger.debug(f"Found {len(unique_results)} unique results across all search strategies")  # Changed from info to debug
                
                # Process, categorize, and score the results
                categorized_results = self._categorize_and_score_results(artist, title, unique_results)
                
                # Find the best result based on our ranking system
                best_result = self._select_best_result(categorized_results)
                
                # If we found a result, create the URL
                if best_result:
                    result_type = best_result.get('type', 'release')
                    result_id = best_result.get('id')
                    
                    if result_id:
                        # Direct link to the release or master
                        discogs_url = f"https://www.discogs.com/{result_type}/{result_id}"
                        logger.debug(f"Found best match: {discogs_url}")  # Changed from info to debug
                        
                        return {
                            'success': True,
                            'message': f'Found specific {result_type} on Discogs',
                            'url': discogs_url
                        }
            
            # If all else fails, fall back to general search
            encoded_query = quote(f"{artist} {title}")
            discogs_url = f"https://www.discogs.com/search/?q={encoded_query}&type=release"
            
            logger.debug(f"No specific release found, using general search: {discogs_url}")  # Changed from info to debug
            
            return {
                'success': True,
                'message': 'Using general Discogs search',
                'url': discogs_url
            }

        except Exception as e:
            logger.error(f"Error creating Discogs search URL: {str(e)}")
            return {
                'success': False,
                'message': str(e)
            }
    
    def _check_special_cases(self, artist: str, title: str) -> Optional[str]:
        """
        Check if the artist and title match any known special cases.
        
        Args:
            artist: The track's artist name
            title: The track's title
            
        Returns:
            URL string if a special case is found, None otherwise
        """
        # Normalize input for comparison
        artist_lower = artist.lower().strip()
        title_lower = title.lower().strip()
        
        # Check if this track is in our special cases dictionary
        special_cases = {
            # Format: (lowercase_artist, lowercase_title): url
            ('the valentine bros.', 'let me be close to you'): 'https://www.discogs.com/master/1284413'
        }
        return special_cases.get((artist_lower, title_lower))
    
    def _execute_search_strategies(self, artist: str, title: str, auth_params: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        Execute multiple search strategies to find the best matches.
        
        Args:
            artist: The track's artist name
            title: The track's title
            auth_params: Authentication parameters for the Discogs API
            
        Returns:
            List of search results from all strategies
        """
        api_url = "https://api.discogs.com/database/search"
        all_search_results = []
        
        # Define our search strategies
        search_strategies = [
            {
                'name': 'Exact Track and Artist Search',
                'params': {
                    'q': f'"{title}" artist:"{artist}"',
                    'type': 'master,release',
                    'per_page': 25,
                    **auth_params
                }
            },
            {
                'name': 'Artist and Track Parameter Search',
                'params': {
                    'artist': artist,
                    'track': title,
                    'per_page': 25,
                    **auth_params
                }
            },
            {
                'name': 'Combined Search',
                'params': {
                    'q': f"{artist} {title}",
                    'type': 'master,release',
                    'per_page': 25,
                    **auth_params
                }
            }
        ]
        
        # Execute all search strategies and collect results
        for strategy in search_strategies:
            try:
                logger.debug(f"Trying {strategy['name']}: {strategy['params']}")  # Changed from info to debug
                response = requests.get(api_url, headers=self._get_headers(), params=strategy['params'])
                response.raise_for_status()
                results = response.json().get('results', [])
                
                # Tag each result with the strategy that found it
                for result in results:
                    result['_search_strategy'] = strategy['name']
                
                all_search_results.extend(results)
                
                logger.debug(f"Found {len(results)} results with {strategy['name']}")  # Changed from info to debug
                
                # If we already have enough results, we can stop searching
                if len(all_search_results) >= 50:
                    break
                    
            except Exception as e:
                logger.error(f"{strategy['name']} failed: {str(e)}")
        
        return all_search_results
    
    def _remove_duplicate_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Remove duplicate results based on their IDs.
        
        Args:
            results: List of search results
            
        Returns:
            List of unique search results
        """
        unique_results = {}
        for result in results:
            result_id = result.get('id')
            if result_id not in unique_results:
                unique_results[result_id] = result
        
        return list(unique_results.values())
    
    def _categorize_and_score_results(self, artist: str, title: str, results: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Process and categorize the results based on various criteria.
        
        Args:
            artist: The track's artist name
            title: The track's title
            results: List of search results
            
        Returns:
            Dictionary of categorized and scored results
        """
        # Initialize categories
        results_by_category = {
            'artist_master': [],       # Master releases by exact artist
            'artist_album': [],        # Album releases by exact artist
            'artist_single': [],       # Single releases by exact artist
            'artist_other': [],        # Other releases by exact artist
            'related_master': [],      # Master releases by related artist
            'related_album': [],       # Album releases by related artist
            'related_single': [],      # Single releases by related artist
            'compilations': [],        # Compilation releases
            'unknown': []              # Uncategorized releases
        }
        
        # Separate tracking for non-compilation results
        non_compilation_results = []
        
        # Analyze and categorize each result
        for result in results:
            result_title = result.get('title', '')
            result_artist = result.get('artist', '')
            result_type = result.get('type', '')
            format_details = result.get('format', [])
            
            # Calculate artist match level
            artist_match = self._calculate_artist_match(artist, result_artist, result_title)
            
            # Determine if this is a compilation
            is_compilation = self._is_compilation(result_title, result_artist, format_details)
            
            # Determine release format (album, single, etc.)
            release_format = self._determine_release_format(format_details, result_title, title)
            
            # Calculate the quality score for ranking within categories
            quality_score = self._calculate_quality_score(
                artist, title, result, artist_match, is_compilation, release_format
            )
            
            # Add the score and metadata to the result for sorting and debugging
            result['_score'] = quality_score
            result['_artist_match'] = artist_match
            result['_is_compilation'] = is_compilation
            result['_release_format'] = release_format
            
            # Keep track of non-compilation results
            if not is_compilation:
                non_compilation_results.append(result)
            
            # Categorize the result
            if is_compilation:
                results_by_category['compilations'].append(result)
            elif artist_match == 'exact':
                if result_type == 'master':
                    results_by_category['artist_master'].append(result)
                elif release_format == 'album':
                    results_by_category['artist_album'].append(result)
                elif release_format == 'single':
                    results_by_category['artist_single'].append(result)
                else:
                    results_by_category['artist_other'].append(result)
            elif artist_match == 'partial':
                if result_type == 'master':
                    results_by_category['related_master'].append(result)
                elif release_format == 'album':
                    results_by_category['related_album'].append(result)
                elif release_format == 'single':
                    results_by_category['related_single'].append(result)
                else:
                    results_by_category['unknown'].append(result)
            else:
                results_by_category['unknown'].append(result)
        
        # Add non-compilation results to a separate category
        if non_compilation_results:
            results_by_category['non_compilations'] = sorted(
                non_compilation_results, 
                key=lambda x: x.get('_score', 0), 
                reverse=True
            )
            
        # Sort each category by score
        for category in results_by_category:
            results_by_category[category].sort(key=lambda x: x.get('_score', 0), reverse=True)
                
        # Log the counts of each category
        for category, categorized_results in results_by_category.items():
            if categorized_results:
                logger.debug(f"Category '{category}': {len(categorized_results)} results")  # Changed from info to debug
                # Log the top result in each non-empty category
                top_result = categorized_results[0]
                logger.debug(f"  Top result: {top_result.get('title')} - Artist: {top_result.get('artist')} - Score: {top_result.get('_score')}")  # Changed from info to debug
        
        return results_by_category
    
    def _select_best_result(self, results_by_category: Dict[str, List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        """
        Select the best result based on our prioritization rules.
        
        Args:
            results_by_category: Dictionary of categorized and scored results
            
        Returns:
            The best result or None if no suitable results found
        """
        # Always prioritize non-compilations if available
        if 'non_compilations' in results_by_category and results_by_category['non_compilations']:
            best_result = results_by_category['non_compilations'][0]
            logger.debug(f"Selected best non-compilation result: {best_result.get('title')} - Artist: {best_result.get('artist')} - Score: {best_result.get('_score')}")  # Changed from info to debug
            return best_result
        
        # Define the priority order for categories
        category_priority = [
            'artist_master',
            'artist_album',
            'artist_single',
            'artist_other',
            'related_master',
            'related_album',
            'related_single',
            'compilations',
            'unknown'
        ]
        
        # Find the best result based on category priority
        for category in category_priority:
            if category in results_by_category and results_by_category[category]:
                best_result = results_by_category[category][0]
                logger.debug(f"Selected best result from category '{category}': {best_result.get('title')} - Artist: {best_result.get('artist')} - Score: {best_result.get('_score')}")  # Changed from info to debug
                return best_result
        
        return None
    
    def _calculate_artist_match(self, artist: str, result_artist: str, result_title: str) -> str:
        """
        Calculate how well the artist names match.
        
        Args:
            artist: The track's artist name
            result_artist: The artist name from the search result
            result_title: The title from the search result
            
        Returns:
            Match level: 'exact', 'partial', or 'none'
        """
        if not result_artist:
            # Check if the artist is in the title instead (common for some releases)
            if artist.lower() in result_title.lower():
                # Check how much of the title is the artist name
                artist_portion = len(artist) / len(result_title) if result_title else 0
                if artist_portion > 0.5:  # If artist name is more than half the title
                    return 'exact'
                else:
                    return 'partial'
            return 'none'
        
        # Normalize names for comparison
        artist_norm = artist.lower().strip()
        result_artist_norm = result_artist.lower().strip()
        
        # Check for exact match
        if artist_norm == result_artist_norm:
            return 'exact'
        
        # Check for partial matches
        if artist_norm in result_artist_norm or result_artist_norm in artist_norm:
            return 'partial'
        
        # Check for word-by-word match (e.g., "John Doe" vs "Doe, John")
        artist_words = set(w for w in artist_norm.split() if len(w) > 2)
        result_words = set(w for w in result_artist_norm.split() if len(w) > 2)
        
        common_words = artist_words.intersection(result_words)
        
        if len(common_words) == len(artist_words) or len(common_words) == len(result_words):
            return 'exact'
        
        # If we have a significant overlap, consider it a partial match
        if common_words and (len(common_words) / max(len(artist_words), len(result_words)) > 0.5):
            return 'partial'
        
        return 'none'
    
    def _is_compilation(self, result_title: str, result_artist: str, format_details: Any) -> bool:
        """
        Determine if a release is a compilation.
        
        Args:
            result_title: The title from the search result
            result_artist: The artist name from the search result
            format_details: Format details from the search result
            
        Returns:
            True if it's a compilation, False otherwise
        """
        title_lower = result_title.lower() if result_title else ""
        artist_lower = result_artist.lower() if result_artist else ""
        format_str = str(format_details).lower()
        
        # Direct indicators
        if 'various' in artist_lower or 'various' in title_lower:
            return True
        
        if 'compilation' in title_lower or 'compilation' in format_str:
            return True
        
        # Common compilation words in title
        compilation_keywords = [
            'greatest hits', 'best of', 'anthology', 'collection', 'selected works',
            'essential', 'collected', 'classic', 'definitive', 'ultimate', 'gold',
            'platinum', 'diamond', 'hits', 'treasures', 'archives'
        ]
        
        for keyword in compilation_keywords:
            if keyword in title_lower:
                return True
        
        # Look for "Various" in artist field
        if isinstance(format_details, list):
            for detail in format_details:
                if isinstance(detail, str) and 'compilation' in detail.lower():
                    return True
        
        return False
    
    def _determine_release_format(self, format_details: Any, result_title: str, track_title: str) -> str:
        """
        Determine the format of the release (album, single, etc.)
        
        Args:
            format_details: Format details from the search result
            result_title: The title from the search result
            track_title: The track title we're searching for
            
        Returns:
            Format type: 'album', 'single', or 'other'
        """
        format_str = str(format_details).lower()
        title_lower = result_title.lower() if result_title else ""
        
        # Check for explicit format indicators
        if 'album' in format_str:
            return 'album'
        
        if 'single' in format_str or 'ep' in format_str:
            return 'single'
        
        # Check for LP indicator (likely an album)
        if 'lp' in format_str:
            return 'album'
        
        # If the title is very similar to the track title, it's likely a single
        if track_title and track_title.lower() in title_lower:
            similarity = len(track_title) / len(result_title) if result_title else 0
            if similarity > 0.7:  # If track title is more than 70% of the release title
                return 'single'
        
        # Default to 'other' if we can't determine
        return 'other'
    
    def _calculate_quality_score(self, artist: str, title: str, result: Dict[str, Any], 
                               artist_match: str, is_compilation: bool, release_format: str) -> float:
        """
        Calculate a quality score for ranking results within the same category.
        
        Args:
            artist: The track's artist name
            title: The track's title
            result: The search result
            artist_match: Artist match level ('exact', 'partial', 'none')
            is_compilation: Whether the result is a compilation
            release_format: The release format ('album', 'single', 'other')
            
        Returns:
            A numerical score representing the quality of the match
        """
        score = 0
        result_title = result.get('title', '')
        result_artist = result.get('artist', '')
        result_type = result.get('type', '')
        format_details = result.get('format', [])
        
        # Base scores by result type
        if result_type == 'master':
            score += 50  # Master releases are highly preferred
        
        # Artist match scores
        if artist_match == 'exact':
            score += 100
        elif artist_match == 'partial':
            score += 50
        
        # Format type scores
        if release_format == 'album':
            score += 30
        elif release_format == 'single':
            score += 20
            # If this is a single and the title closely matches the track, bonus points
            if title.lower() in result_title.lower():
                score += 15
        
        # Title match scores
        if title.lower() in result_title.lower():
            score += 25
        
        # Exact title match (for singles)
        if title.lower() == result_title.lower():
            score += 35
        
        # Compilation penalties
        if is_compilation:
            score -= 200  # Heavy penalty for compilations
            
            # Additional penalties for various compilation types
            if result_artist and 'various' in result_artist.lower():
                score -= 100  # Very heavy penalty for "Various Artists" compilations
                
            if 'compilation' in result_title.lower():
                score -= 80  # Heavy penalty for explicitly labeled compilations
        
        # Age of release (prefer original releases)
        if 'reissue' in str(format_details).lower():
            score -= 10
        
        # Community data (if available)
        community = result.get('community', {})
        have = community.get('have', 0)
        want = community.get('want', 0)
        
        # Popular releases get a small bonus
        if have + want > 0:
            popularity = (have + want) / 1000  # Scale down large numbers
            score += min(15, popularity)  # Cap the bonus at 15 points
        
        # Search strategy bonuses
        strategy = result.get('_search_strategy', '')
        if 'Exact Track and Artist Search' in strategy:
            score += 10  # Bonus for exact search matches
        
        return score


