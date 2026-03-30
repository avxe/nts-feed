"""Input validation utilities for API endpoints.

Provides reusable validators for common input types to prevent
injection attacks, resource exhaustion, and invalid data.
"""

import re
from typing import Any, Dict, List, Optional
from functools import wraps
from flask import request, jsonify


# === Constants ===

# Maximum lengths for string fields
MAX_TITLE_LENGTH = 500
MAX_ARTIST_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 5000
MAX_URL_LENGTH = 2048
MAX_PLAYLIST_NAME_LENGTH = 200

# Maximum batch sizes
MAX_BATCH_SIZE = 500
MAX_TRACKS_BATCH = 200
MAX_IDS_BATCH = 500

# NTS URL patterns
NTS_SHOW_PATTERN = re.compile(
    r'^https?://(?:www\.)?nts\.live/shows/[\w-]+/?$',
    re.IGNORECASE
)
NTS_EPISODE_PATTERN = re.compile(
    r'^https?://(?:www\.)?nts\.live/shows/[\w-]+/episodes/[\w-]+/?$',
    re.IGNORECASE
)


def escape_like(value: str) -> str:
    """Escape SQL LIKE wildcard characters in a user-supplied string.

    Prevents ``%`` and ``_`` in user input from acting as LIKE wildcards.
    The backslash is used as the escape character, so callers must pass
    ``escape='\\\\'`` to SQLAlchemy's ``.ilike()`` / ``.contains()``
    when using the escaped value.
    """
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


class ValidationError(Exception):
    """Raised when input validation fails."""
    def __init__(self, message: str, field: Optional[str] = None):
        self.message = message
        self.field = field
        super().__init__(message)


# === String Validators ===

def sanitize_string(value: Any, max_length: int = MAX_TITLE_LENGTH, 
                    strip: bool = True, allow_empty: bool = True) -> Optional[str]:
    """Sanitize a string value.
    
    Args:
        value: The value to sanitize
        max_length: Maximum allowed length
        strip: Whether to strip whitespace
        allow_empty: Whether empty strings are allowed
        
    Returns:
        Sanitized string or None if empty and allowed
        
    Raises:
        ValidationError: If validation fails
    """
    if value is None:
        if allow_empty:
            return None
        raise ValidationError("Value is required")
    
    if not isinstance(value, str):
        value = str(value)
    
    if strip:
        value = value.strip()
    
    if not value:
        if allow_empty:
            return None
        raise ValidationError("Value cannot be empty")
    
    if len(value) > max_length:
        raise ValidationError(f"Value exceeds maximum length of {max_length}")
    
    return value


def validate_required_string(value: Any, field_name: str, 
                             max_length: int = MAX_TITLE_LENGTH) -> str:
    """Validate a required string field.
    
    Args:
        value: The value to validate
        field_name: Name of the field (for error messages)
        max_length: Maximum allowed length
        
    Returns:
        Validated string
        
    Raises:
        ValidationError: If validation fails
    """
    try:
        result = sanitize_string(value, max_length=max_length, allow_empty=False)
        if result is None:
            raise ValidationError(f"{field_name} is required", field_name)
        return result
    except ValidationError as e:
        if e.field is None:
            e.field = field_name
            e.message = f"{field_name}: {e.message}"
        raise


# === URL Validators ===

def validate_url(url: Any, max_length: int = MAX_URL_LENGTH) -> str:
    """Validate a general URL.
    
    Args:
        url: The URL to validate
        max_length: Maximum URL length
        
    Returns:
        Validated URL string
        
    Raises:
        ValidationError: If validation fails
    """
    url = sanitize_string(url, max_length=max_length, allow_empty=False)
    if url is None:
        raise ValidationError("URL is required", "url")
    
    # Basic URL format check
    if not url.startswith(('http://', 'https://')):
        raise ValidationError("URL must start with http:// or https://", "url")
    
    return url


def validate_nts_show_url(url: Any) -> str:
    """Validate an NTS show URL.
    
    Args:
        url: The URL to validate
        
    Returns:
        Validated URL string
        
    Raises:
        ValidationError: If not a valid NTS show URL
    """
    url = validate_url(url)
    
    # Normalize URL
    url = url.rstrip('/')
    
    if not NTS_SHOW_PATTERN.match(url + '/'):
        raise ValidationError(
            "Invalid NTS show URL. Expected format: https://www.nts.live/shows/show-name",
            "url"
        )
    
    return url


def validate_nts_episode_url(url: Any) -> str:
    """Validate an NTS episode URL.
    
    Args:
        url: The URL to validate
        
    Returns:
        Validated URL string
        
    Raises:
        ValidationError: If not a valid NTS episode URL
    """
    url = validate_url(url)
    
    # Normalize URL
    url = url.rstrip('/')
    
    if not NTS_EPISODE_PATTERN.match(url + '/'):
        raise ValidationError(
            "Invalid NTS episode URL. Expected format: https://www.nts.live/shows/show-name/episodes/episode-name",
            "url"
        )
    
    return url


