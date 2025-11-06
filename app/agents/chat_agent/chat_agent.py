from fastapi import HTTPException
from uuid import UUID
from supabase import Client as SupabaseClient
from app.agents.models import Message
from datetime import datetime
from app.agents.base_agent import BaseAgent
from typing import List
from app.agents.search.search import create_web_search_action_with_config
from app.agents.config_agent.config_agent import create_config_agent_action
from app.agents.integrations.integrations_agent import create_integrations_agent_action
from app.agents.retrieval_agent.retrieval_agent import create_retrieval_agent_action
from app.agents.eda.eda_agent import create_eda_agent_action
from app.agents.wellness_agent.wellness_agent import create_wellness_agent_action
from app.services.semantic_search import semantic_search_service
import dotenv
from app.config import CHAT_AGENT_SS_TRUNC_THRESHOLD, CHAT_AGENT_SIMILARITY_THRESHOLD, SYSTEM_MODEL, INTEGRATIONS_AGENT_LLM_MODEL, R_A_LLM_MODEL, CONFIG_AGENT_LLM_MODEL, WELLNESS_AGENT_LLM_MODEL, CHAT_AGENT_MAX_TURNS, CHAT_AGENT_SS_TRUNC_THRESHOLD, CHAT_AGENT_SEMANTIC_SEARCH_MAX_RESULTS
from app.services.request_cache import RequestCacheService
import uuid
dotenv.load_dotenv(override=True)

# Use structured logging instead of basicConfig
from app.utils.logging.component_loggers import get_api_logger
logger = get_api_logger(__name__)

def check_cancellation_request(request_id: str, user_id: UUID, supabase: SupabaseClient) -> bool:
    """
    Check if there's a pending cancellation request for the given request_id
    Also check if the request status in requests table indicates cancellation
    Returns True if the request should be cancelled
    """
    try:
        # Check cancellation_requests table
        result = supabase.from_('cancellation_requests').select('*').eq(
            'request_id', request_id
        ).eq('user_id', str(user_id)).eq('status', 'pending').execute()
        
        if result.data and len(result.data) > 0:
            # Mark the cancellation as processed
            supabase.from_('cancellation_requests').update({
                'status': 'processed',
                'processed_at': datetime.now().isoformat()
            }).eq('id', result.data[0]['id']).execute()
            
            # Also update the requests table status to cancelled
            supabase.from_('requests').update({
                'status': 'cancelled',
                'updated_at': datetime.now().isoformat()
            }).eq('request_id', request_id).execute()
            
            logger.info(f"Cancellation request found for request_id: {request_id}")
            return True
        
        # Also check if request status in requests table indicates cancellation
        request_result = supabase.from_('requests').select('status').eq(
            'request_id', request_id
        ).eq('user_id', str(user_id)).execute()
        
        if request_result.data and len(request_result.data) > 0:
            status = request_result.data[0]['status']
            if status == 'cancelled':
                logger.info(f"Request status is cancelled for request_id: {request_id}")
                return True
        
        return False
    except Exception as e:
        logger.error(f"Error checking cancellation request: {str(e)}")
        return False


