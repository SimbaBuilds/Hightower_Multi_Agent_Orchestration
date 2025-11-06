"""
Integrations Manager Agent:

A specialized agent that handles integration management and tool discovery.
Provides tools for fetching integration information, managing memories, and accessing service tools.
Extends BaseAgent with integration-specific context and tools.
"""

import json
import time
import asyncio
from typing import List
from uuid import UUID
from datetime import datetime
from app.agents.base_agent import BaseAgent
from app.agents.models import Message, Action
from supabase import Client as SupabaseClient
from app.utils.logging.component_loggers import get_agent_logger, log_agent_event
from app.agents.retrieval_agent.retrieval_agent import create_retrieval_agent_action
from dotenv import load_dotenv
from app.config import NUM_RECENT_INTEGRATIONS_INJECTED, INTEGRATIONS_AGENT_RESOURCE_TRUNC_THRESHOLD, INTEGRATIONS_AGENT_LLM_MODEL, INTEGRATIONS_AGENT_MAX_TURNS, INTEGRATIONS_AGENT_RESOURCE_TRUNC_THRESHOLD, INTEGRATIONS_AGENT_SERVICE_TOOL_TRUNC_THRESHOLD, INTEGRATIONS_AGENT_RECENT_INTEGRATIONS_DEFAULT_LIMIT, INTEGRATION_COMPLETION_LLM, R_A_LLM_MODEL, SERVICE_TOOL_DEFAULT_TIMEOUT, SERVICE_TOOL_MAX_TIMEOUT
from app.utils.llm_classifier import classify_service_types
from app.services.request_cache import RequestCacheService
import uuid

load_dotenv(override=True)

logger = get_agent_logger("Integrations Agent", __name__)


def initial_md_fetch(input_str: str, user_id: str = None, supabase: SupabaseClient = None, request_id: str = None) -> str:
    """
    Fetch names and descriptions of all available service tools.
    When service_name is provided, also includes associated resources with truncated content.
    
    Args:
        input_str: Optional filter parameters (can be empty) or service_name
        user_id: UUID of the user
        supabase: Supabase client for database operations
        request_id: Request ID for cache operations
        
    Returns:
        List of service tool names and descriptions, optionally with associated resources
    """
    if not supabase:
        return "Error: Database connection is required"
    
    try:
        # Parse optional filters
        filters = {}
        service_id = None
        service_tags = []
        
        if input_str and input_str.strip():
            try:
                filters = json.loads(input_str)
            except json.JSONDecodeError:
                # If not JSON, treat as a simple search term or service name
                filters = {"search": input_str}
        
        # Start with base query for service tools
        query = supabase.from_('service_tools').select('name, description, category, service_id, is_active')
        
        # Filter by service if specified
        if 'service_name' in filters:
            # Get service ID and its associated tag IDs
            service_result = supabase.from_('services').select(
                'id, tag_1_id, tag_2_id, tag_3_id, tag_4_id, tag_5_id'
            ).eq('service_name', filters['service_name']).execute()
            
            if service_result.data:
                service_data = service_result.data[0]
                service_id = service_data['id']
                query = query.eq('service_id', service_id)
                
                # Collect ALL tag IDs associated with this service
                service_tag_ids = []
                for i in range(1, 6):
                    tag_id = service_data.get(f'tag_{i}_id')
                    if tag_id:
                        service_tag_ids.append(tag_id)
                
                # Get the tag names and types for these IDs to understand what we're searching for
                if service_tag_ids:
                    tags_info_result = supabase.from_('tags').select('id, name, type').in_('id', service_tag_ids).execute()
                    if tags_info_result.data:
                        tag_names = [tag['name'] for tag in tags_info_result.data]
                        logger.info(f"Service '{filters['service_name']}' has tags: {tag_names}")
                
                # For resource fetching, we want ALL tags associated with this service AND service type
                # This includes both 'service' tags (like 'Gmail') and 'service_type' tags (like 'Email')
                service_tags = service_tag_ids.copy()  # Start with service's direct tag associations
                
                # Also find the service's own tag if it exists (type='service', name=service_name)
                service_own_tag_result = supabase.from_('tags').select('id').eq('type', 'service').eq('name', filters['service_name']).execute()
                if service_own_tag_result.data:
                    service_own_tag_id = service_own_tag_result.data[0]['id']
                    if service_own_tag_id not in service_tags:
                        service_tags.append(service_own_tag_id)
                        
                logger.info(f"Total service-related tag IDs for resource search: {len(service_tags)}")

                log_agent_event(
                    logger,
                    f"Collected {len(service_tags)} service and service-type tags for '{filters['service_name']}' resource fetching",
                    agent_name="Integrations Agent",
                    user_id=str(user_id) if user_id else "unknown",
                    action="service_tags_collected",
                    service_name=filters['service_name'],
                    tag_count=len(service_tags)
                )

            else:
                return f"Service '{filters['service_name']}' not found"
        
        # Only get active tools by default
        query = query.eq('is_active', True)
        
        result = query.execute()
        
        response = ""
        
        if result.data:
            tools_list = []
            for tool in result.data:
                # Apply client-side search filter if needed
                if 'search' in filters:
                    search_term = filters['search'].lower()
                    if search_term not in tool['name'].lower() and \
                       (not tool['description'] or search_term not in tool['description'].lower()):
                        continue
                
                desc = tool['description'] or "No description available"
                category = tool['category'] or "General"
                tools_list.append(f"- {tool['name']}: {desc} (Category: {category})")
            
            if tools_list:
                response = "Available service tools:\n" + "\n".join(tools_list)
            else:
                response = "No service tools found matching your criteria"
        else:
            response = "No service tools available"
        
        # If we have a specific service and user_id, fetch associated resources
        if service_id and service_tags and user_id:
            try:
                # Log resource fetch attempt
                log_agent_event(
                    logger,
                    f"Fetching truncated resources for service {filters.get('service_name', 'unknown')}",
                    agent_name="Integrations Agent",
                    user_id=str(user_id),
                    action="fetch_service_resources_truncated",
                    service_name=filters.get('service_name'),
                    service_id=service_id,
                    tag_count=len(service_tags)
                )
                
                # Use simple, reliable approach - fetch resources for each tag individually and combine
                # This avoids complex OR queries that timeout in Supabase 2.15.1
                logger.info(f"Fetching resources for {len(service_tags)} service-related tags")
                all_resources = []
                found_resource_ids = set()
                
                # Limit to 2 most important tags to prevent timeout while maintaining core functionality
                primary_tags = service_tags[:2] if len(service_tags) > 2 else service_tags
                if len(service_tags) > 2:
                    logger.info(f"Limited to {len(primary_tags)} primary tags (was {len(service_tags)}) to prevent timeout")
                
                for tag_id in primary_tags:
                    try:
                        # Simple individual queries for each tag column
                        for tag_column in ['tag_1_id', 'tag_2_id', 'tag_3_id', 'tag_4_id', 'tag_5_id']:
                            try:
                                tag_resources = supabase.from_('resources').select(
                                    'id, title, type, content, instructions, relevance_score, last_accessed'
                                ).eq('user_id', user_id).eq(tag_column, tag_id).limit(5).execute()  # Limit per tag column
                                
                                # Add unique resources
                                for resource in tag_resources.data:
                                    if resource['id'] not in found_resource_ids:
                                        all_resources.append(resource)
                                        found_resource_ids.add(resource['id'])
                                        
                            except Exception as col_error:
                                logger.debug(f"Column {tag_column} query failed for tag {tag_id}: {col_error}")
                                continue
                                
                    except Exception as tag_error:
                        logger.warning(f"Failed to fetch resources for tag {tag_id}: {tag_error}")
                        continue
                
                # Sort by relevance score
                all_resources.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
                
                # Limit total results to prevent response bloat
                if len(all_resources) > 20:
                    all_resources = all_resources[:20]
                    logger.info(f"Limited results to 20 resources (was {len(all_resources)})")
                
                # Create result object
                class MockResult:
                    def __init__(self, data):
                        self.data = data
                
                resources_result = MockResult(all_resources)
                logger.info(f"Successfully found {len(all_resources)} unique resources for service tags")
                
                if resources_result.data:
                    # Include content preview in logs for fetched resources
                    resource_content_previews = []
                    for resource in resources_result.data:
                        content = resource.get('content', '')
                        preview = content[:100] + '...' if len(content) > 100 else content
                        resource_content_previews.append({
                            "resource_id": resource['id'],
                            "title": resource['title'],
                            "content_preview": preview,
                            "content_length": len(content)
                        })
                    logger.info(f"Resource content previews: {resource_content_previews}")
                    # Log successful resource retrieval
                    log_agent_event(
                        logger,
                        f"Found {len(resources_result.data)} truncated resources for service",
                        agent_name="Integrations Agent",
                        user_id=str(user_id),
                        action="service_resources_found",
                        resource_count=len(resources_result.data),
                        service_name=filters.get('service_name'),
                        resource_content_previews=resource_content_previews
                    )
                    
                    response += "\n\nAssociated Resources:"
                    
                    # Group resources by type
                    resources_by_type = {}
                    for resource in resources_result.data:
                        res_type = resource.get('type', 'general')
                        if res_type not in resources_by_type:
                            resources_by_type[res_type] = []
                        resources_by_type[res_type].append(resource)
                    
                    # Display resources grouped by type
                    for res_type, resources in resources_by_type.items():
                        response += f"\n\n[{res_type.title()} Resources]"
                        for resource in resources:
                            response += f"\n- Resource ID: {resource['id']}"
                            response += f"\n  Title: {resource['title']}"
                            response += f"\n  Relevance: {resource['relevance_score']}"
                            
                            if resource.get('instructions'):
                                response += f"\n  Instructions: \"{resource['instructions']}\""
                            
                            # Truncate content
                            content = resource.get('content', '')
                            if len(content) > INTEGRATIONS_AGENT_RESOURCE_TRUNC_THRESHOLD:
                                response += f"\n  Content: {content[:INTEGRATIONS_AGENT_RESOURCE_TRUNC_THRESHOLD]}...[truncated - {len(content)} chars total]"
                            else:
                                response += f"\n  Content: {content}"
                            
                            # Add last accessed info
                            if resource.get('last_accessed'):
                                last_accessed = datetime.fromisoformat(
                                    resource['last_accessed'].replace('Z', '+00:00')
                                ).strftime("%Y-%m-%d")
                                response += f"\n  Last accessed: {last_accessed}"
                
            except Exception as e:
                logger.warning(f"Failed to fetch resources for service: {e}")
                # Don't fail the whole request if resources fail
        
        return response
            
    except Exception as e:
        logger.error(f"Error fetching service tools: {str(e)}")
        return f"Error fetching service tools: {str(e)}"


