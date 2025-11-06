"""
Web Search Module with XAI LiveSearch Integration

This module provides web search capabilities with multiple fallback options:
1. XAI LiveSearch (real-time search via Grok chat completions)
2. Brave Search API  
3. DuckDuckGo search
4. Alternative search engines

XAI LiveSearch Integration:
- Uses the /v1/chat/completions endpoint with search_parameters
- Supports sources: web, news, X platform  
- Provides real-time data with citations
- Configurable per user preferences
- Can be forced on-demand by including "XAI" in queries

User Configuration:
- enabled_system_integrations["XAI Live Search"]: Enable/disable XAI LiveSearch
- Additional XAI settings can be configured through the system integrations
"""

from datetime import datetime
from duckduckgo_search import DDGS
import requests
import logging
import time
import re
from bs4 import BeautifulSoup
import random
import os
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any
from uuid import UUID
from app.agents.models import Action
from app.config import THIRD_PARTY_SERVICE_TIMEOUT, SEARCH_AGENT_DUCKDUCKGO_MAX_RESULTS, WEB_APP_URL

load_dotenv(override=True)

logger = logging.getLogger(__name__)


def increment_xai_live_search_usage_sync(user_id: UUID, supabase) -> None:
    """Increment user's monthly XAI Live Search usage counter with cost tracking"""
    try:
        # Increment xai_live_search_month for the user with automatic cost calculation
        result = supabase.rpc(
            'increment_user_usage_auto_cost',
            {
                'user_id': str(user_id),
                'usage_type': 'xai_live_search_month'
            }
        ).execute()
        
        if result.data is None:
            logger.error(f"Failed to increment XAI Live Search usage for user {user_id}")
        else:
            logger.info(f"Incremented XAI Live Search usage and cost for user {user_id}")
            
            # Send Stripe meter event for XAI Live Search usage
            try:
                from app.services.stripe_service import stripe_service
                import asyncio
                
                # Get user profile to get stripe_customer_id
                profile_result = supabase.from_('user_profiles').select('stripe_customer_id').eq('id', str(user_id)).execute()
                if profile_result.data and profile_result.data[0].get('stripe_customer_id'):
                    stripe_customer_id = profile_result.data[0]['stripe_customer_id']
                    
                    # Run the async Stripe call in the current event loop
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # If we're already in an event loop, schedule the task
                        asyncio.create_task(stripe_service.send_xai_live_search_event(
                            stripe_customer_id=stripe_customer_id,
                            user_id=user_id
                        ))
                    else:
                        # If not in an event loop, run directly
                        asyncio.run(stripe_service.send_xai_live_search_event(
                            stripe_customer_id=stripe_customer_id,
                            user_id=user_id
                        ))
                else:
                    logger.warning(f"No Stripe customer ID found for user {user_id} - skipping XAI Live Search meter event")
            except Exception as stripe_error:
                logger.error(f"Error sending Stripe meter event for XAI Live Search usage user {user_id}: {str(stripe_error)}")
                # Don't re-raise - Stripe failures shouldn't block the request
            
    except Exception as e:
        logger.error(f"Error incrementing XAI Live Search usage for user {user_id}: {str(e)}")