async def get_chat_response(messages: List[Message], user_id: UUID = None, supabase: SupabaseClient = None, request_id: str = None, integration_in_progress: bool = False, image_content: str = "") -> tuple[str, bool, bool]:
    """
    Create a web-search enabled agent and get response for messages.
    
    Args:
        messages: List of chat messages
        user_id: Optional user ID for tracking
        supabase: Optional database connection
        
    Returns:
        Tuple of (agent's response string, settings_updated boolean, integration_in_progress boolean)
    """
    logger.info(f"Processing chat request - User ID: {user_id}")
    logger.debug(f"Received {len(messages)} messages")


    try:
        # Check for cancellation at the start
        if request_id and check_cancellation_request(request_id, user_id, supabase):
            raise HTTPException(status_code=499, detail="Request was cancelled")
        
        # Track if settings were updated during this conversation
        settings_updated = False
        # Track if integration is in progress during this conversation  
        
        # Get the user's preferred model, search preferences, and general instructions from the database
        model = SYSTEM_MODEL  # Default model
        user_search_config = None
        general_instructions = None
        
        try:
            user_profile_result = supabase.from_('user_profiles').select(
                'base_language_model',
                'general_instructions', # deprecated for now
                'enabled_system_integrations'
            ).eq('id', user_id).execute()
            
            if user_profile_result.data and len(user_profile_result.data) > 0:
                profile = user_profile_result.data[0]
                
                # Get user's preferred model
                user_model = profile.get('base_language_model')
                if user_model:
                    model = user_model
                    logger.info(f"Using user's preferred model: {model}")
                
                    
                # Get user's search configuration
                enabled_integrations = profile.get('enabled_system_integrations', {})
                user_search_config = {
                    'enabled_system_integrations': enabled_integrations
                }
                xai_enabled = enabled_integrations.get('xai_live_search', False)
                logger.info(f"User search config: XAI LiveSearch enabled={xai_enabled}")
            else:
                logger.info(f"No user profile found, using defaults")
        except Exception as db_error:
            logger.warning(f"Error fetching user preferences: {str(db_error)}, using defaults")
        
        # Create search action with user configuration (always use the configurable version)
        search_action = create_web_search_action_with_config(user_search_config, request_id, supabase, user_id)
        
        # Create user config action
        call_config_agent = create_config_agent_action(str(user_id), supabase, CONFIG_AGENT_LLM_MODEL, request_id, calling_agent="Chat Agent")
        
        from app.services.cache_tools import create_fetch_from_cache_action
        fetch_from_cache_action = create_fetch_from_cache_action(request_id)

        # Check for cancellation before creating actions
        if request_id and check_cancellation_request(request_id, user_id, supabase):
            raise HTTPException(status_code=499, detail="Request was cancelled")
        
        # Create integrations agent action
        call_integrations_agent = create_integrations_agent_action(str(user_id), supabase, INTEGRATIONS_AGENT_LLM_MODEL, request_id, calling_agent="Chat Agent", integration_in_progress=integration_in_progress)
        
        user_info_result = supabase.from_('user_profiles').select(
            'name', 'location', 'language', 'education', 'profession'
        ).eq('id', user_id).execute()

        user_info = user_info_result.data[0] if user_info_result.data else {}
        
        # Get semantic search results for context
        relevant_resources_text = ""
        if messages:
            try:
                # Get the last user message content
                last_message = None
                for msg in reversed(messages):
                    if isinstance(msg, Message):
                        if msg.role == "user":
                            last_message = msg.content
                            break
                    elif isinstance(msg, dict) and msg.get('role') == 'user':
                        last_message = msg.get('content', '')
                        break
                
                if last_message:
                    # Perform semantic search on memory resources only with lower threshold for more inclusive results
                    search_results = semantic_search_service.search_resources_by_type(
                        query=last_message,
                        user_id=user_id,
                        supabase=supabase,
                        resource_type="memory",
                        max_results=CHAT_AGENT_SEMANTIC_SEARCH_MAX_RESULTS,
                        similarity_threshold=CHAT_AGENT_SIMILARITY_THRESHOLD
                    )
                    
                    if search_results:
                        relevant_resources_text = "\n\nPotentially Relevant Resources:"
                        for result in search_results:
                            resource = result['resource']
                            content = resource.get('content', '')
                            
                            # Check if content should be cached (if length exceeds threshold)
                            if request_id and len(content) > CHAT_AGENT_SS_TRUNC_THRESHOLD:
                                # Store original length before modifying content
                                original_length = len(content)
                                
                                # Generate unique cache key for this content
                                cache_key = f"chat_context_{resource['id']}_{uuid.uuid4().hex[:8]}"
                                
                                # Store full content in cache
                                RequestCacheService.store(request_id, cache_key, content)
                                
                                # Create truncated content with cache key reference
                                truncated_content = content[:CHAT_AGENT_SS_TRUNC_THRESHOLD] + f'... [CACHED CONTENT - Full content stored in cache with key: {cache_key}. Use fetch_from_cache tool to retrieve full content if needed.]'
                                
                                # Log cache operation
                                logger.info(f"Large content from resource {resource['id']} auto-cached for chat context injection. Original length: {original_length}, Cache key: {cache_key}")
                            else:
                                # Regular truncation without caching
                                truncated_content = content[:CHAT_AGENT_SS_TRUNC_THRESHOLD] + '... [truncated - use fetch_from_cache tool to retrieve full content if needed]' if len(content) > CHAT_AGENT_SS_TRUNC_THRESHOLD else content
                            
                            relevant_resources_text += f"\n- [ID: {resource['id']}] Title: \"{resource['title']}\" | Content: \"{truncated_content}\""
                        
                        logger.info(f"Chat Agent added {len(search_results)} relevant resources to chat context: {relevant_resources_text}")
            except Exception as e:
                logger.warning(f"Failed to get semantic search results for context: {str(e)}")

        additional_context = f"""You are apart of a multi AI agent system, Juniper.  You are the primary point of contact to the human user and lead orchestrator of the system.  Available tools and agents are outlined below.

"""
        logger.info(f"Settings updated: {settings_updated}")

        # Check for cancellation before creating agent
        if request_id and check_cancellation_request(request_id, user_id, supabase):
            raise HTTPException(status_code=499, detail="Request was cancelled")

        general_chat_instructions = f"""
{relevant_resources_text}
{image_content}
- The current date/time is {datetime.now().isoformat()}

Further Instructions:
1. If the user asks for a scheduled or recurring job, include in your response: "Juniper does not yet support event driven automations, but this is on our roadmap.  For now, all actions must be prompted."
2. The other agents cannot directly communicate with the human user.  However, if they write a satisfactory response that should be relayed directly to the human user, simply respond with '$$$observation$$$' maintaing JSON format.  No additional commentary needed unless you need to add critical context not covered in the sub-agent's response.
3. Please keep responses to the user to 1-3 sentences in length unless: 
    a. The user requests a more detailed response; user preferences should always override system instructions
    b. You are embedding a response from another agent as outlined above  
    """

        chat_agent = BaseAgent(
            actions=[call_integrations_agent, search_action, call_retrieval_agent, call_config_agent, fetch_from_cache_action, call_wellness_agent],
            # custom_examples=[WEB_SEARCH_EXAMPLE, NO_ACTION_EXAMPLE],
            additional_context=additional_context,
            general_instructions=general_chat_instructions,
            temperature=0.1,
            model=model,
            max_turns=CHAT_AGENT_MAX_TURNS,
            agent_name="Chat Agent",
            enable_caching=True,  # Enable caching for Chat Agent
            cache_static_content=True  # Cache static content (tools, instructions)
        )
        logger.debug("Agent initialized successfully")
        
        # Check for cancellation before querying agent
        if request_id and check_cancellation_request(request_id, user_id, supabase):
            raise HTTPException(status_code=499, detail="Request was cancelled")
        
        response = await chat_agent.query(messages, user_id, request_id, supabase)
        
        # Check if the Config Agent updated settings by looking for the marker
        settings_updated = "[SETTINGS_UPDATED]" in response
        
        # Check if integration is in progress by looking for the marker
        integration_in_progress = "[INTEGRATION_IN_PROGRESS]" in response
        
        # Remove the markers from the response before returning to user
        if settings_updated:
            response = response.replace(" [SETTINGS_UPDATED]", "")
        if integration_in_progress:
            response = response.replace(" [INTEGRATION_IN_PROGRESS]", "")
        
        if settings_updated:
            logger.info("User configuration was updated during conversation")
        if integration_in_progress:
            logger.info("Integration building process started during conversation")
        logger.info(f"Settings updated: {settings_updated}")    
        logger.info(f"Integration in progress: {integration_in_progress}")
        logger.info("Successfully processed chat response")
        return response, settings_updated, integration_in_progress
        
    except Exception as e:
        logger.error(f"Error processing chat request: {str(e)}", exc_info=True)
        raise