def fetch_tool_data(input_str: str, user_id: str = None, supabase: SupabaseClient = None, request_id: str = None, agent_instance=None) -> str:
    """
    Fetch tool data including full tool definitions. Resources are only fetched if resource_ids are provided.
    
    Args:
        input_str: JSON with tool_name and optional resource_ids, or tool name string
        user_id: UUID of the user
        supabase: Supabase client for database operations
        request_id: Request ID for cache operations
        
    Returns:
        Detailed tool definition, and resources (full content) only if resource_ids are specified
    """
    if not supabase:
        return "Error: Database connection is required"
    
    if not user_id:
        return "Error: User ID is required"
    
    try:
        # Parse input
        resource_ids_filter = None
        if input_str.startswith('{'):
            params = json.loads(input_str)
            tool_name = params.get('tool_name')
            resource_ids_filter = params.get('resource_ids', None)
        else:
            tool_name = input_str.strip()
        
        if not tool_name:
            return "Error: tool_name is required"
        
        # Get the tool from service_tools table
        tool_result = supabase.from_('service_tools').select('*').eq('name', tool_name).execute()
        
        if not tool_result.data:
            return f"Error: Tool '{tool_name}' not found"
        
        tool = tool_result.data[0]
        
        # Silent intelligence upgrade based on tool requirements
        required_intelligence = tool.get('required_intelligence', 1)
        if required_intelligence > 1 and agent_instance:
            try:
                agent_instance.upgrade_intelligence(required_intelligence)
            except Exception as e:
                logger.warning(f"Failed to upgrade intelligence for tool {tool_name}: {e}")
        
        # Build tool definition response
        response = f"Tool: {tool['name']}\n"
        response += f"Display Name: {tool['display_name'] or tool['name']}\n"
        response += f"Description: {tool['description'] or 'No description'}\n"
        response += f"Service ID: {tool['service_id']}\n"
        response += f"Category: {tool['category'] or 'General'}\n"
        response += f"Version: {tool['version'] or 'Not specified'}\n"
        response += f"Active: {tool['is_active']}\n"
        response += f"Intelligence Level: {required_intelligence}\n"
        
        if tool['parameters']:
            response += f"Parameters: {json.dumps(tool['parameters'], indent=2)}\n"
        
        if tool['returns']:
            response += f"Returns: {json.dumps(tool['returns'], indent=2)}\n"
        
        if tool['example']:
            response += f"Example: {json.dumps(tool['example'], indent=2)}\n"
        
        if tool['run_script']:
            response += f"Run Script: {tool['run_script']}\n"
        
        if tool['endpoint_url']:
            response += f"Endpoint URL: {tool['endpoint_url']}\n"
        
        if tool['http_method']:
            response += f"HTTP Method: {tool['http_method']}\n"
        
        # Always get service and its tags for SMS/email auto-append functionality
        service_result = supabase.from_('services').select('tag_1_id, tag_2_id, tag_3_id, tag_4_id, tag_5_id').eq('id', tool['service_id']).execute()
        
        if service_result.data:
            service = service_result.data[0]
            service_tag_names = get_tag_names_for_service(service, supabase)
            
            # Check if service has SMS/text message tags and auto-append phone number
            has_sms_tags = any(
                'sms' in tag_name.lower() or 'text message' in tag_name.lower() 
                for tag_name in service_tag_names
            )
            
            if has_sms_tags:
                try:
                    # Fetch user's integration for this service
                    integration_result = supabase.from_('integrations').select(
                        'configuration'
                    ).eq('user_id', user_id).eq('service_id', tool['service_id']).eq('is_active', True).execute()
                    
                    if integration_result.data and integration_result.data[0].get('configuration'):
                        config = integration_result.data[0]['configuration']
                        phone_number = config.get('phone_number')
                        
                        if phone_number:
                            response += f"\nUser's Phone Number in case none provided: {phone_number}\n"
                            
                            log_agent_event(
                                logger,
                                f"Auto-appended phone number for SMS service: {tool['service_id']}",
                                agent_name="Integrations Agent",
                                user_id=str(user_id),
                                action="phone_number_auto_appended",
                                tool_name=tool_name,
                                service_id=tool['service_id'],
                                phone_number_present=bool(phone_number)
                            )
                        else:
                            response += "\nNote: This service supports SMS/text messaging but no phone number is configured.\n"
                    else:
                        response += "\nNote: This service supports SMS/text messaging but no integration configuration found.\n"
                        
                except Exception as e:
                    logger.warning(f"Failed to fetch phone number for SMS service: {e}")
                    response += "\nNote: This service supports SMS/text messaging but phone number could not be retrieved.\n"
            
            # Check if service has email tags and auto-append user's email
            has_email_tags = any(
                'email' in tag_name.lower() 
                for tag_name in service_tag_names
            )
            
            if has_email_tags:
                try:
                    # Fetch user's email from auth.users table
                    auth_result = supabase.auth.admin.get_user_by_id(user_id)
                    
                    if auth_result.user and auth_result.user.email:
                        user_email = auth_result.user.email
                        response += f"\nUser's Email Address: {user_email}\n"
                        
                        log_agent_event(
                            logger,
                            f"Auto-appended email address for email service: {tool['service_id']}",
                            agent_name="Integrations Agent",
                            user_id=str(user_id),
                            action="email_auto_appended",
                            tool_name=tool_name,
                            service_id=tool['service_id'],
                            email_present=bool(user_email)
                        )
                    else:
                        response += "\nNote: This service supports email but no user email found in auth.\n"
                        
                except Exception as e:
                    logger.warning(f"Failed to fetch user email for email service: {e}")
                    response += "\nNote: This service supports email but user email could not be retrieved.\n"
        
        # Only fetch resources if resource_ids are explicitly provided (for full content)
        if resource_ids_filter:
            # Get associated resources using tool tag and service tags
            all_resources = []
            tag_ids_to_search = []
            
            # Add tool's tag if it exists
            if tool['tag_id']:
                tag_ids_to_search.append(tool['tag_id'])
            
            # Add service tags to search list (we already have service from above)
            if service_result.data:
                service = service_result.data[0]
                for i in range(1, 6):
                    tag_id = service.get(f'tag_{i}_id')
                    if tag_id and tag_id not in tag_ids_to_search:
                        tag_ids_to_search.append(tag_id)
            
            # Search for resources associated with any of these tags (for full content)
            if tag_ids_to_search:
                logger.info(f"Fetching full resources for {len(tag_ids_to_search)} tool and service tags")
                all_resources = []
                found_resource_ids = set()
                
                # Use simple individual queries to avoid timeouts
                for tag_id in tag_ids_to_search:
                    try:
                        # Query each tag column individually
                        for tag_column in ['tag_1_id', 'tag_2_id', 'tag_3_id', 'tag_4_id', 'tag_5_id']:
                            try:
                                tag_resources = supabase.from_('resources').select('*').eq('user_id', user_id).eq(tag_column, tag_id).execute()
                                
                                # Add unique resources
                                for resource in tag_resources.data:
                                    if resource['id'] not in found_resource_ids:
                                        all_resources.append(resource)
                                        found_resource_ids.add(resource['id'])
                                        
                            except Exception as col_error:
                                logger.debug(f"Column {tag_column} query failed for tag {tag_id}: {col_error}")
                                continue
                                
                    except Exception as tag_error:
                        logger.warning(f"Failed to fetch resources for tag {tag_id}: {tag_error}")
                        continue
                
                # Create result object
                class MockResult:
                    def __init__(self, data):
                        self.data = data
                
                resources_result = MockResult(all_resources)
                logger.info(f"Found {len(all_resources)} unique resources for tool and service tags")
                
                if resources_result.data:
                    all_resources = resources_result.data
                    
                    # Filter resources to only those specified in resource_ids_filter
                    original_count = len(all_resources)
                    all_resources = [r for r in all_resources if str(r['id']) in resource_ids_filter]
                    
                    # Log selective resource filtering
                    log_agent_event(
                        logger,
                        f"Filtered resources from {original_count} to {len(all_resources)} based on requested IDs",
                        agent_name="Integrations Agent",
                        user_id=str(user_id),
                        action="resources_filtered",
                        tool_name=tool_name,
                        original_count=original_count,
                        filtered_count=len(all_resources),
                        requested_ids=resource_ids_filter
                    )
                    
                    # Warn if no resources matched the filter
                    if not all_resources and original_count > 0:
                        log_agent_event(
                            logger,
                            f"No resources matched the requested IDs for tool {tool_name}",
                            level="WARNING",
                            agent_name="Integrations Agent",
                            user_id=str(user_id),
                            action="resources_filter_empty",
                            tool_name=tool_name,
                            requested_ids=resource_ids_filter
                        )
                
                    # Include full content in logs for fetched resources (since these are specifically requested)
                    resource_content_data = []
                    for resource in all_resources:
                        content = resource.get('content', '')
                        resource_content_data.append({
                            "resource_id": resource['id'],
                            "title": resource['title'],
                            "content_preview": content[:200] + '...' if len(content) > 200 else content,
                            "content_length": len(content),
                            "full_content_available": True
                        })
                    
                    # Log resource retrieval
                    log_agent_event(
                        logger,
                        f"Retrieved {len(all_resources)} full resources for tool {tool_name}",
                        agent_name="Integrations Agent",
                        user_id=str(user_id),
                        action="resources_retrieved_full",
                        tool_name=tool_name,
                        resource_count=len(all_resources),
                        resource_ids=[r['id'] for r in all_resources],
                        tag_ids=tag_ids_to_search,
                        filtered_by_ids=bool(resource_ids_filter),
                        resource_content_data=resource_content_data
                    )
                    
                    if all_resources:
                        response += "\nAssociated Resources (Full Content):\n"
                        
                        # Group resources by type for better organization
                        resources_by_type = {}
                        for resource in all_resources:
                            res_type = resource.get('type', 'general')
                            if res_type not in resources_by_type:
                                resources_by_type[res_type] = []
                            resources_by_type[res_type].append(resource)
                        
                        # Display resources grouped by type
                        for res_type, resources in resources_by_type.items():
                            response += f"\n[{res_type.title()} Resources]\n"
                            for resource in resources:
                                response += f"\nResource ID: {resource['id']}\n"
                                response += f"Title: {resource['title']}\n"
                                response += f"Type: {resource['type']}\n"
                                response += f"Relevance Score: {resource['relevance_score']}\n"
                                
                                # Show instructions field prominently if present
                                if resource.get('instructions'):
                                    response += f"Instructions: {resource['instructions']}\n"
                                
                                # Show FULL content - no truncation
                                response += f"Content:\n{resource['content']}\n"
                                
                                # Add metadata
                                if resource.get('last_accessed'):
                                    last_accessed = datetime.fromisoformat(
                                        resource['last_accessed'].replace('Z', '+00:00')
                                    ).strftime("%Y-%m-%d %H:%M")
                                    response += f"Last Accessed: {last_accessed}\n"
                                
                                response += "-" * 50 + "\n"
                    else:
                        response += "\nNo resources found for the specified IDs.\n"
        
        return response
        
    except Exception as e:
        logger.error(f"Error fetching tool data: {str(e)}")
        return f"Error fetching tool data: {str(e)}"