def web_search(query: str, use_xai_livesearch: bool = False, xai_config: Optional[Dict[str, Any]] = None, user_id: Optional[UUID] = None, supabase = None) -> tuple[str, bool]:
    """Search the web using XAI LiveSearch, Brave Search API, or fallback search engines.
    
    Args:
        query: The search query
        use_xai_livesearch: Whether to use XAI LiveSearch instead of Brave Search
        xai_config: XAI LiveSearch configuration parameters including sources, country, etc.
    """
    logger.info(f"Performing web search with query: {query}, use_xai_livesearch: {use_xai_livesearch}")
    
    xai_used_successfully = False
    ubp_limit_reached = False
    
    # Try XAI LiveSearch if enabled
    if use_xai_livesearch:
        logger.info("Attempting XAI LiveSearch as primary search method")
        
        # Check UBP limits before attempting XAI LiveSearch
        if user_id and supabase:
            try:
                from app.auth import check_ubp_limits
                
                # Get user profile for UBP checking
                profile_result = supabase.from_('user_profiles').select('ubp_current, ubp_max, subscription_tier').eq('id', str(user_id)).execute()
                if profile_result.data:
                    user_profile = profile_result.data[0]
                    ubp_check = check_ubp_limits(user_profile)
                    
                    if not ubp_check['can_proceed']:
                        logger.warning(f"XAI Live Search blocked for user {user_id} - UBP limit exceeded, falling back to regular search. UBP check details: {ubp_check}")
                        ubp_limit_reached = True
                    else:
                        logger.info(f"XAI Live Search allowed for user {user_id}. UBP check details: {ubp_check}")
            except Exception as limit_error:
                logger.error(f"Error checking UBP limits for XAI Live Search user {user_id}: {str(limit_error)}")
                # Continue with request if limit check fails (fail-open)
        
        # Only attempt XAI LiveSearch if UBP limits allow it
        if not ubp_limit_reached:
            result = _try_xai_livesearch(query, xai_config, user_id=user_id, supabase=supabase)
            if result and "No results found" not in result:
                logger.info("XAI LiveSearch succeeded, returning results")
                xai_used_successfully = True
                
                # Increment usage counter if user_id and supabase are provided
                if user_id and supabase:
                    increment_xai_live_search_usage_sync(user_id, supabase)
                
                return result, xai_used_successfully
            logger.warning("XAI LiveSearch failed. Falling back to Brave search.")
    else:
        logger.info("Skipping XAI LiveSearch, proceeding with Brave search")
    
    # Try Brave Search API
    logger.info("Attempting Brave Search as primary/fallback search method")
    result = _try_brave_search(query)
    
    # If Brave search fails, try DuckDuckGo
    if not result or "No results found" in result:
        logger.warning("Brave search failed. Trying DuckDuckGo search.")
        result = _try_duckduckgo_search(query)
        
        # If DuckDuckGo fails, use alternative search
        if not result or "No results found" in result:
            logger.warning("DuckDuckGo search failed. Trying alternative search.")
            result = _try_alternative_search(query)
    
    # Append UBP limit message if XAI LiveSearch was requested but blocked due to limits
    if ubp_limit_reached and result:
        result += f"\n\n Usage limit reached for the user - fell back to base search engine.  Please notify user and let them know they can manage their account in the web app at {WEB_APP_URL}.  Also, offer to disable XAI Live Search for them."
    
    return result, xai_used_successfully


