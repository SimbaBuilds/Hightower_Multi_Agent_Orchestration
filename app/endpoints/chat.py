import json
import os
import time
from fastapi import Request, Form, Depends
from fastapi import HTTPException, APIRouter
from uuid import uuid4
from app.auth import get_current_user, get_supabase_client, check_user_limits
from supabase import Client as SupabaseClient
from app.agents.models import Message
from datetime import datetime
from typing import List, Optional, Dict, Any, Annotated
from app.agents.chat_agent.chat_agent import get_chat_response
from pydantic import BaseModel
import dotenv
from app.services.request_cache import RequestCacheService
from app.services.vision_processor import process_image_with_vision
from app.services.stripe_service import stripe_service

dotenv.load_dotenv(override=True)

# Use structured logging instead of basicConfig
from app.utils.logging.component_loggers import get_api_logger
logger = get_api_logger(__name__)

router = APIRouter()

class ChatRequest(BaseModel):
    message: str
    timestamp: int
    history: List[Message]
    preferences: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None  # Optional request ID from frontend
    integration_in_progress: bool = False
    image_url: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    timestamp: int
    settings_updated: bool = False
    integration_in_progress: bool = False


@router.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: Request,
    user_id: Annotated[str, Depends(get_current_user)],
    supabase: Annotated[SupabaseClient, Depends(get_supabase_client)],
    json_data: str = Form(...),
) -> ChatResponse:
    try:
        request_data = ChatRequest(**json.loads(json_data))
        image_url = None  # Initialize image_url variable for scope access throughout function
        
        # Prioritize frontend request_id for pooling, fallback to middleware-generated ID
        request_id = request_data.request_id
        if not request_id:
            # Use middleware-generated ID as fallback
            request_id = getattr(request.state, 'request_id', None)
            if not request_id:
                # Generate new ID if neither frontend nor middleware provided one
                request_id = str(uuid4())
        
        # Always update request.state to ensure consistency across the request lifecycle
        request.state.request_id = request_id
        
        # Auto-cache the original user message
        cache_key = f"original_user_message_{request_id}"
        RequestCacheService.store(request_id, cache_key, request_data.message)
            
        logger.info(f"Received chat request: message='{request_data.message}', history_count={len(request_data.history)}, preferences={request_data.preferences}, integration_in_progress={request_data.integration_in_progress}, request_id={request_id}")
        
        # Initialize overage tracking variables
        will_incur_overage = False
        overage_cost_cents = 0
        limit_exceeded = False
        
        # Check user limits before processing (skip for integration completion requests)
        if not request_data.integration_in_progress:
            try:
                # Get user profile for limit checking
                profile_result = supabase.from_('user_profiles').select('*').eq('id', user_id).execute()
                if not profile_result.data:
                    raise HTTPException(status_code=401, detail="User profile not found")
                
                user_profile = profile_result.data[0]
                limit_check = check_user_limits(user_profile)
                
                if not limit_check['can_proceed']:
                    from app.config import WEB_APP_URL
                    logger.warning(f"User {user_id} exceeded {limit_check['error_type']} limit")
                    limit_exceeded = True
                    
                    # Create appropriate limit message based on error type
                    if limit_check['error_type'] == 'monthly_requests':
                        limit_message = f"The user has reached the limit for monthly requests. Please notify them and let them know that they can manage their account in the web app at {WEB_APP_URL}."
                    elif limit_check['error_type'] == 'request_overage_ubp_limit':
                        limit_message = f"The user has reached the limit for overage requests. Please notify them and let them know that they can manage their account in the web app at {WEB_APP_URL}."
                    else:
                        limit_message = f"The user has reached the limit for {limit_check['error_type']}. Please notify them and let them know that they can manage their account in the web app at {WEB_APP_URL}."
                    
                    error_detail = {
                        "error": "limit_exceeded",
                        "error_type": limit_check['error_type'],
                        "current_tier": limit_check['current_tier'],
                        "current_usage": limit_check['current_usage'],
                        "limit": limit_check['limit'],
                        "requests_remaining_month": limit_check.get('requests_remaining_month', 0),
                        "message": limit_message
                    }
                    
                    # Add overage-specific information for Pro users
                    if limit_check['error_type'] == 'request_overage_ubp_limit':
                        error_detail.update({
                            "overage_cost_cents": limit_check['overage_cost_cents'],
                            "ubp_current_cents": limit_check['ubp_current_cents'],
                            "ubp_limit_cents": limit_check['ubp_limit_cents']
                        })
                    
                    # Pass the error detail to the chat agent instead of raising HTTPException
                    error_message = json.dumps(error_detail)
                    request_data.message = error_message
                    
                    # Also update the last message in history so the chat agent receives the error details
                    if request_data.history and len(request_data.history) > 0:
                        # Find the last user message and replace it
                        for i in range(len(request_data.history) - 1, -1, -1):
                            msg = request_data.history[i]
                            if (isinstance(msg, dict) and msg.get('role') == 'user') or (hasattr(msg, 'role') and msg.role == 'user'):
                                if isinstance(msg, dict):
                                    msg['content'] = error_message
                                else:
                                    msg.content = error_message
                                break
                
                # Update overage tracking variables
                will_incur_overage = limit_check.get('will_incur_overage', False)
                overage_cost_cents = limit_check.get('overage_cost_cents', 0)
            except Exception as limit_error:
                logger.error(f"Error checking limits for user {user_id}: {str(limit_error)}")
                # Continue processing if limit check fails (fail-open for now)

        # Ensure request record exists - create if not exists, otherwise update
        conversation_id = None
        try:
            # First try to update existing record
            update_result = supabase.from_('requests').update({
                'status': 'processing',
                'user_message': request_data.message,
                'total_turns': 0,
                'metadata': {
                    'message_preview': request_data.message[:100],
                    'history_count': len(request_data.history),
                    'preferences': request_data.preferences
                },
                'updated_at': datetime.now().isoformat()
            }).eq('request_id', request_id).execute()

            # Check if update affected any rows
            if not update_result.data:
                # Record doesn't exist - handle based on environment

                # Production: Retry until record is found
                max_retries = 16 if os.getenv("ENV") == "PROD" else 8  # (0.5s intervals)
                retry_count = 0

                while retry_count < max_retries:
                    time.sleep(0.5)  # Wait 500ms between retries
                    retry_count += 1

                    # Try to update the record again
                    retry_result = supabase.from_('requests').update({
                        'status': 'processing',
                        'user_message': request_data.message,
                        'total_turns': 0,
                        'metadata': {
                            'message_preview': request_data.message[:100],
                            'history_count': len(request_data.history),
                            'preferences': request_data.preferences
                        },
                        'updated_at': datetime.now().isoformat()
                    }).eq('request_id', request_id).execute()

                    if retry_result.data:
                        logger.info(f"Found request record on retry {retry_count} for request_id: {request_id}")
                        conversation_id = retry_result.data[0].get('conversation_id')
                        break

                    if retry_count >= max_retries:
                        logger.error(f"Request record still not found after {max_retries} retries for request_id: {request_id}")
                        # Continue processing even though record wasn't found
                        break
            else:
                logger.info(f"Updated existing request record for request_id: {request_id}")
                conversation_id = update_result.data[0].get('conversation_id')

        except Exception as db_error:
            logger.error(f"Failed to create/update request record: {str(db_error)}")
            # Continue processing even if request record create/update fails

        # Ensure conversation exists and get conversation_id
        try:
            if not conversation_id:
                # Create new conversation
                conversation_result = supabase.from_('conversations').insert({
                    'user_id': user_id,
                    'title': f"Chat - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    'conversation_type': 'general',
                    'status': 'active',
                    'metadata': {
                        'request_id': request_id,
                        'created_from_chat': True
                    },
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }).execute()

                if conversation_result.data:
                    conversation_id = conversation_result.data[0]['id']
                    logger.info(f"Created new conversation: {conversation_id}")

                    # Update request record with conversation_id
                    supabase.from_('requests').update({
                        'conversation_id': conversation_id,
                        'updated_at': datetime.now().isoformat()
                    }).eq('request_id', request_id).execute()
                else:
                    logger.error("Failed to create conversation record")
            else:
                logger.info(f"Using existing conversation: {conversation_id}")

        except Exception as conv_error:
            logger.error(f"Failed to create/update conversation: {str(conv_error)}")
            # Continue processing even if conversation creation fails

        image_content = ""  # Check for image pre-processing
        try:
            # First check if image_url is provided in the request JSON data
            if request_data.image_url and request_data.image_url.strip():
                logger.info(f"Image URL found in request data: {request_data.image_url}")
                
                # Query the database to get the actual image URL (in case frontend passes a reference)
                request_result = supabase.from_('requests').select('image_url').eq('request_id', request_id).execute()
                if request_result.data and len(request_result.data) > 0:
                    db_image_url = request_result.data[0].get('image_url')
                    # Use database URL if available, otherwise use the one from request data
                    image_url = db_image_url if db_image_url else request_data.image_url
                else:
                    # Use the image URL from request data if database lookup fails
                    image_url = request_data.image_url
                
                logger.info(f"Processing image URL: {image_url}")
                
                # Process image with vision model
                extracted_info = process_image_with_vision(request_data.message, image_url)
                if extracted_info:
                    image_content = "Additional context from user provided image: " + extracted_info
                    logger.info(f"Enhanced user message with image context: {len(extracted_info)} characters")
                else:
                    logger.warning("Vision processing failed - continuing with original message")
            else:
                logger.debug("No image URL provided in request data")
                
        except Exception as vision_error:
            logger.error(f"Error during image pre-processing: {str(vision_error)}")
            # Continue with original message - don't let vision processing errors break the chat flow

        # Store user message in messages table (idempotent)
        try:
            if conversation_id:
                # Check if user message already exists for this request_id to ensure idempotency
                existing_user_msg = supabase.from_('messages').select('id').eq(
                    'conversation_id', conversation_id
                ).eq('role', 'user').filter(
                    'metadata->>request_id', 'eq', request_id
                ).execute()

                if existing_user_msg.data and len(existing_user_msg.data) > 0:
                    logger.info(f"User message already exists for request_id: {request_id}, skipping insert")
                else:
                    user_message_content = request_data.message
                    # Append image context if available
                    if image_content:
                        user_message_content += "\n\n" + image_content

                    user_message_result = supabase.from_('messages').insert({
                        'conversation_id': conversation_id,
                        'user_id': user_id,
                        'role': 'user',
                        'content': user_message_content,
                        'metadata': {
                            'request_id': request_id,
                            'has_image': bool(request_data.image_url),
                            'image_url': request_data.image_url if request_data.image_url else None,
                            'vision_processed': bool(image_content)
                        },
                        'created_at': datetime.now().isoformat()
                    }).execute()

                    if user_message_result.data:
                        logger.info(f"Stored user message for conversation: {conversation_id}")
                    else:
                        logger.error("Failed to store user message")
            else:
                logger.warning("No conversation_id available, skipping user message storage")

        except Exception as msg_error:
            logger.error(f"Failed to store user message: {str(msg_error)}")
            # Continue processing even if message storage fails

        # Update request status to thinking
        try:
            supabase.from_('requests').update({
                'status': 'thinking',
                'updated_at': datetime.now().isoformat()
            }).eq('request_id', request_id).execute()
        except Exception as db_error:
            logger.error(f"Failed to update request status to thinking: {str(db_error)}")

        response, settings_updated, integration_in_progress = await get_chat_response(
            messages=request_data.history,
            user_id=user_id,
            supabase=supabase,
            request_id=request_id,
            integration_in_progress=request_data.integration_in_progress,
            image_content=image_content
        )

        if not response:
            # Update request status to failed
            try:
                supabase.from_('requests').update({
                    'status': 'failed',
                    'metadata': {'error': 'No response received from agent'},
                    'updated_at': datetime.now().isoformat()
                }).eq('request_id', request_id).execute()
            except Exception as db_error:
                logger.error(f"Failed to update request status to failed: {str(db_error)}")
            
            raise HTTPException(
                status_code=500,
                detail="No response received from agent"
            )
        elif (response.strip().startswith(("Error:", "ERROR:", "Incorrect API key", "API Error")) or
              (len(response) < 200 and any(pattern in response for pattern in [
                  "API key provided", "authentication failed", "rate limit exceeded"
              ]))):
            response = response[:50]
        
        # Filter out 'Perfect!' from the response string
        response = response.replace("Perfect!", "").strip()

        # Store assistant response in messages table (idempotent)
        try:
            if conversation_id and response:
                # Check if assistant message already exists for this request_id to ensure idempotency
                # NOTE: THERE IS ALSO A DEDUPING DB CONSTRAINT WITH THE SAME LOGIC
                existing_assistant_msg = supabase.from_('messages').select('id').eq(
                    'conversation_id', conversation_id
                ).eq('role', 'assistant').filter(
                    'metadata->>request_id', 'eq', request_id
                ).execute()

                if existing_assistant_msg.data and len(existing_assistant_msg.data) > 0:
                    logger.info(f"Assistant message already exists for request_id: {request_id}, skipping insert")
                else:
                    assistant_message_result = supabase.from_('messages').insert({
                        'conversation_id': conversation_id,
                        'user_id': user_id,
                        'role': 'assistant',
                        'content': response,
                        'metadata': {
                            'request_id': request_id,
                            'settings_updated': settings_updated,
                            'integration_in_progress': integration_in_progress,
                            'response_length': len(response)
                        },
                        'created_at': datetime.now().isoformat()
                    }).execute()

                    if assistant_message_result.data:
                        logger.info(f"Stored assistant response for conversation: {conversation_id}")
                    else:
                        logger.error("Failed to store assistant response")
            else:
                if not conversation_id:
                    logger.warning("No conversation_id available, skipping assistant message storage")
                if not response:
                    logger.warning("No response content, skipping assistant message storage")

        except Exception as msg_error:
            logger.error(f"Failed to store assistant message: {str(msg_error)}")
            # Continue processing even if message storage fails

        # Update request status to completed (but preserve cancelled status if set)
        try:
            # First check current status to avoid overriding cancellation
            current_status_result = supabase.from_('requests').select('status').eq('request_id', request_id).execute()
            current_status = 'pending'  # default fallback
            
            if current_status_result.data and len(current_status_result.data) > 0:
                current_status = current_status_result.data[0]['status']
            
            # Only update to completed if not already cancelled
            if current_status != 'cancelled':
                supabase.from_('requests').update({
                    'status': 'completed',
                    'assistant_response': response,
                    'metadata': {
                        'message_preview': request_data.message[:100],
                        'history_count': len(request_data.history),
                        'preferences': request_data.preferences,
                        'settings_updated': settings_updated,
                        'integration_in_progress': integration_in_progress,
                        'response_preview': response[:100],
                        'vision_processing': 'completed' if request_data.image_url and 'Additional context from image:' in request_data.message else ('attempted' if request_data.image_url else 'none')
                    },
                    'updated_at': datetime.now().isoformat()
                }).eq('request_id', request_id).execute()
                logger.info(f"Request completed successfully: {request_id}")
                
                # Increment requests_today counter for successful requests (only if not integration completion and no limits exceeded)
                if not request_data.integration_in_progress and not limit_exceeded:
                    try:
                        increment_result = supabase.rpc(
                            'increment_user_usage',
                            {
                                'user_id': str(user_id),
                                'usage_type': 'requests_today'
                            }
                        ).execute()
                        
                        if increment_result.data is None:
                            logger.error(f"Failed to increment requests_today for user {user_id}")
                        else:
                            logger.info(f"Incremented requests_today for user {user_id}")
                        
                        # Check if we need to charge overage for this request
                        if will_incur_overage and overage_cost_cents > 0:
                            try:
                                # Charge overage cost to UBP current
                                overage_result = supabase.rpc(
                                    'increment_user_usage_with_cost',
                                    {
                                        'user_id': str(user_id),
                                        'usage_type': 'requests_month',  # Track the overage request
                                        'cost_cents': overage_cost_cents
                                    }
                                ).execute()
                                
                                if overage_result.data is None:
                                    logger.error(f"Failed to charge request overage for user {user_id}")
                                else:
                                    logger.info(f"Charged ${overage_cost_cents/100:.2f} request overage for user {user_id}")
                                    
                                    # Send Stripe meter event for request overage
                                    try:
                                        # Get user profile to get stripe_customer_id
                                        profile_result = supabase.from_('user_profiles').select('stripe_customer_id').eq('id', user_id).execute()
                                        if profile_result.data and profile_result.data[0].get('stripe_customer_id'):
                                            stripe_customer_id = profile_result.data[0]['stripe_customer_id']
                                            await stripe_service.send_request_overage_event(
                                                stripe_customer_id=stripe_customer_id,
                                                user_id=user_id
                                            )
                                        else:
                                            logger.warning(f"No Stripe customer ID found for user {user_id} - skipping meter event")
                                    except Exception as stripe_error:
                                        logger.error(f"Error sending Stripe meter event for user {user_id}: {str(stripe_error)}")
                                        # Don't re-raise - Stripe failures shouldn't block the request
                                        
                            except Exception as overage_error:
                                logger.error(f"Error charging request overage for user {user_id}: {str(overage_error)}")
                            
                    except Exception as usage_error:
                        logger.error(f"Error incrementing requests_today for user {user_id}: {str(usage_error)}")
                else:
                    logger.info(f"Skipping usage increment for integration completion request: {request_id}")
            else:
                # Update metadata but preserve cancelled status
                supabase.from_('requests').update({
                    'metadata': {
                        'message_preview': request_data.message[:100],
                        'history_count': len(request_data.history),
                        'preferences': request_data.preferences,
                        'settings_updated': settings_updated,
                        'integration_in_progress': integration_in_progress,
                        'response_preview': response[:100],
                        'cancellation_preserved': True,
                        'vision_processing': 'completed' if request_data.image_url and 'Additional context from image:' in request_data.message else ('attempted' if request_data.image_url else 'none')
                    },
                    'updated_at': datetime.now().isoformat()
                }).eq('request_id', request_id).execute()
                logger.info(f"Request was cancelled, preserving cancelled status: {request_id}")
                
                # Note: Don't increment requests_today for cancelled requests
        except Exception as db_error:
            logger.error(f"Failed to update request status: {str(db_error)}")
        
        logger.info(f"Settings updated: {settings_updated}")
        return ChatResponse(
            response=response, 
            timestamp=int(datetime.now().timestamp()),
            settings_updated=settings_updated,
            integration_in_progress=integration_in_progress
        )

    except Exception as e:
        # Update request status to failed if we have a request_id (but preserve cancelled status)
        if 'request_id' in locals():
            try:
                # Check current status to avoid overriding cancellation
                current_status_result = supabase.from_('requests').select('status').eq('request_id', request_id).execute()
                current_status = 'pending'  # default fallback
                
                if current_status_result.data and len(current_status_result.data) > 0:
                    current_status = current_status_result.data[0]['status']
                
                # Only update to failed if not already cancelled
                if current_status != 'cancelled':
                    supabase.from_('requests').update({
                        'status': 'failed',
                        'metadata': {'error': str(e)},
                        'updated_at': datetime.now().isoformat()
                    }).eq('request_id', request_id).execute()
                    logger.info(f"Request failed: {request_id}")
                else:
                    # Update metadata but preserve cancelled status
                    supabase.from_('requests').update({
                        'metadata': {'error': str(e), 'cancellation_preserved': True},
                        'updated_at': datetime.now().isoformat()
                    }).eq('request_id', request_id).execute()
                    logger.info(f"Request was cancelled (error occurred but status preserved): {request_id}")
            except Exception as db_error:
                logger.error(f"Failed to update request status: {str(db_error)}")
        
        # Note: Cache cleanup is handled by middleware in finally block
        # Each request's cache is isolated, so failures don't affect other requests
        
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process chat request: {str(e)}"
        )