def get_tag_names_for_service(service_data: dict, supabase: SupabaseClient) -> List[str]:
    """
    Helper function to get tag names for a service based on its tag IDs.
    
    Args:
        service_data: Service data dict containing tag_1_id through tag_5_id
        supabase: Supabase client for database operations
        
    Returns:
        List of tag names
    """
    tag_names = []
    tag_ids = []
    
    # Collect all non-null tag IDs
    for i in range(1, 6):
        tag_id = service_data.get(f'tag_{i}_id')
        if tag_id:
            tag_ids.append(tag_id)
    
    if tag_ids:
        try:
            # Fetch tag names for all tag IDs
            tags_result = supabase.from_('tags').select('id, name').in_('id', tag_ids).execute()
            if tags_result.data:
                # Create a mapping of tag_id to name
                tag_map = {tag['id']: tag['name'] for tag in tags_result.data}
                # Preserve order by checking each tag_id in order
                for tag_id in tag_ids:
                    if tag_id in tag_map:
                        tag_names.append(tag_map[tag_id])
        except Exception as e:
            logger.warning(f"Error fetching tag names: {e}")
    
    return tag_names


def fetch_recently_used_integrations(input_str: str, user_id: str = None, supabase: SupabaseClient = None, request_id: str = None) -> str:
    """
    Fetch recently used integrations for the user.
    
    Args:
        input_str: Optional JSON with limit parameter
        user_id: UUID of the user
        supabase: Supabase client for database operations
        request_id: Request ID for cache operations
        
    Returns:
        List of recently used integrations
    """
    if not user_id:
        return "Error: User ID is required"
    
    if not supabase:
        return "Error: Database connection is required"
    
    try:
        # Parse optional limit
        limit = INTEGRATIONS_AGENT_RECENT_INTEGRATIONS_DEFAULT_LIMIT  # Default
        if input_str and input_str.strip():
            try:
                params = json.loads(input_str)
                limit = params.get('limit', INTEGRATIONS_AGENT_RECENT_INTEGRATIONS_DEFAULT_LIMIT)
            except json.JSONDecodeError:
                pass
        
        # Fetch user's integrations ordered by last_used
        result = supabase.from_('integrations').select(
            'id, service_id, is_active, status, last_used, created_at, notes'
        ).eq('user_id', user_id).order('last_used', desc=True).limit(limit).execute()
        
        if result.data:
            integrations_list = []
            
            for integration in result.data:
                # Get service details including tag fields
                service_result = supabase.from_('services').select(
                    'service_name, description, tag_1_id, tag_2_id, tag_3_id, tag_4_id, tag_5_id'
                ).eq('id', integration['service_id']).execute()
                
                if service_result.data:
                    service = service_result.data[0]
                    status = integration['status'] or 'active'
                    last_used = integration['last_used'] or integration['created_at']
                    
                    # Format last used time
                    if last_used:
                        last_used_dt = datetime.fromisoformat(last_used.replace('Z', '+00:00'))
                        last_used_str = last_used_dt.strftime("%Y-%m-%d %H:%M")
                    else:
                        last_used_str = "Never"
                    
                    # Get tag names for this service
                    tag_names = get_tag_names_for_service(service, supabase)
                    
                    integration_str = f"- {service['service_name']} (Status: {status}, Active: {integration['is_active']})"
                    integration_str += f"\n  Last used: {last_used_str}"
                    if tag_names:
                        integration_str += f"\n  Tags: {', '.join(tag_names)}"
                    if integration['notes']:
                        integration_str += f"\n  Notes: {integration['notes']}"
                    
                    integrations_list.append(integration_str)
            
            if integrations_list:
                return "Recently used integrations:\n" + "\n\n".join(integrations_list)
            else:
                return "No integrations found"
        else:
            return "No integrations set up yet"
            
    except Exception as e:
        logger.error(f"Error fetching recent integrations: {str(e)}")
        return f"Error fetching recent integrations: {str(e)}"