def _try_xai_livesearch(query: str, config: Optional[Dict[str, Any]] = None, max_retries: int = 3, user_id: Optional[UUID] = None, supabase = None) -> str:
    """Try to search using XAI LiveSearch API with retry logic."""
    
    xai_api_key = os.getenv("XAI_API_KEY")
    if not xai_api_key:
        logger.warning("XAI API key not found in environment variables")
        return None
    
    # Default configuration
    default_config = {
        "mode": "auto",
        "return_citations": True,
        "sources": [{"type": "web"}, {"type": "x"}],
        "max_search_results": 15
    }
    
    # Merge user config with defaults
    search_config = default_config.copy()
    if config:
        # Use the sources directly if provided (new format)
        if config.get("sources"):
            search_config["sources"] = config["sources"]

        if config.get("safe_search") is not None:
            search_config["safe_search"] = config["safe_search"]
        
        if config.get("from_date"):
            search_config["from_date"] = config["from_date"]
        
        if config.get("to_date"):
            search_config["to_date"] = config["to_date"]

        if config.get("country"):
            search_config["country"] = config["country"]

        if config.get("return_citations") is not None:
            search_config["return_citations"] = config["return_citations"]

        if config.get("max_search_results") is not None:
            search_config["max_search_results"] = config["max_search_results"]
        


    
    # Use chat completions endpoint with search parameters
    url = "https://api.x.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {xai_api_key}",
        "Content-Type": "application/json"
    }
    
    # Build the request payload for chat completions with search
    payload = {
        "messages": [
            {
                "role": "user",
                "content": f"Search for and provide information about: {query}"
            }
        ],
        "search_parameters": search_config,
        "model": "grok-3-latest",
        "max_tokens": 4000,
        "temperature": 0.3
    }
    
    # Log the search configuration and payload being sent to XAI API
    logger.info(f"XAI LiveSearch configuration: {search_config}")
    logger.debug(f"XAI LiveSearch full payload: {payload}")
    
    delay = 0.5  # initial delay in seconds
    
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=THIRD_PARTY_SERVICE_TIMEOUT)
            
            if response.status_code == 200:
                data = response.json()
                
                # Extract the message content from chat completion response
                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    if "message" in choice and "content" in choice["message"]:
                        content = choice["message"]["content"]
                        
                        # Add citation information if available
                        if search_config.get("return_citations") and "citations" in data:
                            citations = data.get("citations", [])
                            if citations:
                                content += "\n\nSources:\n"
                                max_citations = search_config.get("max_search_results", 15)
                                for i, citation in enumerate(citations[:max_citations], 1):
                                    content += f"{i}. {citation}\n"
                        
                        logger.debug(f"XAI LiveSearch returned response with {len(content)} characters")
                        return content
                
                return "No results found."
            
            elif response.status_code == 429 and attempt < max_retries:  # Rate limit
                logger.warning(f"XAI LiveSearch rate limit encountered. Retrying in {delay} seconds (attempt {attempt}/{max_retries})...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
                continue
            else:
                logger.error(f"XAI LiveSearch API returned status code {response.status_code}: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error during XAI LiveSearch (attempt {attempt}): {str(e)}")
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 1.5
                continue
            return None


def _try_brave_search(query: str, max_retries: int = 3) -> str:
    """Try to search using Brave Search API with retry logic."""
    brave_api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not brave_api_key:
        logger.warning("Brave API key not found in environment variables")
        return None
        
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": brave_api_key
    }
    params = {
        "q": query
    }
    
    delay = 0.5  # initial delay in seconds
    
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=THIRD_PARTY_SERVICE_TIMEOUT)
            
            if response.status_code == 200:
                data = response.json()
                web_results = data.get("web", {}).get("results", [])
                
                if not web_results:
                    return "No results found."
                
                snippets = []
                for result in web_results[:5]:  # Limit to top 5 results
                    title = result.get("title", "")
                    description = result.get("description", "")
                    url = result.get("url", "")
                    
                    snippets.append(f"{title}\n{description}\nSource: {url}")
                
                logger.debug(f"Brave search returned {len(snippets)} results")
                return "\n\n".join(snippets)
            
            elif response.status_code == 429 and attempt < max_retries:  # Rate limit
                logger.warning(f"Rate limit encountered. Retrying in {delay} seconds (attempt {attempt}/{max_retries})...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
                continue
            else:
                logger.error(f"Brave search API returned status code {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error during Brave search (attempt {attempt}): {str(e)}")
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 1.5
                continue
            return None


def _try_duckduckgo_search(query: str, max_retries: int = 5) -> str:
    """Try to search using DuckDuckGo with retry logic."""
    delay = 0.5  # initial delay in seconds
    
    for attempt in range(1, max_retries + 1):
        try:
            ddgs = DDGS()
            results = list(ddgs.text(query, max_results=SEARCH_AGENT_DUCKDUCKGO_MAX_RESULTS))
            if not results:
                return "No results found."

            # Format the search results
            snippets = []
            for result in results:
                if isinstance(result, dict):
                    title = result.get('title', '')
                    snippet = result.get('body', '') or result.get('snippet', '') or result.get('text', '')
                    link = result.get('href', '') or result.get('link', '')
                else:
                    # If result is not a dict, convert to string
                    snippet = str(result)
                    title = ''
                    link = ''

                if title and link:
                    snippets.append(f"{title}\n{snippet}\nSource: {link}")
                else:
                    snippets.append(snippet)

            logger.debug(f"DuckDuckGo search returned {len(snippets)} results")
            return "\n\n".join(snippets)
        
        except Exception as e:
            error_str = str(e)
            logger.error(f"Error during DuckDuckGo search (attempt {attempt}): {error_str}")
            
            # Check for rate limit error (202 Ratelimit)
            if ("202" in error_str and "Ratelimit" in error_str) and attempt < max_retries:
                logger.warning(f"Rate limit encountered. Retrying in {delay} seconds (attempt {attempt}/{max_retries})...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
                continue
            elif attempt < max_retries:
                # Other error but still have retries left
                logger.warning(f"Search error encountered. Retrying in {delay} seconds (attempt {attempt}/{max_retries})...")
                time.sleep(delay)
                delay *= 1.5  # Slightly less aggressive backoff for other errors
                continue
            else:
                # Out of retries, return None to trigger fallback
                logger.error(f"DuckDuckGo search failed after {max_retries} attempts.")
                return None


def _try_alternative_search(query: str) -> str:
    """Fallback search implementation using a direct web scraping approach."""
    try:
        # List of common search engines with their search URL patterns and selectors
        search_engines = [
            {
                'name': 'Bing',
                'url': f"https://www.bing.com/search?q={query.replace(' ', '+')}",
                'result_selector': 'li.b_algo',
                'title_selector': 'h2',
                'snippet_selector': '.b_caption p',
                'link_selector': 'h2 a',
                'link_attribute': 'href'
            },
            {
                'name': 'Yahoo',
                'url': f"https://search.yahoo.com/search?p={query.replace(' ', '+')}",
                'result_selector': 'div.algo',
                'title_selector': 'h3',
                'snippet_selector': 'div.compText',
                'link_selector': 'h3 a',
                'link_attribute': 'href'
            }
        ]
        
        # Randomly select a search engine to avoid pattern detection
        search_engine = random.choice(search_engines)
        logger.info(f"Using {search_engine['name']} as fallback search engine")
        
        # Set a more realistic user agent
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.google.com/',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        response = requests.get(search_engine['url'], headers=headers, timeout=THIRD_PARTY_SERVICE_TIMEOUT)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            results = soup.select(search_engine['result_selector'])
            
            if not results:
                logger.warning(f"No results found from {search_engine['name']} search")
                return f"No results found from alternative search ({search_engine['name']})."
            
            snippets = []
            for i, result in enumerate(results[:5]):  # Limit to top 5 results
                try:
                    title_elem = result.select_one(search_engine['title_selector'])
                    title = title_elem.get_text().strip() if title_elem else "No title"
                    
                    snippet_elem = result.select_one(search_engine['snippet_selector'])
                    snippet = snippet_elem.get_text().strip() if snippet_elem else "No description"
                    
                    link_elem = result.select_one(search_engine['link_selector'])
                    link = link_elem.get(search_engine['link_attribute']) if link_elem else "#"
                    
                    # Clean up the link if needed
                    if search_engine['name'] == 'Yahoo' and 'RU=' in link:
                        # Yahoo uses redirect URLs, extract the actual URL
                        match = re.search(r'RU=([^/]*)', link)
                        if match:
                            link = requests.utils.unquote(match.group(1))
                    
                    snippets.append(f"{title}\n{snippet}\nSource: {link}")
                except Exception as e:
                    logger.error(f"Error parsing result {i}: {str(e)}")
                    continue
            
            if not snippets:
                return f"Failed to parse any results from {search_engine['name']}."
                
            logger.debug(f"Alternative search returned {len(snippets)} results")
            return "\n\n".join(snippets)
        else:
            logger.error(f"Alternative search returned status code {response.status_code}")
            return f"Search failed. {search_engine['name']} returned error code {response.status_code}."
    
    except Exception as e:
        logger.error(f"Error during alternative search: {str(e)}")
        return f"Both search methods failed. Last error: {str(e)}"


def _clean_handles(handles: List[str]) -> List[str]:
    """Clean X handles by removing @ symbol if present and trimming whitespace."""
    if not handles:
        return handles
    cleaned = []
    for handle in handles:
        if handle and handle.strip():  # Only process non-empty handles
            cleaned_handle = handle.strip()  # Remove whitespace first
            if cleaned_handle.startswith('@'):
                cleaned_handle = cleaned_handle[1:]  # Remove only the first @ symbol
            cleaned.append(cleaned_handle)
    return cleaned


def web_search_with_config(query: str, user_config: Optional[Dict[str, Any]] = None, force_xai: bool = False, handles: Optional[List[str]] = None, from_date: Optional[str] = None, to_date: Optional[str] = None, user_id: Optional[UUID] = None, supabase = None) -> str:
    """
    Enhanced web search function that uses user configuration to determine search method.
    
    Args:
        query: The search query
        user_config: User's search preferences from their profile
        force_xai: Force use of XAI LiveSearch even if disabled in config
        handles: Specific X handles to search when using XAI LiveSearch (@ symbol will be automatically stripped)
        from_date: Start date for search data in ISO8601 format (YYYY-MM-DD)
        to_date: End date for search data in ISO8601 format (YYYY-MM-DD)
    """
    # Log the input parameters for debugging
    logger.info(f"web_search_with_config called with force_xai={force_xai}, handles={handles}, from_date={from_date}, to_date={to_date}")
    if user_config:
        enabled_integrations = user_config.get("enabled_system_integrations", {})
        xai_enabled = enabled_integrations.get("xai_live_search", False)
        logger.info(f"User config XAI Live Search enabled: {xai_enabled}")
    else:
        logger.info("No user config provided")
    
    # Default to regular search
    use_xai = False
    xai_config = None
    
    # Clean handles by removing @ symbols if present
    if handles:
        handles = _clean_handles(handles)
        logger.debug(f"Cleaned handles: {handles}")
    
    # Check if XAI LiveSearch should be used
    xai_enabled = False
    if user_config:
        enabled_integrations = user_config.get("enabled_system_integrations", {})
        xai_enabled = enabled_integrations.get("xai_live_search", False)
    
    if force_xai or xai_enabled:
        use_xai = True
        logger.info(f"XAI LiveSearch will be used. Reason: force_xai={force_xai}, config_enabled={xai_enabled}")
        
        # Build XAI configuration from user settings and parameters
        if user_config or handles or from_date or to_date:
            # Default sources
            mapped_sources = [{"type": "web"}]
            
            # If handles are provided, add X source with those handles
            if handles:
                x_config = {"type": "x", "x_handles": handles}
                mapped_sources.append(x_config)
                logger.debug(f"Added X source with handles: {x_config}")
            else:
                # If no handles provided via parameter, add basic X source
                # (since we simplified config, we default to basic X search when XAI is enabled)
                mapped_sources.append({"type": "x"})
                logger.debug("Added basic X source (no specific handles)")
            
            xai_config = {
                "sources": mapped_sources,
                "mode": "auto",
                "return_citations": True,
                "max_search_results": 15
            }
            
            # Add date range parameters if provided
            if from_date:
                xai_config["from_date"] = from_date
                logger.debug(f"Added from_date: {from_date}")
            
            if to_date:
                xai_config["to_date"] = to_date
                logger.debug(f"Added to_date: {to_date}")
            
            # Add optional parameters from user config
            # Note: Additional XAI configuration (country, safe_search) can be added here
            # when those settings are moved to enabled_system_integrations structure
        else:
            # Default XAI config when no user config or handles provided
            xai_config = {
                "sources": [{"type": "web"}],
                "mode": "auto", 
                "return_citations": True,
                "max_search_results": 15
            }
            logger.debug("Using default XAI config (no user config or handles)")
    else:
        logger.info(f"Regular search will be used. force_xai={force_xai}, config_enabled={xai_enabled}")
    
    result, xai_used = web_search(query, use_xai_livesearch=use_xai, xai_config=xai_config, user_id=user_id, supabase=supabase)
    return result


def unified_search_handler(input_str: str, user_config: Optional[Dict[str, Any]] = None, user_id: Optional[UUID] = None, supabase = None) -> str:
    """Unified handler for web search that can parse JSON tool calls or plain text."""
    import json
    
    # Try to parse as JSON for tool call with parameters
    try:
        parsed_input = json.loads(input_str)
        if isinstance(parsed_input, dict):
            query = parsed_input.get("query", "")
            handles = parsed_input.get("handles", [])
            from_date = parsed_input.get("from_date")
            to_date = parsed_input.get("to_date")
            # XAI LiveSearch is now determined automatically by user's profile setting
            return web_search_with_config(query, user_config, force_xai=False, handles=handles, from_date=from_date, to_date=to_date, user_id=user_id, supabase=supabase)
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Fallback to treating input as a simple query string
    return web_search_with_config(input_str, user_config, force_xai=False, user_id=user_id, supabase=supabase)


def create_web_search_action_with_config(user_config: Optional[Dict[str, Any]] = None, request_id: str = None, supabase = None, user_id: Optional[UUID] = None) -> Action:
    """Create a web search action with user configuration baked in."""
    
    # Determine the description based on user config
    base_description = "Call this action to search the web for current information using real-time search capabilities.  Research tasks should be delegated to the integrations agent."
    xai_enabled = False
    if user_config:
        enabled_integrations = user_config.get("enabled_system_integrations", {})
        xai_enabled = enabled_integrations.get("xai_live_search", False)
    
    if xai_enabled:
        description = f"{base_description} XAI LiveSearch is enabled for real-time web and X platform search."
    else:
        description = f"{base_description} Standard web search is enabled."
    
    def search_handler_with_status(input_str: str) -> str:
        """Search handler that updates request status to 'searching'."""
        # Update request status to "searching"
        if request_id and supabase:
            try:
                supabase.from_('requests').update({
                    'status': 'searching',
                    'updated_at': datetime.now().isoformat()
                }).eq('request_id', request_id).execute()
                logger.info(f"Updated request status to 'searching' for request_id: {request_id}")
            except Exception as db_error:
                logger.error(f"Failed to update request status to searching: {str(db_error)}")
        
        # Call the original search handler
        return unified_search_handler(input_str, user_config, user_id, supabase)
    
    return Action(
        name="web_search",
        description=description,
        parameters={
            "query": {
                "type": "string",
                "description": "The search query e.g. 'Tesla stock news'"
            },
            "handles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific X handles to search (e.g., ['@elonmusk', 'tesla'] or ['elonmusk', 'tesla'] - @ symbols are automatically stripped). Only works if XAI LiveSearch is enabled in user profile.",
                "default": []
            },
            "from_date": {
                "type": "string",
                "description": "Start date for search data in ISO8601 format (YYYY-MM-DD). Only works if XAI LiveSearch is enabled in user profile.",
                "default": None
            },
            "to_date": {
                "type": "string",
                "description": "End date for search data in ISO8601 format (YYYY-MM-DD). Only works if XAI LiveSearch is enabled in user profile.",
                "default": None
            }
        },
        returns="Real-time information from web sources with citations when available",
        example=f'Action: web_search: {{"query": "Latest AI developments {datetime.now().year}"}}',
        handler=search_handler_with_status
    )