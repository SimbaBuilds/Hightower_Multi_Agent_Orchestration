"""
Request Cache Service:

A dedicated cache service that manages request-scoped caches for storing large data
and providing cache keys for retrieval by agents within the same request.
"""

import threading
import logging
from typing import Any, Dict
from app.config import CACHE_MAX_CHARS


class RequestCacheService:
    """
    Service for managing request-scoped caches with automatic cleanup.
    
    This service provides:
    - One cache per request identified by request_id
    - Request isolation - each request gets its own cache instance
    - Automatic cleanup when request completes
    - Concurrency safety - no interference between simultaneous requests
    - Shared within request - all agents in the same request access cache via request_id
    """
    
    _caches: Dict[str, Dict[str, Any]] = {}  # request_id -> cache_dict
    _lock = threading.RLock()  # Reentrant lock for thread safety
    _logger = logging.getLogger(__name__)
    
    @classmethod
    def get_cache(cls, request_id: str) -> Dict[str, Any]:
        """
        Get or create cache for a specific request.
        
        Args:
            request_id: Unique identifier for the request
            
        Returns:
            Dictionary cache for the request
        """
        with cls._lock:
            if request_id not in cls._caches:
                cls._caches[request_id] = {}
            return cls._caches[request_id]
    
    @classmethod
    def store(cls, request_id: str, key: str, data: Any) -> str:
        """
        Store data in request cache and return cache key.
        Content is truncated if it exceeds CACHE_MAX_CHARS limit.
        
        Args:
            request_id: Unique identifier for the request
            key: Cache key to store data under
            data: Data to store in cache
            
        Returns:
            The cache key that was used to store the data
        """
        with cls._lock:
            cache = cls.get_cache(request_id)
            
            # Convert data to string and check length
            data_str = str(data)
            original_length = len(data_str)
            
            # Truncate if necessary
            if original_length > CACHE_MAX_CHARS:
                data_str = data_str[:CACHE_MAX_CHARS]
                cls._logger.info(
                    f"Cache content truncated for key '{key}' in request '{request_id}': "
                    f"{original_length} -> {CACHE_MAX_CHARS} chars"
                )
            
            cache[key] = data_str
            return key
    
    @classmethod
    def retrieve(cls, request_id: str, key: str) -> Any:
        """
        Retrieve data from request cache.
        
        Args:
            request_id: Unique identifier for the request
            key: Cache key to retrieve data for
            
        Returns:
            Cached data if found, None otherwise
        """
        with cls._lock:
            cache = cls.get_cache(request_id)
            return cache.get(key)
    
    @classmethod
    def cleanup_request(cls, request_id: str) -> None:
        """
        Clean up cache for completed request.
        
        Args:
            request_id: Unique identifier for the request to cleanup
        """
        with cls._lock:
            cls._caches.pop(request_id, None)
    
    @classmethod
    def has_cache(cls, request_id: str) -> bool:
        """
        Check if cache exists for a request.
        
        Args:
            request_id: Unique identifier for the request
            
        Returns:
            True if cache exists for the request, False otherwise
        """
        with cls._lock:
            return request_id in cls._caches
    
    @classmethod
    def get_cache_keys(cls, request_id: str) -> list:
        """
        Get all cache keys for a request.
        
        Args:
            request_id: Unique identifier for the request
            
        Returns:
            List of cache keys for the request
        """
        with cls._lock:
            cache = cls.get_cache(request_id)
            return list(cache.keys())