async def use_service_tool_async(input_str: str, user_id: str = None, supabase: SupabaseClient = None, request_id: str = None) -> str:
    """
    Async version of use_service_tool for use in async contexts.
    
    Args:
        input_str: JSON with tool_name and tool_parameters
        user_id: UUID of the user
        supabase: Supabase client for database operations
        request_id: Request ID for cache operations
        
    Returns:
        Result of the tool execution
    """
    if not user_id:
        return "Error: User ID is required to use service tools"
    
    try:
        # Parse input
        params = json.loads(input_str)
        
        # Validate required fields
        if "tool_name" not in params:
            return "Error: 'tool_name' field is required"
        
        tool_name = params["tool_name"]
        tool_parameters = params.get("tool_parameters", {})
        
        # Check if tool name contains "send" and validate against original user message
        if "send" in tool_name.lower():
            try:
                # Fetch original user message from cache
                original_message_key = f"original_user_message_{request_id}"
                original_message = RequestCacheService.retrieve(request_id, original_message_key)
                
                if original_message is None:
                    return "Error: Could not retrieve original user message for send validation"
                
                # Check if "send" is in the original user message
                if "send" not in original_message.lower():
                    return f"The human user's most recent message: \"{original_message}\" did not include the word \"send\".  It must include the word \"send\" for a message to be sent.  Please notify the user."
                    
                # Log the successful validation
                log_agent_event(
                    logger,
                    f"Send validation passed for tool: {tool_name}",
                    agent_name="Integrations Agent",
                    user_id=str(user_id),
                    request_id=request_id,
                    action="send_validation_success",
                    tool_name=tool_name
                )
                
            except Exception as e:
                logger.error(f"Error during send validation: {str(e)}")
                return "Error: Failed to validate send permission"
        
        # Import the service registry
        from app.agents.integrations.service_tools.registry import get_user_registry
        
        # Get the user-specific registry and tool
        user_registry = get_user_registry(user_id, supabase)
        tool = user_registry.get_tool_by_name(tool_name)
        
        if not tool:
            return f"Error: Tool '{tool_name}' not found in service registry"
        
        # Add user_id and request_id to tool parameters since all service tools require them
        if isinstance(tool_parameters, dict):
            tool_parameters["user_id"] = user_id
            tool_parameters["request_id"] = request_id
            tool_input = json.dumps(tool_parameters)
        else:
            # If tool_parameters is a string, try to parse and add user_id and request_id
            try:
                params_dict = json.loads(str(tool_parameters))
                params_dict["user_id"] = user_id
                params_dict["request_id"] = request_id
                tool_input = json.dumps(params_dict)
            except:
                # Fallback: create a new dict with user_id and request_id
                tool_input = json.dumps({"user_id": user_id, "request_id": request_id, "query": str(tool_parameters)})
        
        # Log the service tool call before execution
        log_agent_event(
            logger,
            f"Executing service tool async: {tool_name}",
            agent_name="Integrations Agent",
            user_id=str(user_id),
            request_id=request_id,
            action="service_tool_call_start_async",
            tool_name=tool_name,
            tool_parameters=tool_parameters,
            service_name=getattr(tool, 'service_name', None)
        )
        
        start_time = time.time()
        
        # Determine timeout for this tool execution
        tool_timeout = getattr(tool, 'execution_timeout', None)
        if tool_timeout is None:
            timeout = SERVICE_TOOL_DEFAULT_TIMEOUT
        else:
            # Ensure timeout doesn't exceed maximum allowed
            timeout = min(tool_timeout, SERVICE_TOOL_MAX_TIMEOUT)
        
        # Execute the tool's handler (which is async) with timeout enforcement
        try:
            result = await asyncio.wait_for(tool.handler(tool_input), timeout=timeout)
            
            # Check if result content should be cached (if length exceeds threshold)
            if result and request_id and len(str(result)) > INTEGRATIONS_AGENT_SERVICE_TOOL_TRUNC_THRESHOLD:
                # Store original length before modifying result
                original_length = len(str(result))
                
                # Generate unique cache key for this content
                cache_key = f"service_tool_{tool_name}_{uuid.uuid4().hex[:8]}"
                
                # Store full content in cache
                RequestCacheService.store(request_id, cache_key, result)
                
                # Create truncated content with cache key reference
                truncated_content = str(result)[:INTEGRATIONS_AGENT_SERVICE_TOOL_TRUNC_THRESHOLD]
                cache_message = f"\n\n[CACHED CONTENT - Full content stored in cache with key: {cache_key}. Use fetch_from_cache tool to retrieve full content if needed.]"
                result = truncated_content + cache_message
                
                # Log cache operation
                log_agent_event(
                    logger,
                    f"Large content from {tool_name} auto-cached",
                    agent_name="Integrations Agent",
                    user_id=str(user_id),
                    request_id=request_id,
                    action="service_tool_content_cached",
                    tool_name=tool_name,
                    cache_key=cache_key,
                    original_length=original_length,
                    truncated_length=len(truncated_content)
                )
            
            # Log successful execution
            log_agent_event(
                logger,
                f"Successfully executed service tool async: {tool_name}",
                agent_name="Integrations Agent",
                user_id=str(user_id),
                request_id=request_id,
                action="service_tool_call_success_async",
                tool_name=tool_name,
                service_name=getattr(tool, 'service_name', None),
                result_preview=str(result)[:200] if result else None,
                duration_ms=round((time.time() - start_time) * 1000, 2)
            )
            return result
            
        except asyncio.TimeoutError:
            # Handle timeout specifically
            duration = time.time() - start_time
            log_agent_event(
                logger,
                f"Service tool execution timed out: {tool_name} after {timeout}s",
                level="ERROR",
                agent_name="Integrations Agent",
                user_id=str(user_id),
                request_id=request_id,
                action="service_tool_timeout_async",
                tool_name=tool_name,
                timeout_seconds=timeout,
                duration_seconds=round(duration, 2)
            )
            return f"Error: Service tool '{tool_name}' timed out after {timeout} seconds. The operation may be too complex or the service may be experiencing issues."
            
        except Exception as e:
            # Log execution error
            log_agent_event(
                logger,
                f"Service tool execution failed: {tool_name}",
                level="ERROR",
                agent_name="Integrations Agent",
                user_id=str(user_id),
                request_id=request_id,
                action="service_tool_call_error_async",
                tool_name=tool_name,
                error=str(e),
                duration_ms=round((time.time() - start_time) * 1000, 2)
            )
            return f"Error executing service tool: {str(e)}"
        
    except json.JSONDecodeError:
        return "Error: Invalid JSON input"
    except Exception as e:
        logger.error(f"Error executing service tool async: {str(e)}")
        return f"Error executing service tool: {str(e)}"


