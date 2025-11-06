"""
Vision processing service for extracting information from images based on user requests.
"""

from typing import Optional, List
from app.agents.model_providers import (
    create_fallback_provider, 
    get_fallback_providers, 
    is_provider_available,
    RetryConfig
)
from app.utils.logging.component_loggers import get_api_logger

logger = get_api_logger(__name__)

def process_image_with_vision(user_message: str, image_url: str) -> Optional[str]:
    """
    Process an image using vision-capable LLM to extract relevant information 
    based on the user's request.
    
    Args:
        user_message: The user's original message/request
        image_url: URL to the image to be processed
        
    Returns:
        Extracted information from the image, or None if processing fails
    """
    system_prompt = f"Please extract relevant information from this image based on the user request: {user_message}.  Your only job is to extract relevant content from the image and provide it in your response.  Do not include anything else in your response."
    
    # Get ordered list of fallback providers
    providers = get_fallback_providers()
    
    for provider_name in providers:
        if not is_provider_available(provider_name):
            logger.info(f"Skipping {provider_name} provider - not available")
            continue
            
        try:
            logger.info(f"Attempting vision processing with {provider_name} provider")
            
            # Create provider instance with retry configuration
            retry_config = RetryConfig(max_retries=2, base_delay=1.0, max_delay=30.0)
            provider = create_fallback_provider(provider_name, retry_config)
            
            if provider is None:
                logger.warning(f"Failed to create {provider_name} provider instance")
                continue
                
            # Check if provider supports vision
            if not hasattr(provider, 'generate_vision_response'):
                logger.info(f"{provider_name} provider does not support vision - skipping")
                continue
            
            # Process image with vision model
            extracted_info = provider.generate_vision_response(
                text_prompt=system_prompt,
                image_url=image_url,
                temperature=0.1
            )
            
            if extracted_info:
                logger.info(f"Successfully processed image with {provider_name} provider")
                return extracted_info
            else:
                logger.warning(f"{provider_name} provider returned empty response")
                
        except Exception as e:
            logger.error(f"Error processing image with {provider_name} provider: {str(e)}")
            continue
    
    # If all providers failed
    logger.error("All vision providers failed to process the image")
    return None

def get_vision_capable_providers() -> List[str]:
    """
    Get list of providers that support vision processing.
    
    Returns:
        List of provider names that support vision
    """
    vision_providers = []
    for provider_name in get_fallback_providers():
        if is_provider_available(provider_name):
            try:
                provider = create_fallback_provider(provider_name)
                if provider and hasattr(provider, 'generate_vision_response'):
                    vision_providers.append(provider_name)
            except Exception:
                continue
    
    return vision_providers