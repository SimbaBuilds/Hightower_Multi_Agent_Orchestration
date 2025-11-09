import json
import os
from fastapi import Request, Form, APIRouter
from fastapi import HTTPException
from uuid import uuid4
from app.agents.models import Message
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
import dotenv
from app.services.request_cache import RequestCacheService

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
    json_data: str = Form(...),
) -> ChatResponse:
    try:
        request_data = ChatRequest(**json.loads(json_data))

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

        logger.info(f"Received chat request: message='{request_data.message}', history_count={len(request_data.history)}, request_id={request_id}")

        # Return a simple mock response
        response = f"Echo: {request_data.message[:100]}"

        return ChatResponse(
            response=response,
            timestamp=int(datetime.now().timestamp()),
            settings_updated=False,
            integration_in_progress=False
        )

    except Exception as e:
        logger.error(f"Failed to process chat request: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process chat request: {str(e)}"
        )