def fetch_service_integration_scripts(input_str: str, user_id: str = None, supabase: SupabaseClient = None, request_id: str = None) -> str:
    """
    Fetch authentication scripts for a service from associated tags.
    
    Args:
        input_str: JSON with service_name or service name string
        user_id: UUID of the user
        supabase: Supabase client for database operations
        request_id: Request ID for cache operations
        
    Returns:
        Authentication scripts from associated tags of type service and service_type
    """
    if not supabase:
        return "Error: Database connection is required"
    
    #NOTE: there is a logic error below and only the integration scripts in the 
    # integration_script_id column of the tag record of type service is propagating - 
    # not all integration scripts associated with the tags in the service record 
    # (basically each service must be assigned a script via the tag record that shares their name)
    try:
        # Parse input
        if input_str.startswith('{'):
            params = json.loads(input_str)
            service_name = params.get('service_name')
        else:
            service_name = input_str.strip()
        
        if not service_name:
            return "Error: service_name is required"
        
        # Get service with tag IDs
        result = supabase.from_('services').select(
            'service_name, integration_method, tag_1_id, tag_2_id, tag_3_id, tag_4_id, tag_5_id'
        ).eq('service_name', service_name).execute()
        
        if not result.data:
            return f"Error: Service '{service_name}' not found"
        
        service = result.data[0]
        
        response = f"Integration scripts for {service['service_name']}:\n"
        
      
        # First, get the main service tag's integration_script
        service_tag_result = supabase.from_('tags').select(
            'id, name, type, integration_script_id, integration_scripts!integration_script_id(script)'
        ).eq('name', service_name).eq('type', 'service').execute()
        
        script_content = None
        if service_tag_result.data and service_tag_result.data[0].get('integration_scripts'):
            script_content = service_tag_result.data[0]['integration_scripts']['script']
            
            
            response += "Service Integration Script:\n"
            response += "```python\n"
            response += script_content
            response += "\n```\n\n"
        
        # Fetch content from all associated tags (service and service_type)
        tag_ids = []
        for i in range(1, 6):
            tag_id = service.get(f'tag_{i}_id')
            if tag_id:
                tag_ids.append(tag_id)
        
        if tag_ids:
            # Get all tags and their integration_scripts
            tags_result = supabase.from_('tags').select(
                'id, name, type, integration_script_id, integration_scripts!integration_script_id(script)'
            ).in_('id', tag_ids).execute()
            
            if tags_result.data:
                # Separate tags by type for better organization
                service_tags = [tag for tag in tags_result.data if tag['type'] == 'service' and tag.get('integration_scripts')]
                service_type_tags = [tag for tag in tags_result.data if tag['type'] == 'service_type' and tag.get('integration_scripts')]
                
                if service_tags or service_type_tags:
                    response += "Associated Tag Scripts:\n"
                
                # Show service tags first
                for tag in service_tags:
                    script_content = tag['integration_scripts']['script']
                    response += f"\n{tag['name']} (service) Script:\n"
                    response += "```python\n"
                    response += script_content
                    response += "\n```\n"
                
                # Then show service_type tags
                for tag in service_type_tags:
                    script_content = tag['integration_scripts']['script']
                    response += f"\n{tag['name']} (service_type) Script:\n"
                    response += "```python\n"
                    response += script_content
                    response += "\n```\n"
        
          # Check if this is a Health and Wellness service and inject recent health metrics
        # if user_id:
        #     try:
        #         # Get the "Health and Wellness" tag ID
        #         health_wellness_tag_result = supabase.from_('tags').select('id').eq('name', 'Health and Wellness').execute()
                
        #         if health_wellness_tag_result.data:
        #             health_wellness_tag_id = health_wellness_tag_result.data[0]['id']
                    
        #             # Check if any of the service's tag IDs match the Health and Wellness tag
        #             service_tag_ids = [
        #                 service.get('tag_1_id'),
        #                 service.get('tag_2_id'), 
        #                 service.get('tag_3_id'),
        #                 service.get('tag_4_id'),
        #                 service.get('tag_5_id')
        #             ]
                    
        #             if health_wellness_tag_id in service_tag_ids:
        #                 # This is a Health and Wellness service, fetch recent health metrics
        #                 health_metrics_result = supabase.from_('health_metrics_daily').select(
        #                     'date, sleep_score, activity_score, readiness_score, stress_level, recovery_score, resilience_score, total_steps, calories_burned, resting_hr, hrv_avg'
        #                 ).eq('user_id', user_id).order('date', desc=True).limit(7).execute()
                        
        #                 if health_metrics_result.data:
        #                     response += "\nRecent Health Metrics (Last 7 Days):\n"
        #                     response += "=" * 50 + "\n"
                            
        #                     for i, metrics in enumerate(health_metrics_result.data, 1):
        #                         response += f"\nDay {i} ({metrics['date']}):\n"
                                
        #                         # Return raw metric names and values without mapping to display names
        #                         # This makes the system more extensible as new metrics are added
        #                         for key, value in metrics.items():
        #                             # Skip date field as it's already shown
        #                             if key != 'date' and value is not None:
        #                                 # Use the raw column name from the database
        #                                 response += f"  {key}: {value}\n"
                            
        #                     response += "\n" + "=" * 50 + "\n"
                            
        #                     # Log the health metrics injection
        #                     log_agent_event(
        #                         logger,
        #                         f"Injected {len(health_metrics_result.data)} health metrics records for Health and Wellness service",
        #                         agent_name="Integrations Agent",
        #                         user_id=str(user_id),
        #                         action="health_metrics_injected",
        #                         service_name=service['service_name'],
        #                         metrics_count=len(health_metrics_result.data)
        #                     )
        #                 else:
        #                     response += "\nNo recent health metrics data available.\n"
        #     except Exception as e:
        #         logger.warning(f"Failed to fetch health metrics for Health and Wellness service: {e}")
        #         # Don't fail the whole request if health metrics fail
        
        
        
        message = "use information about the available service tools to provide a concise description of the key actions we can take on the user's behalf."
        default_content = f"\nLastly, {message}"
        if script_content == None or script_content == "" or script_content == "None":
            default_content = f"Integration is simple for this service and can be completed with the following step: {message}.  Creds have already been obtained and integration will be completed when you notify the calling agent with information about actions we can take on the user's behalf."
    
        response += default_content
        
        return response
        
    except Exception as e:
        logger.error(f"Error fetching integration scripts: {str(e)}")
        return f"Error fetching integration scripts: {str(e)}"


