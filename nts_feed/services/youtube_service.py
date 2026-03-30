"""
YouTube Service for NTS Feed
------------------------------

This module provides a service for interacting with the YouTube Data API to find videos.
It implements search functionality to find the most relevant videos for a given track.

Author: NTS Feed Team
"""

import os
import logging
import json
import time
import hashlib
import datetime
from typing import Dict, Any, Optional
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import func, or_, select

from ..db.models import Artist, Track

# Set up logging
logger = logging.getLogger('youtube_service')


def _normalize_lookup_value(value: str) -> str:
    raw = (value or "").lower().strip()
    if not raw:
        return ""
    raw = " ".join(raw.split())
    allowed_punct = {" ", "&", "+", "/", ".", "-", "'", ",", ":", ";", "(", ")"}
    out = []
    for ch in raw:
        if ch.isalnum() or ch in allowed_punct:
            out.append(ch)
    return " ".join("".join(out).split())


def _artist_set_hash(artist_names: list[str]) -> str:
    normalized = sorted(
        name for name in (_normalize_lookup_value(name) for name in artist_names if name) if name
    )
    hasher = hashlib.sha256()
    for name in normalized:
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()

class YouTubeService:
    """
    Service for interacting with the YouTube Data API to search for videos.
    
    This class provides methods for finding the most relevant videos for tracks,
    using the YouTube Data API search functionality.
    """
    
    def __init__(self, api_key: str = ''):
        """
        Initialize the YouTubeService with API key.
        
        Args:
            api_key: YouTube API key
        """
        self.api_key = api_key
        self.cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'youtube_cache')
        self.cache_expiry = 30 * 24 * 60 * 60  # 30 days in seconds
        self.last_request_time = 0
        self.request_interval = 1.0  # 1 second between requests to avoid quota issues
        
        # Quota management
        self.quota_file = os.path.join(self.cache_dir, 'quota.json')
        self.daily_quota_limit = 10000  # YouTube API daily quota limit
        self.quota_reset_hour = 0  # Midnight UTC
        
        # Create cache directory if it doesn't exist
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=True)
            
        # Initialize quota tracking
        self._init_quota_tracking()
    
    def _init_quota_tracking(self):
        """Initialize quota tracking."""
        if os.path.exists(self.quota_file):
            try:
                with open(self.quota_file, 'r') as f:
                    self.quota_data = json.load(f)
            except Exception as e:
                logger.error(f"Error reading quota file: {str(e)}")
                self._reset_quota_data()
        else:
            self._reset_quota_data()
            
        # Check if we need to reset the daily quota
        self._check_quota_reset()
    
    def _reset_quota_data(self):
        """Reset quota data to defaults."""
        today = datetime.datetime.utcnow().strftime('%Y-%m-%d')
        self.quota_data = {
            'date': today,
            'used': 0,
            'searches': 0,
            'cache_hits': 0
        }
        self._save_quota_data()
    
    def _save_quota_data(self):
        """Save quota data to file."""
        try:
            with open(self.quota_file, 'w') as f:
                json.dump(self.quota_data, f)
        except Exception as e:
            logger.error(f"Error saving quota data: {str(e)}")
    
    def _check_quota_reset(self):
        """Check if we need to reset the daily quota."""
        today = datetime.datetime.utcnow().strftime('%Y-%m-%d')
        if self.quota_data.get('date') != today:
            logger.info(f"Resetting YouTube API quota for new day: {today}")
            self._reset_quota_data()
    
    def _update_quota(self, cost=1, is_cache_hit=False):
        """Update quota usage."""
        self._check_quota_reset()
        
        if is_cache_hit:
            self.quota_data['cache_hits'] += 1
        else:
            self.quota_data['used'] += cost
            self.quota_data['searches'] += 1
            
        self._save_quota_data()
        
        # Log quota usage every 10 searches
        if self.quota_data['searches'] % 10 == 0:
            logger.info(f"YouTube API quota usage: {self.quota_data['used']}/{self.daily_quota_limit} units " +
                       f"({self.quota_data['searches']} searches, {self.quota_data['cache_hits']} cache hits)")
    
    def _check_quota_exceeded(self):
        """Check if we've exceeded our daily quota."""
        self._check_quota_reset()
        return self.quota_data['used'] >= self.daily_quota_limit
    
    def _get_cache_key(self, artist: str, title: str) -> str:
        """Generate a cache key from artist and title."""
        cache_key = f"{artist.lower().strip()}_{title.lower().strip()}"
        return hashlib.md5(cache_key.encode()).hexdigest()
    
    def _get_from_cache(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Try to get a cached result for the given artist and title."""
        cache_key = self._get_cache_key(artist, title)
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    cached_data = json.load(f)
                
                # Check if cache is expired
                if time.time() - cached_data.get('timestamp', 0) < self.cache_expiry:
                    logger.info(f"Using cached YouTube result for '{artist} - {title}'")
                    self._update_quota(is_cache_hit=True)
                    return cached_data.get('data')
            except Exception as e:
                logger.error(f"Error reading cache: {str(e)}")
        
        return None
    
    def _save_to_cache(self, artist: str, title: str, data: Dict[str, Any]) -> None:
        """Save result to cache."""
        cache_key = self._get_cache_key(artist, title)
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        
        try:
            with open(cache_file, 'w') as f:
                json.dump({
                    'timestamp': time.time(),
                    'data': data
                }, f)
            logger.info(f"Cached YouTube result for '{artist} - {title}'")
        except Exception as e:
            logger.error(f"Error saving to cache: {str(e)}")
    
    def _rate_limit(self) -> None:
        """Implement rate limiting to avoid quota issues."""
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        
        if time_since_last_request < self.request_interval:
            sleep_time = self.request_interval - time_since_last_request
            logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()

    def _lookup_track(self, session, artist: str, title: str) -> Optional[Track]:
        title_norm = _normalize_lookup_value(title)
        artist_norm = _normalize_lookup_value(artist)
        artist_hash = _artist_set_hash([artist])

        if not title_norm:
            return None

        filters = [Track.title_norm == title_norm]
        if artist_norm and artist_hash:
            filters.append(
                or_(
                    Track.canonical_artist_set_hash == artist_hash,
                    Track.artists.any(func.lower(Artist.name) == artist_norm),
                )
            )
        elif artist_hash:
            filters.append(Track.canonical_artist_set_hash == artist_hash)
        elif artist_norm:
            filters.append(Track.artists.any(func.lower(Artist.name) == artist_norm))

        return session.execute(select(Track).where(*filters)).scalar_one_or_none()

    def _find_track(self, db_sessionmaker, artist: str, title: str) -> Optional[Track]:
        if not db_sessionmaker:
            return None
        try:
            with db_sessionmaker() as session:
                return self._lookup_track(session, artist, title)
        except Exception as e:
            logger.debug("Failed track lookup for YouTube resolution: %s", e)
            return None

    def _build_track_result(self, track: Track, artist: str, title: str) -> Optional[Dict[str, Any]]:
        if not track or not track.youtube_lookup_attempted_at:
            return None
        if track.youtube_search_only and track.youtube_video_url:
            return {
                'success': True,
                'message': 'Using saved YouTube search',
                'video_url': track.youtube_video_url,
                'search_only': True,
            }
        if track.youtube_video_id:
            return {
                'success': True,
                'message': f'Using saved YouTube video for "{artist} - {title}"',
                'video_id': track.youtube_video_id,
                'video_url': track.youtube_video_url or f'https://www.youtube.com/watch?v={track.youtube_video_id}',
                'embed_url': track.youtube_embed_url or f'https://www.youtube.com/embed/{track.youtube_video_id}',
                'title': track.youtube_title,
                'channel': track.youtube_channel,
                'thumbnail': track.youtube_thumbnail,
                'duration': None,
                'views': None,
            }
        encoded_query = f"{artist} {title}".replace(' ', '+')
        return {
            'success': True,
            'message': 'No specific video found, using saved general search',
            'video_url': track.youtube_video_url or f'https://www.youtube.com/results?search_query={encoded_query}',
            'search_only': True,
        }

    def _persist_track_result(self, db_sessionmaker, artist: str, title: str, result: Dict[str, Any]) -> None:
        if not db_sessionmaker or not result.get('success'):
            return
        try:
            with db_sessionmaker() as session:
                track = self._lookup_track(session, artist, title)
                if not track:
                    return
                track.youtube_lookup_attempted_at = datetime.datetime.utcnow()
                track.youtube_search_only = bool(result.get('search_only'))
                track.youtube_video_id = result.get('video_id')
                track.youtube_video_url = result.get('video_url')
                track.youtube_embed_url = result.get('embed_url')
                track.youtube_title = result.get('title')
                track.youtube_channel = result.get('channel')
                track.youtube_thumbnail = result.get('thumbnail')
                session.commit()
        except Exception as e:
            logger.debug("Failed to persist YouTube track resolution: %s", e)
        
    def find_best_video(self, artist: str, title: str, db_sessionmaker=None) -> Dict[str, Any]:
        """
        Find the best matching YouTube video for a track.
        
        Args:
            artist: The track's artist name
            title: The track's title
            
        Returns:
            Dictionary with success status, message, and video information
        """
        # Check persistent track metadata first
        track = self._find_track(db_sessionmaker, artist, title)
        persisted_result = self._build_track_result(track, artist, title)
        if persisted_result:
            return persisted_result

        # Check cache first
        cached_result = self._get_from_cache(artist, title)
        if cached_result:
            self._persist_track_result(db_sessionmaker, artist, title, cached_result)
            return cached_result
        
        # Check if we've exceeded our quota
        if self._check_quota_exceeded():
            logger.warning(f"YouTube API daily quota exceeded ({self.quota_data['used']} units used)")
            return {
                'success': False,
                'message': 'YouTube API daily quota exceeded. Please try again tomorrow.',
                'quota_exceeded': True
            }
            
        try:
            # Apply rate limiting
            self._rate_limit()
            
            # Create YouTube API client
            youtube = build('youtube', 'v3', developerKey=self.api_key, cache_discovery=True)
            
            # Prepare search query
            query = f"{artist} {title} official"
            
            # Execute search request - combine parts to reduce API calls
            search_response = youtube.search().list(
                q=query,
                part='snippet',
                maxResults=1,
                type='video',
                fields='items(id/videoId,snippet(title,channelTitle,thumbnails/high/url))'  # Request only needed fields
            ).execute()
            
            # Update quota (search.list costs 100 units)
            self._update_quota(cost=100)
            
            # Check if we got any results
            if 'items' in search_response and len(search_response['items']) > 0:
                video_id = search_response['items'][0]['id']['videoId']
                video_title = search_response['items'][0]['snippet']['title']
                channel_title = search_response['items'][0]['snippet']['channelTitle']
                thumbnail_url = search_response['items'][0]['snippet']['thumbnails']['high']['url']
                
                # Create result without additional API call for video details
                result = {
                    'success': True,
                    'message': f'Found YouTube video for "{artist} - {title}"',
                    'video_id': video_id,
                    'video_url': f'https://www.youtube.com/watch?v={video_id}',
                    'embed_url': f'https://www.youtube.com/embed/{video_id}',
                    'title': video_title,
                    'channel': channel_title,
                    'thumbnail': thumbnail_url,
                    'duration': None,
                    'views': None
                }
                
                # Save to cache
                self._save_to_cache(artist, title, result)
                self._persist_track_result(db_sessionmaker, artist, title, result)
                
                return result
            else:
                # If no specific video found, return a search URL
                encoded_query = f"{artist} {title}".replace(' ', '+')
                result = {
                    'success': True,
                    'message': 'No specific video found, using general search',
                    'video_url': f'https://www.youtube.com/results?search_query={encoded_query}',
                    'search_only': True
                }
                
                # Save to cache
                self._save_to_cache(artist, title, result)
                self._persist_track_result(db_sessionmaker, artist, title, result)
                
                return result
            
        except HttpError as e:
            error_message = str(e)
            logger.error(f"YouTube API error: {error_message}")
            
            # Check for quota exceeded errors
            if "quota" in error_message.lower():
                # Force quota to be marked as exceeded
                self.quota_data['used'] = self.daily_quota_limit
                self._save_quota_data()
                
                return {
                    'success': False,
                    'message': 'YouTube API daily quota exceeded. Please try again tomorrow.',
                    'quota_exceeded': True
                }
            
            return {
                'success': False,
                'message': f'YouTube API error: {error_message}'
            }
        except Exception as e:
            logger.error(f"Error finding YouTube video: {str(e)}")
            return {
                'success': False,
                'message': str(e)
            }
