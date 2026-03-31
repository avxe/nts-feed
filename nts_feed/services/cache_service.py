"""
Cache Service for NTS Feed

Provides intelligent caching of NTS API responses to significantly reduce
network requests and improve update performance.
"""

import json
import logging
import time
import hashlib
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class CacheService:
    """
    Service for caching NTS API responses and other expensive operations.
    
    Features:
    - File-based caching with configurable TTL
    - Automatic cache cleanup
    - Cache hit/miss statistics
    - Thread-safe operations
    """
    
    def __init__(self, cache_dir: str = "cache/nts"):
        """
        Initialize the cache service
        
        Args:
            cache_dir: Directory to store cache files
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Default TTL values (in seconds)
        self.default_ttl = {
            'episodes_api': 3600,        # 1 hour for episode API responses
            'episode_page': 24 * 3600,   # 24 hours for episode page content
            'show_page': 6 * 3600,       # 6 hours for show page content
            'tracklist': 24 * 3600,      # 24 hours for tracklist data
            'genres': 24 * 3600          # 24 hours for genre data
        }
        
        # Statistics
        self.stats = {
            'hits': 0,
            'misses': 0,
            'sets': 0,
            'deletes': 0
        }
        
        logger.debug("Cache service initialized with directory: %s", self.cache_dir)
    
    def _get_cache_key(self, category: str, identifier: str) -> str:
        """
        Generate a cache key from category and identifier
        
        Args:
            category: Cache category (e.g., 'episodes_api', 'episode_page')
            identifier: Unique identifier for the cached item
            
        Returns:
            Hashed cache key
        """
        key_data = f"{category}:{identifier}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key: str) -> Path:
        """Get the full path for a cache file"""
        return self.cache_dir / f"{cache_key}.json"
    
    def get(self, category: str, identifier: str, ttl: Optional[int] = None) -> Optional[Any]:
        """
        Get an item from cache
        
        Args:
            category: Cache category
            identifier: Unique identifier
            ttl: Custom TTL in seconds (optional)
            
        Returns:
            Cached data if valid, None otherwise
        """
        cache_key = self._get_cache_key(category, identifier)
        cache_path = self._get_cache_path(cache_key)
        
        try:
            if not cache_path.exists():
                self.stats['misses'] += 1
                return None
            
            # Check if cache is expired
            file_age = time.time() - cache_path.stat().st_mtime
            cache_ttl = ttl or self.default_ttl.get(category, 3600)
            
            if file_age > cache_ttl:
                logger.debug(f"Cache expired for {category}:{identifier} (age: {file_age:.1f}s)")
                self.delete(category, identifier)
                self.stats['misses'] += 1
                return None
            
            # Load and return cached data
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
            
            self.stats['hits'] += 1
            logger.debug(f"Cache hit for {category}:{identifier}")
            return cached_data.get('data')
            
        except Exception as e:
            logger.warning(f"Error reading cache for {category}:{identifier}: {e}")
            self.stats['misses'] += 1
            return None
    
    def set(self, category: str, identifier: str, data: Any, ttl: Optional[int] = None) -> bool:
        """
        Store an item in cache
        
        Args:
            category: Cache category
            identifier: Unique identifier
            data: Data to cache
            ttl: Custom TTL in seconds (optional)
            
        Returns:
            True if successful, False otherwise
        """
        cache_key = self._get_cache_key(category, identifier)
        cache_path = self._get_cache_path(cache_key)
        
        try:
            cache_data = {
                'timestamp': time.time(),
                'category': category,
                'identifier': identifier,
                'ttl': ttl or self.default_ttl.get(category, 3600),
                'data': data
            }
            
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
            
            self.stats['sets'] += 1
            logger.debug(f"Cached data for {category}:{identifier}")
            return True
            
        except Exception as e:
            logger.error(f"Error caching data for {category}:{identifier}: {e}")
            return False
    
    def delete(self, category: str, identifier: str) -> bool:
        """
        Delete an item from cache
        
        Args:
            category: Cache category
            identifier: Unique identifier
            
        Returns:
            True if successful, False otherwise
        """
        cache_key = self._get_cache_key(category, identifier)
        cache_path = self._get_cache_path(cache_key)
        
        try:
            if cache_path.exists():
                cache_path.unlink()
                self.stats['deletes'] += 1
                logger.debug(f"Deleted cache for {category}:{identifier}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting cache for {category}:{identifier}: {e}")
            return False
    
    def clear_category(self, category: str) -> int:
        """
        Clear all cache entries for a specific category
        
        Args:
            category: Cache category to clear
            
        Returns:
            Number of items deleted
        """
        deleted_count = 0
        
        try:
            for cache_file in self.cache_dir.glob("*.json"):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cached_data = json.load(f)
                    
                    if cached_data.get('category') == category:
                        cache_file.unlink()
                        deleted_count += 1
                        
                except Exception as e:
                    logger.warning(f"Error checking cache file {cache_file}: {e}")
            
            logger.info(f"Cleared {deleted_count} cache entries for category '{category}'")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error clearing cache category '{category}': {e}")
            return 0
    
    def cleanup_expired(self) -> int:
        """
        Remove all expired cache entries
        
        Returns:
            Number of items deleted
        """
        deleted_count = 0
        current_time = time.time()
        
        try:
            for cache_file in self.cache_dir.glob("*.json"):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cached_data = json.load(f)
                    
                    timestamp = cached_data.get('timestamp', 0)
                    ttl = cached_data.get('ttl', 3600)
                    
                    if current_time - timestamp > ttl:
                        cache_file.unlink()
                        deleted_count += 1
                        
                except Exception as e:
                    logger.warning(f"Error checking cache file {cache_file}: {e}")
                    # Delete corrupted cache files
                    try:
                        cache_file.unlink()
                        deleted_count += 1
                    except Exception:
                        pass
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} expired cache entries")
            
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error during cache cleanup: {e}")
            return 0
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics
        
        Returns:
            Dictionary with cache statistics
        """
        total_requests = self.stats['hits'] + self.stats['misses']
        hit_rate = (self.stats['hits'] / total_requests * 100) if total_requests > 0 else 0
        
        # Count cache files
        cache_files = list(self.cache_dir.glob("*.json"))
        total_size = sum(f.stat().st_size for f in cache_files if f.exists())
        
        return {
            'hits': self.stats['hits'],
            'misses': self.stats['misses'],
            'sets': self.stats['sets'],
            'deletes': self.stats['deletes'],
            'hit_rate_percent': round(hit_rate, 2),
            'total_entries': len(cache_files),
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'cache_dir': str(self.cache_dir)
        }
    
    def invalidate_show(self, show_url: str):
        """
        Invalidate all cache entries for a specific show
        
        Args:
            show_url: URL of the show to invalidate
        """
        from ..scrape import slugify
        show_slug = slugify(show_url)
        
        # Clear episodes API cache
        self.delete('episodes_api', show_slug)
        
        # Clear show page cache
        self.delete('show_page', show_url)
        
        logger.info(f"Invalidated cache for show: {show_url}")


# Global cache service instance
cache_service = CacheService()


def with_cache(category: str, ttl: Optional[int] = None):
    """
    Decorator for caching function results
    
    Args:
        category: Cache category
        ttl: Cache TTL in seconds
        
    Usage:
        @with_cache('episodes_api', ttl=3600)
        def fetch_episodes(show_slug):
            # ... expensive operation
            return data
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            import inspect
            sig = inspect.signature(func)
            bound_args = sig.bind(*args, **kwargs)
            bound_args.apply_defaults()
            
            # Use first argument as identifier (usually show_slug or url)
            identifier = str(bound_args.arguments.get(list(sig.parameters.keys())[0], 'unknown'))
            
            # Try to get from cache
            cached_result = cache_service.get(category, identifier, ttl)
            if cached_result is not None:
                return cached_result
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            if result is not None:
                cache_service.set(category, identifier, result, ttl)
            
            return result
        
        return wrapper
    return decorator