def create_integrations_tools(user_id: str = None, supabase: SupabaseClient = None, request_id: str = None, agent_instance=None) -> List[Action]:
    """Create all integration management tools with dependencies injected."""
    
    tools = []
    
    # 0. Fetch from Cache Tool
    from app.services.cache_tools import create_fetch_from_cache_action
    tools.append(create_fetch_from_cache_action(request_id))
    
    # 1. Fetch Tool Names and Descriptions
    tools.append(Action(
        name="initial_md_fetch",
        description="Fetch service tool names and descriptions as well as associated resources with truncated content.",
        parameters={
            "service_name": {"type": "string", "description": "Name of the service to fetch data for"},
        },
        returns="List of service tool names with descriptions, and associated resources with truncated content",
        example='Action: initial_md_fetch: {"service_name": "Gmail", "search": "draft"}',
        handler=lambda input_str: initial_md_fetch(input_str, user_id, supabase, request_id)
    ))
    
    # 2. Fetch Tool Data
    tools.append(Action(
        name="fetch_tool_data",
        description="Fetch complete tool definition and execution parameters of specific tool. Only fetches resources if resource_ids are provided.",
        parameters={
            "tool_name": {"type": "string", "description": "Name of the tool to get data for"},
            "resource_ids": {"type": "array", "description": "List of resource IDs to fetch in full. If not provided, no resources are fetched."}
        },
        returns="Detailed tool definition, and resources with full content only if resource_ids are provided",
        example='Action: fetch_tool_data: {"tool_name": "draft_email", "resource_ids": ["123e4567-e89b-12d3-a456-426614174000"]}',
        handler=lambda input_str: fetch_tool_data(input_str, user_id, supabase, request_id, agent_instance)
    ))
    
    # 3. Fetch Recently Used Integrations
    tools.append(Action(
        name="fetch_recently_used_integrations",
        description="Fetch the user's recently used integrations ordered by last use.  Use this if it is unclear what service to fetch tools for.  If it is still unclear after using this tool, inform the chat agent.",
        parameters={
            "limit": {"type": "integer", "description": "Maximum number of integrations to return"}
        },
        returns="List of recently used integrations with status and usage info",
        example='Action: fetch_recently_used_integrations: {"limit": 15}',
        handler=lambda input_str: fetch_recently_used_integrations(input_str, user_id, supabase, request_id)
    ))
    
    # 4. Use Service Tool
    async def use_service_tool_handler(input_str):
        return await use_service_tool_async(input_str, user_id, supabase, request_id)
    tools.append(Action(
        name="use_service_tool",
        description="Execute any service tool by name. Use this after discovering tools, resources, and execution parameters with initial_md_fetch and fetch_tool_data.",
        parameters={
            "tool_name": {"type": "string", "description": "Name of the service tool to execute"},
            "tool_parameters": {"type": "object", "description": "Parameters to pass to the tool as defined in its schema"}
        },
        returns="Result of the service tool execution",
        example='Action: use_service_tool: {"tool_name": "draft_email", "tool_parameters": {"to": "user@example.com", "subject": "Test", "body": "Hello"}}',
        handler=use_service_tool_handler
    ))
    
    # 5. Fetch Service Integration Scripts
    def fetch_scripts_handler(input_str: str) -> str:
        return fetch_service_integration_scripts(input_str, user_id, supabase, request_id)
    
    tools.append(Action(
        name="fetch_service_integration_scripts",
        description="Fetch integration completion scripts for a service integration. Use when we are completing an integration with an external service for a user.",
        parameters={
            "service_name": {"type": "string", "description": "Name of the service"}
        },
        returns="Guidance and context for the integration process",
        example='Action: fetch_service_integration_scripts: {"service_name": "Slack"}',
        handler=fetch_scripts_handler
    ))
    
    # 6. Call Retrieval Agent
    tools.append(create_retrieval_agent_action(user_id, supabase, R_A_LLM_MODEL, request_id, calling_agent="Integrations Agent"))
    
    return tools


