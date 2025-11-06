"""
Cache Tools:

Common cache tools for agents to interact with the RequestCacheService.
"""

import json
from typing import Any
from app.services.request_cache import RequestCacheService
from app.agents.models import Action


def fetch_from_cache(input_str: str, request_id: str = None) -> str:
    """
    Fetch data from request cache using cache key.
    
    Args:
        input_str: JSON string with cache_key parameter
        request_id: Unique identifier for the request
        
    Returns:
        Cached data if found, error message otherwise
    """
    if not request_id:
        return "Error: Request ID is required to fetch from cache"
    
    try:
        # Parse input
        params = json.loads(input_str)
        
        # Validate required fields
        if "cache_key" not in params:
            return "Error: 'cache_key' field is required"
        
        cache_key = params["cache_key"]
        
        # Retrieve data from cache
        cached_data = RequestCacheService.retrieve(request_id, cache_key)
        
        if cached_data is None:
            return f"Error: No data found for cache key '{cache_key}'"
        
        formatted_cache_data = f"Cached Data:\n```\n{cached_data}\n```"
        # Return the cached data
        return str(formatted_cache_data)
        
    except json.JSONDecodeError:
        return "Error: Invalid JSON input"
    except Exception as e:
        return f"Error fetching from cache: {str(e)}"


def create_fetch_from_cache_action(request_id: str = None) -> Action:
    """
    Create a fetch_from_cache action with request_id injected.
    
    Args:
        request_id: Unique identifier for the request
        
    Returns:
        Action for fetching data from request cache
    """
    return Action(
        name="fetch_from_cache",
        description="Fetch large data from request cache using cache key.",
        parameters={
            "cache_key": {"type": "string", "description": "Cache key"}
        },
        returns="Cached data content",
        example='Action: fetch_from_cache: {"cache_key": "integration_scripts_gmail_123"}',
        handler=lambda input_str: fetch_from_cache(input_str, request_id)
    )