def validate_optional_nts_url(url: Any) -> Optional[str]:
    """Validate an optional NTS URL (show or episode).
    
    Args:
        url: The URL to validate (can be None/empty)
        
    Returns:
        Validated URL string or None
    """
    url = sanitize_string(url, max_length=MAX_URL_LENGTH, allow_empty=True)
    if not url:
        return None
    
    url = url.rstrip('/')
    
    # Accept either show or episode URL
    if NTS_SHOW_PATTERN.match(url + '/') or NTS_EPISODE_PATTERN.match(url + '/'):
        return url
    
    # For non-NTS URLs, just return the sanitized version
    return url


# === List Validators ===

def validate_list(value: Any, field_name: str, 
                  max_length: int = MAX_BATCH_SIZE,
                  min_length: int = 0) -> List:
    """Validate a list/array input.
    
    Args:
        value: The value to validate
        field_name: Name of the field (for error messages)
        max_length: Maximum number of items
        min_length: Minimum number of items
        
    Returns:
        Validated list
        
    Raises:
        ValidationError: If validation fails
    """
    if value is None:
        value = []
    
    if not isinstance(value, list):
        raise ValidationError(f"{field_name} must be a list", field_name)
    
    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} exceeds maximum size of {max_length} items",
            field_name
        )
    
    if len(value) < min_length:
        raise ValidationError(
            f"{field_name} requires at least {min_length} items",
            field_name
        )
    
    return value


def validate_id_list(value: Any, field_name: str,
                     max_length: int = MAX_IDS_BATCH) -> List[int]:
    """Validate a list of integer IDs.
    
    Args:
        value: The value to validate
        field_name: Name of the field (for error messages)
        max_length: Maximum number of IDs
        
    Returns:
        List of validated integer IDs
        
    Raises:
        ValidationError: If validation fails
    """
    items = validate_list(value, field_name, max_length=max_length)
    
    result = []
    for i, item in enumerate(items):
        try:
            id_val = int(item)
            if id_val <= 0:
                raise ValidationError(
                    f"{field_name}[{i}]: ID must be positive",
                    field_name
                )
            result.append(id_val)
        except (TypeError, ValueError):
            raise ValidationError(
                f"{field_name}[{i}]: Invalid ID format",
                field_name
            )
    
    return result


# === Track/Episode Validators ===

def validate_track_data(data: Dict, require_episode: bool = False) -> Dict:
    """Validate track data for likes/playlists.
    
    Args:
        data: Dictionary with track data
        require_episode: Whether episode_url is required
        
    Returns:
        Validated track data dictionary
        
    Raises:
        ValidationError: If validation fails
    """
    return {
        'artist': validate_required_string(data.get('artist'), 'artist', MAX_ARTIST_LENGTH),
        'title': validate_required_string(data.get('title'), 'title', MAX_TITLE_LENGTH),
        'episode_url': (
            validate_optional_nts_url(data.get('episode_url'))
            if not require_episode
            else validate_nts_episode_url(data.get('episode_url'))
        ),
        'episode_title': sanitize_string(data.get('episode_title'), MAX_TITLE_LENGTH),
        'show_title': sanitize_string(data.get('show_title'), MAX_TITLE_LENGTH),
    }


def validate_episode_like_data(data: Dict) -> Dict:
    """Validate episode like data.
    
    Args:
        data: Dictionary with episode like data
        
    Returns:
        Validated episode data dictionary
        
    Raises:
        ValidationError: If validation fails
    """
    episode_url = sanitize_string(data.get('episode_url'), MAX_URL_LENGTH, allow_empty=False)
    if not episode_url:
        raise ValidationError("episode_url is required", "episode_url")
    
    episode_title = sanitize_string(data.get('episode_title'), MAX_TITLE_LENGTH, allow_empty=False)
    if not episode_title:
        raise ValidationError("episode_title is required", "episode_title")
    
    return {
        'episode_url': episode_url,
        'episode_title': episode_title,
        'show_title': sanitize_string(data.get('show_title'), MAX_TITLE_LENGTH),
        'show_url': validate_optional_nts_url(data.get('show_url')),
        'episode_date': sanitize_string(data.get('episode_date'), 100),
        'image_url': sanitize_string(data.get('image_url'), MAX_URL_LENGTH),
    }


def validate_playlist_data(data: Dict, require_name: bool = False) -> Dict:
    """Validate playlist data.
    
    Args:
        data: Dictionary with playlist data
        require_name: Whether name is required
        
    Returns:
        Validated playlist data dictionary
        
    Raises:
        ValidationError: If validation fails
    """
    name = sanitize_string(data.get('name'), MAX_PLAYLIST_NAME_LENGTH)
    if require_name and not name:
        raise ValidationError("Playlist name is required", "name")
    
    return {
        'name': name or 'New Playlist',
        'description': sanitize_string(data.get('description'), MAX_DESCRIPTION_LENGTH),
    }


# === Decorator for Endpoint Validation ===

def validate_json_body(validator_func):
    """Decorator to validate JSON request body.
    
    Args:
        validator_func: Function that takes data dict and returns validated dict
                       or raises ValidationError
        
    Returns:
        Decorator function
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            data = request.get_json(silent=True) or {}
            try:
                validated = validator_func(data)
                # Inject validated data into request context
                request.validated_data = validated
            except ValidationError as e:
                return jsonify({
                    'success': False,
                    'message': e.message,
                    'field': e.field
                }), 400
            return f(*args, **kwargs)
        return decorated_function
    return decorator