class IntegrationsAgent(BaseAgent):
    """
    Specialized agent for integration discovery and management.
    """
    
    def __init__(
        self,
        user_id: str,
        supabase: SupabaseClient,
        recent_integration_services: List[str] = None,
        model: str = INTEGRATIONS_AGENT_LLM_MODEL,
        temperature: float = 0.3,
        request_id: str = None,
        calling_agent: str = None,
        integration_in_progress: bool = False
    ):
        """
        Initialize the integrations manager agent with user-specific context.
        
        Args:
            user_id: The ID of the user
            supabase: Supabase client for database operations
            recent_integration_services: List of service names from user's recent integrations
            model: The model to use for the agent
            temperature: Lower temperature for more consistent operations
        """
        # Create integration tools with injected dependencies
        tools = create_integrations_tools(user_id=user_id, supabase=supabase, request_id=request_id, agent_instance=self)
        
        # Format recent integrations for context
        if recent_integration_services:
            integrations_list = ", ".join(recent_integration_services)
            integration_status = f"{integrations_list}"
        else:
            integration_status = "User has no recent integrations"
        
        # Format system integrations for context
        system_integrations = supabase.from_('services').select('service_name').eq('type', 'system').execute()
        system_integrations = [service['service_name'] for service in system_integrations.data]
        # Filter out "XAI Live Search" from available system integrations
        system_integrations = [service for service in system_integrations if service != "XAI Live Search"]
        system_integrations = ", ".join(system_integrations)
        
        
        # Define the agent's specialized context
        integration_context = f"""You are the integrations manager in a multi-agent system called Juniper.
You report directly to the chat agent who has direct contact with the human user.
Your primary role is to interact with users' connected third party services and available system services using the flow outlined below."""        
        # Define general instructions for integration handling
        integration_instructions = f"""To execute a third party service tool:
0. (Optional, Rarely needed) Use fetch_recently_used_integrations to fetch recently used services if it is unclear what service to use
1. Use initial_md_fetch to fetch tool names, descriptions, and associated resources for the service(s).  
2. Use fetch_tool_data to get full tool details and additional resource content if necessary
3. Use use_service_tool to execute the tool

Current date/time: {datetime.now().isoformat()}
User's integrations with relevant services (suspected type): {integration_status}
Available System Services: {system_integrations}

"""

        # Initialize the base agent with integration-specific setup
        super().__init__(
            actions=tools,  
            additional_context=integration_context,
            general_instructions=integration_instructions,
            model=model,
            temperature=temperature,
            max_turns=INTEGRATIONS_AGENT_MAX_TURNS,  # More turns for complex integration workflows
            agent_name="Integrations Agent",
            calling_agent=calling_agent,
            enable_caching=True,  # Enable caching for Integrations Agent
            cache_static_content=True  # Cache static content (tools, instructions)
        )
        
        self.user_id = user_id
        self.supabase = supabase
        self.recent_integration_services = recent_integration_services


def create_integrations_agent_action(user_id: str = None, supabase: SupabaseClient = None, model: str = INTEGRATIONS_AGENT_LLM_MODEL, request_id: str = None, calling_agent: str = None, integration_in_progress: bool = False) -> Action:
    """Create an action that calls the integrations manager agent."""
    
    async def handle_integrations_request(request: str) -> str:
        """Handle integration management requests through the integrations agent."""
        try:
            # Update request status to "pinging"
            if request_id and supabase:
                try:
                    supabase.from_('requests').update({
                        'status': 'pinging',
                        'updated_at': datetime.now().isoformat()
                    }).eq('request_id', request_id).execute()
                    logger.info(f"Updated request status to 'pinging' for request_id: {request_id}")
                except Exception as db_error:
                    logger.error(f"Failed to update request status to pinging: {str(db_error)}")
            
            # First, use LLM classifier to predict relevant service types
            predicted_service_types = []
            try:
                predicted_service_types = await classify_service_types(
                    command=request,
                    user_id=user_id,
                    supabase=supabase
                )
                
                if predicted_service_types:
                    log_agent_event(
                        logger,
                        f"LLM Classifier predicted {len(predicted_service_types)} service types",
                        agent_name="Integrations Agent",
                        user_id=str(user_id) if user_id else "unknown",
                        action="classifier_prediction",
                        predicted_types=predicted_service_types,
                        request_preview=request[:500] if request else None
                    )
            except Exception as e:
                logger.warning(f"LLM Classifier failed, will use all recent integrations: {e}")
            
            # Fetch user's recent integrations
            recent_integration_services = []
            total_integration_count = 0
            filtered_integration_count = 0
            
            try:
                # Get recent integrations
                integrations_result = supabase.from_('integrations').select(
                    'service_id, last_used'
                ).eq('user_id', user_id).eq('is_active', True).order(
                    'last_used', desc=True
                ).limit(NUM_RECENT_INTEGRATIONS_INJECTED).execute()
                
                if integrations_result.data:
                    total_integration_count = len(integrations_result.data)
                    
                    # Get service names and tags for these integrations
                    service_ids = [i['service_id'] for i in integrations_result.data]
                    services_result = supabase.from_('services').select(
                        'id, service_name, tag_1_id, tag_2_id, tag_3_id, tag_4_id, tag_5_id'
                    ).in_('id', service_ids).execute()
                    
                    if services_result.data:
                        # If we have predicted service types, we need to resolve tag IDs to filter
                        tag_name_to_id_map = {}
                        if predicted_service_types:
                            # Fetch tag IDs for predicted service types
                            tags_result = supabase.from_('tags').select('id, name').in_('name', predicted_service_types).eq('type', 'service_type').execute()
                            if tags_result.data:
                                tag_name_to_id_map = {tag['name']: tag['id'] for tag in tags_result.data}
                        
                        # Create a mapping of service_id to service info with tags
                        service_map = {}
                        filtered_service_map = {}
                        
                        for service in services_result.data:
                            tag_names = get_tag_names_for_service(service, supabase)
                            service_name = service['service_name']
                            display_name = service_name
                            if tag_names:
                                display_name += f" ({', '.join(tag_names)})"
                            
                            # Always add to the full map
                            service_map[service['id']] = display_name
                            
                            # Check if this service should be included based on predicted types
                            if predicted_service_types and tag_name_to_id_map:
                                # Check if any of the service's tags match predicted types
                                service_tag_ids = [
                                    service.get(f'tag_{i}_id') 
                                    for i in range(1, 6) 
                                    if service.get(f'tag_{i}_id')
                                ]
                                
                                predicted_tag_ids = list(tag_name_to_id_map.values())
                                if any(tag_id in predicted_tag_ids for tag_id in service_tag_ids):
                                    filtered_service_map[service['id']] = display_name
                            else:
                                # No predictions, include all
                                filtered_service_map[service['id']] = display_name
                        
                        # Use filtered map if we have predictions AND it's not empty
                        # Otherwise fallback to most recent NUM_RECENT_INTEGRATIONS_INJECTED integrations
                        if predicted_service_types and filtered_service_map:
                            final_service_map = filtered_service_map
                            logger.info(f"Using {len(filtered_service_map)} filtered integrations based on predicted service types")
                        else:
                            if predicted_service_types and not filtered_service_map:
                                logger.warning(f"Filtering by predicted types {predicted_service_types} resulted in 0 integrations, falling back to {NUM_RECENT_INTEGRATIONS_INJECTED} most recent integrations")
                            final_service_map = service_map
                        
                        # Preserve the order from integrations query and limit to NUM_RECENT_INTEGRATIONS_INJECTED
                        recent_integration_services = []
                        for i in integrations_result.data:
                            if i['service_id'] in final_service_map:
                                recent_integration_services.append(final_service_map[i['service_id']])
                                if len(recent_integration_services) >= NUM_RECENT_INTEGRATIONS_INJECTED:
                                    break
                        
                        filtered_integration_count = len(recent_integration_services)
                        
                        # Log filtering results
                        if predicted_service_types:
                            if filtered_service_map:
                                log_agent_event(
                                    logger,
                                    f"Filtered integrations from {total_integration_count} to {filtered_integration_count} based on predicted service types",
                                    agent_name="Integrations Agent",
                                    user_id=str(user_id) if user_id else "unknown",
                                    action="integrations_filtered",
                                    total_count=total_integration_count,
                                    filtered_count=filtered_integration_count,
                                    predicted_types=predicted_service_types,
                                    filtered_services=recent_integration_services[:5]  # Log first 5 for brevity
                                )
                            else:
                                log_agent_event(
                                    logger,
                                    f"No integrations matched predicted types, using {filtered_integration_count} most recent integrations (fallback)",
                                    agent_name="Integrations Agent",
                                    user_id=str(user_id) if user_id else "unknown",
                                    action="integrations_fallback",
                                    total_count=total_integration_count,
                                    fallback_count=filtered_integration_count,
                                    predicted_types=predicted_service_types,
                                    fallback_services=recent_integration_services[:5]  # Log first 5 for brevity
                                )
            except Exception as e:
                logger.warning(f"Failed to fetch recent integrations: {e}")
            
            # Create integrations manager agent instance
            # Use integration completion LLM when integration is in progress
            if integration_in_progress:
                agent_model = INTEGRATION_COMPLETION_LLM
                logger.info(f"Integration in progress. Now using model: {agent_model} for integrations agent")
            else:
                agent_model = model
                logger.info(f"Using model: {agent_model} for integrations agent")

            integrations_agent = IntegrationsAgent(
                user_id=user_id,
                supabase=supabase,
                recent_integration_services=recent_integration_services,
                model=agent_model,
                request_id=request_id,
                calling_agent=calling_agent,
                integration_in_progress=integration_in_progress
            )
            
            # Create a message for the request
            message = Message(
                role="user",
                content=request,
                type="text",
                timestamp=int(time.time())
            )
            
            # Process the request through the agent
            response = await integrations_agent.query([message], UUID(user_id), request_id, supabase)
            
            # Check if integration building is in progress
            if "building" in response.lower() or "setting up" in response.lower():
                response += " [INTEGRATION_IN_PROGRESS]"
            
            logger.info(f"Integrations agent processed request: {request[:50]}...")
            return response
            
        except Exception as e:
            logger.error(f"Error in integrations agent handler: {str(e)}")
            return f"Error processing integrations request: {str(e)}"
    
    return Action(
        name="call_integrations_agent",
        description="This agent (1) fetches and uses third party service tools to read from and write to productivity services like Gmail and Notion, wearbales services like Oura and Fitbit, and research tools like Perplexity. (2) Handles integration setup workflows (3) Has its own call_retrieval_agent tool for interacting with the retrieval agent.",
        parameters={
            "request": {
                "type": "string",
                "description": "Natural language request containing necessary conversation context, information provided by the user, and/or the user's requested action that the agent should attempt to execute"
            }
        },
        returns="Response about actions taken or actions needed",
        example='Action: call_integrations_agent: {"request": "Please draft an email to John Doe with the subject \\"Hello\\" and the body \\"How are you?\\""}',
        handler=handle_integrations_request
    )