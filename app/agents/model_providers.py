from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from openai import OpenAI
import requests
import json
from google import genai
import os
import time
import random
import base64
from dotenv import load_dotenv
from app.utils.logging.component_loggers import get_agent_logger

load_dotenv(override=True)

class RetryConfig:
    """Configuration for retry logic"""
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 60.0, enable_fallback: bool = True):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.enable_fallback = enable_fallback

def exponential_backoff_retry(func):
    """Decorator to add exponential backoff retry logic to model provider methods"""
    def wrapper(self, *args, **kwargs):
        retry_config = getattr(self, 'retry_config', RetryConfig())
        
        for attempt in range(retry_config.max_retries + 1):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                # Don't retry on the last attempt
                if attempt == retry_config.max_retries:
                    raise e
                
                # Check if this is a retryable error
                if not _is_retryable_error(e):
                    raise e
                
                # Calculate delay with exponential backoff and jitter
                delay = min(
                    retry_config.base_delay * (2 ** attempt),
                    retry_config.max_delay
                )
                # Add jitter to prevent thundering herd
                delay += random.uniform(0, delay * 0.1)
                
                print(f"Attempt {attempt + 1} failed with {type(e).__name__}: {str(e)}")
                print(f"Retrying in {delay:.2f} seconds...")
                time.sleep(delay)
        
        return None  # Should never reach here
    return wrapper

def _is_retryable_error(error: Exception) -> bool:
    """Determine if an error is retryable"""
    # OpenAI specific errors
    if hasattr(error, 'status_code'):
        status_code = error.status_code
        # Retry on server errors (5xx) and rate limits (429)
        if status_code >= 500 or status_code == 429:
            return True
        # Don't retry on client errors (4xx except 429)
        if 400 <= status_code < 500:
            return False
    
    # Requests specific errors
    if isinstance(error, requests.exceptions.RequestException):
        if isinstance(error, requests.exceptions.HTTPError):
            if hasattr(error, 'response') and error.response is not None:
                status_code = error.response.status_code
                # Retry on server errors and rate limits
                if status_code >= 500 or status_code == 429:
                    return True
                # Don't retry on client errors
                if 400 <= status_code < 500:
                    return False
        # Retry on timeout, connection errors, etc.
        if isinstance(error, (requests.exceptions.Timeout, 
                            requests.exceptions.ConnectionError,
                            requests.exceptions.ChunkedEncodingError)):
            return True
    
    # Check error message for common retryable patterns
    error_str = str(error).lower()
    retryable_patterns = [
        'timeout', 'connection', 'server error', 'internal error',
        'service unavailable', 'bad gateway', 'gateway timeout',
        'rate limit', 'too many requests'
    ]
    
    for pattern in retryable_patterns:
        if pattern in error_str:
            return True
    
    # Default to not retryable for unknown errors
    return False

def get_fallback_providers():
    """Get the ordered list of fallback providers"""
    return ['anthropic', 'google', 'openai', 'xai']

def is_provider_available(provider_name: str) -> bool:
    """Check if a provider has the required API key configured"""
    provider_keys = {
        'openai': 'OPENAI_API_KEY',
        'anthropic': 'ANTHROPIC_API_KEY', 
        'xai': 'XAI_API_KEY',
        'google': 'GEMINI_API_KEY'
    }
    
    if provider_name not in provider_keys:
        return False
        
    api_key = os.getenv(provider_keys[provider_name])
    return api_key is not None and api_key.strip() != ''

def create_fallback_provider(provider_name: str, retry_config: Optional[RetryConfig] = None):
    """Create a provider instance for fallback use"""
    if not is_provider_available(provider_name):
        return None
        
    if retry_config is None:
        retry_config = RetryConfig()
    
    try:
        if provider_name == 'openai':
            return OpenAIProvider(retry_config=retry_config)
        elif provider_name == 'anthropic':
            return AnthropicProvider(retry_config=retry_config)
        elif provider_name == 'xai':
            return GrokProvider(retry_config=retry_config)
        elif provider_name == 'google':
            return GoogleProvider(retry_config=retry_config)
        else:
            return None
    except Exception as e:
        print(f"Failed to create {provider_name} provider: {str(e)}")
        return None

class ModelProvider(ABC):
    """Abstract base class for different LLM providers"""
    
    @abstractmethod
    def generate_response(self, messages: List[Dict[str, Any]], temperature: float) -> str:
        """Generate a response from the model given a list of messages"""
        pass
    
    def generate_vision_response(self, text_prompt: str, image_url: str, temperature: float = 0.1) -> str:
        """Generate a response from a vision-capable model given text and image
        
        Args:
            text_prompt: The text prompt/question about the image
            image_url: URL to the image to analyze
            temperature: Sampling temperature for response generation
            
        Returns:
            Model's response analyzing the image based on the text prompt
        """
        raise NotImplementedError("Vision response not implemented for this provider")

class OpenAIProvider(ModelProvider):
    """OpenAI model provider implementation"""
    
    DEFAULT_MODEL = "o3-mini-2025-01-31"
    
    def __init__(self, model: Optional[str] = None, retry_config: Optional[RetryConfig] = None):
        self.logger = get_agent_logger('OpenAIProvider', __name__)
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model if model is not None else self.DEFAULT_MODEL
        self.retry_config = retry_config if retry_config is not None else RetryConfig()
        self.logger.info(f"Initialized OpenAIProvider with model: {self.model}")
    
    @exponential_backoff_retry
    def generate_response(self, messages: List[Dict[str, Any]], temperature: float) -> str:
        # Some OpenAI models (like o3) do not support the temperature parameter
        # We'll omit it if the model name starts with 'o3-'
        if self.model.startswith("o3-"):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages
            )
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature
            )
        completion = response.choices[0].message.content
        self.logger.info(f"Generated response from OpenAIProvider (model: {self.model})")
        return completion
    
    @exponential_backoff_retry
    def generate_vision_response(self, text_prompt: str, image_url: str, temperature: float = 0.1) -> str:
        """Generate response using GPT-4o vision capabilities"""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": text_prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url
                        }
                    }
                ]
            }
        ]
        
        # Use GPT-4o for vision tasks
        vision_model = "gpt-4o"
        response = self.client.chat.completions.create(
            model=vision_model,
            messages=messages,
            temperature=temperature,
            max_tokens=500
        )
        
        completion = response.choices[0].message.content
        self.logger.info(f"Generated vision response from OpenAIProvider (model: {vision_model})")
        return completion

class AnthropicProvider(ModelProvider):
    """Anthropic model provider implementation using custom HTTP requests"""
    
    DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
    API_URL = "https://api.anthropic.com/v1/messages"
    
    def __init__(self, model: Optional[str] = None, retry_config: Optional[RetryConfig] = None):
        self.logger = get_agent_logger('AnthropicProvider', __name__)
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        # Debug: Check if API key is properly formatted (should start with 'sk-ant-')
        if not self.api_key.startswith('sk-ant-'):
            raise ValueError("ANTHROPIC_API_KEY appears to be invalid - should start with 'sk-ant-'")
        self.model = model if model is not None else self.DEFAULT_MODEL
        self.retry_config = retry_config if retry_config is not None else RetryConfig()
        self.logger.info(f"Initialized AnthropicProvider with model: {self.model}")
    
    @exponential_backoff_retry
    def generate_response(self, messages: List[Dict[str, Any]], temperature: float) -> str:
        """Generate response using custom HTTP requests to Anthropic API"""
        
        # Extract and process system message with cache control support
        system_content = None
        for msg in messages:
            if msg["role"] == "system":
                system_content = msg.get("content")
                break
        
        # Build the messages for Anthropic (exclude system messages)
        anthropic_messages = []
        for msg in messages:
            if msg["role"] != "system":  # System message is handled separately
                anthropic_messages.append({
                    "role": "user" if msg["role"] == "user" else "assistant",
                    "content": msg["content"]
                })
        
        # Prepare the request payload
        payload = {
            "model": self.model,
            "max_tokens": 4000,
            "temperature": temperature,
            "messages": anthropic_messages
        }
        
        # Add system prompt with cache control support
        if system_content:
            # Log system content size for cache debugging
            if isinstance(system_content, list):
                total_tokens = sum(len(block.get("text", "")) for block in system_content if isinstance(block, dict))
                self.logger.info(f"System prompt size: ~{total_tokens} characters in {len(system_content)} blocks")
            else:
                self.logger.info(f"System prompt size: {len(str(system_content))} characters")

            # Check if system_content is structured (with cache control) or plain text
            if isinstance(system_content, list):
                # Already structured for cache control
                payload["system"] = system_content
            elif isinstance(system_content, str):
                # Plain text system prompt (backward compatibility)
                payload["system"] = system_content
            else:
                # Handle dict format (single system block with cache control)
                payload["system"] = [system_content]
        
        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"
        }
        
        try:
            # Make the HTTP request
            response = requests.post(
                self.API_URL,
                headers=headers,
                json=payload,
                timeout=60
            )
            
            # Check if request was successful
            response.raise_for_status()
            
            # Parse the response
            response_data = response.json()

            # Extract the content from the response
            if "content" in response_data and len(response_data["content"]) > 0:
                # Log cache usage metrics if available
                usage_info = response_data.get("usage", {})
                cache_creation_tokens = usage_info.get("cache_creation_input_tokens", 0)
                cache_read_tokens = usage_info.get("cache_read_input_tokens", 0)
                input_tokens = usage_info.get("input_tokens", 0)
                output_tokens = usage_info.get("output_tokens", 0)

                # Calculate cache efficiency
                if input_tokens > 0:
                    cache_hit_rate = (cache_read_tokens / input_tokens) * 100 if cache_read_tokens > 0 else 0
                    cache_write_rate = (cache_creation_tokens / input_tokens) * 100 if cache_creation_tokens > 0 else 0
                else:
                    cache_hit_rate = 0
                    cache_write_rate = 0

                # Detailed cache logging
                if cache_creation_tokens > 0 or cache_read_tokens > 0:
                    self.logger.info(f"🔄 CACHE METRICS - Model: {self.model}")
                    self.logger.info(f"  📝 Cache Write: {cache_creation_tokens} tokens ({cache_write_rate:.1f}% of input)")
                    self.logger.info(f"  📖 Cache Read: {cache_read_tokens} tokens ({cache_hit_rate:.1f}% of input)")
                    self.logger.info(f"  💾 Total Input: {input_tokens} tokens, Output: {output_tokens} tokens")
                    self.logger.info(f"  ✅ Cache Status: {'HIT' if cache_read_tokens > 0 else 'MISS'}")
                else:
                    self.logger.info(f"Generated response from AnthropicProvider (model: {self.model}) - No cache usage")

                return response_data["content"][0]["text"]
            else:
                raise ValueError("Unexpected response format from Anthropic API")
                
        except requests.exceptions.RequestException as e:
            # Log detailed error information for HTTP responses
            error_details = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_body = e.response.json()
                    self.logger.error(f"Anthropic API error details: {error_body}")
                    error_details = f"{error_details} - Response: {error_body}"
                except:
                    self.logger.error(f"Anthropic API error response body: {e.response.text}")
                    error_details = f"{error_details} - Response: {e.response.text}"
            raise RuntimeError(f"Error making request to Anthropic API: {error_details}")
        except (KeyError, IndexError, ValueError) as e:
            raise RuntimeError(f"Error parsing Anthropic API response: {str(e)}")
    
    @exponential_backoff_retry
    def generate_vision_response(self, text_prompt: str, image_url: str, temperature: float = 0.1) -> str:
        """Generate response using Claude vision capabilities"""
        
        # Claude doesn't support image URLs - need to fetch and convert to base64
        try:
            # Fetch the image from URL
            image_response = requests.get(image_url, timeout=30)
            image_response.raise_for_status()
            
            # Convert to base64
            image_base64 = base64.b64encode(image_response.content).decode('utf-8')
            
            # Detect image format from image data (magic bytes)
            image_data = image_response.content
            if image_data.startswith(b'\x89PNG\r\n\x1a\n'):
                content_type = 'image/png'
            elif image_data.startswith(b'\xff\xd8\xff'):
                content_type = 'image/jpeg'
            elif image_data.startswith(b'GIF87a') or image_data.startswith(b'GIF89a'):
                content_type = 'image/gif'
            elif image_data.startswith(b'RIFF') and b'WEBP' in image_data[:12]:
                content_type = 'image/webp'
            else:
                # Fallback to HTTP header if we can't detect from magic bytes
                content_type = image_response.headers.get('content-type', 'image/jpeg')
                if 'image/' not in content_type:
                    content_type = 'image/jpeg'  # Final fallback
                
            self.logger.info(f"Converted image to base64 for Claude vision API (size: {len(image_base64)} chars, type: {content_type})")
            
        except Exception as e:
            raise RuntimeError(f"Failed to fetch and convert image for Claude vision API: {str(e)}")
        
        # Build the messages for Anthropic with vision (base64 format)
        anthropic_messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": text_prompt
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": content_type,
                            "data": image_base64
                        }
                    }
                ]
            }
        ]
        
        # Prepare the request payload for vision
        payload = {
            "model": self.model,  # claude-sonnet already supports vision
            "max_tokens": 500,
            "temperature": temperature,
            "messages": anthropic_messages
        }
        
        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"
        }
        
        try:
            # Make the HTTP request
            response = requests.post(
                self.API_URL,
                headers=headers,
                json=payload,
                timeout=60
            )
            
            # Check if request was successful
            response.raise_for_status()
            
            # Parse the response
            response_data = response.json()
            
            # Extract the content from the response
            if "content" in response_data and len(response_data["content"]) > 0:
                self.logger.info(f"Generated vision response from AnthropicProvider (model: {self.model})")
                return response_data["content"][0]["text"]
            else:
                raise ValueError("Unexpected response format from Anthropic API")
                
        except requests.exceptions.RequestException as e:
            # Log detailed error information for HTTP responses
            error_details = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_body = e.response.json()
                    self.logger.error(f"Anthropic Vision API error details: {error_body}")
                    error_details = f"{error_details} - Response: {error_body}"
                except:
                    self.logger.error(f"Anthropic Vision API error response body: {e.response.text}")
                    error_details = f"{error_details} - Response: {e.response.text}"
            raise RuntimeError(f"Error making vision request to Anthropic API: {error_details}")
        except (KeyError, IndexError, ValueError) as e:
            raise RuntimeError(f"Error parsing Anthropic vision API response: {str(e)}")

class GrokProvider(ModelProvider):
    """Grok (x.ai) model provider implementation"""
    
    DEFAULT_MODEL = "grok-3"
    
    def __init__(self, model: Optional[str] = None, retry_config: Optional[RetryConfig] = None):
        self.logger = get_agent_logger('GrokProvider', __name__)
        # Grok uses OpenAI-compatible API with custom base URL
        self.client = OpenAI(
            api_key=os.getenv("XAI_API_KEY"),
            base_url="https://api.x.ai/v1"
        )
        self.model = model if model is not None else self.DEFAULT_MODEL
        self.retry_config = retry_config if retry_config is not None else RetryConfig()
        self.logger.info(f"Initialized GrokProvider with model: {self.model}")
    
    @exponential_backoff_retry
    def generate_response(self, messages: List[Dict[str, Any]], temperature: float) -> str:
        # Convert display name to API model name
        model_lower = self.model.lower()
        if model_lower == "grok 3":
            api_model = "grok-3"
        elif model_lower == "grok 3.5":
            api_model = "grok-3.5"
        elif self.model == "grok-3":  # Already in API format
            api_model = "grok-3"
        elif self.model == "grok-3.5":  # Already in API format
            api_model = "grok-3.5"
        else:
            raise ValueError(f"Invalid model: {self.model}")
        
        response = self.client.chat.completions.create(
            model=api_model,
            messages=messages,
            temperature=temperature
        )
        completion = response.choices[0].message.content
        self.logger.info(f"Generated response from GrokProvider (model: {self.model})")
        return completion
    
    @exponential_backoff_retry
    def generate_vision_response(self, text_prompt: str, image_url: str, temperature: float = 0.1) -> str:
        """Generate response using Grok vision capabilities"""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text", 
                        "text": text_prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url,
                            "detail": "high"
                        }
                    }
                ]
            }
        ]
        
        # Use grok-vision-beta for vision tasks
        vision_model = "grok-vision-beta"
        response = self.client.chat.completions.create(
            model=vision_model,
            messages=messages,
            temperature=temperature,
            max_tokens=500
        )
        
        completion = response.choices[0].message.content
        self.logger.info(f"Generated vision response from GrokProvider (model: {vision_model})")
        return completion


class GoogleProvider(ModelProvider):
    """Google Gemini model provider implementation"""
    
    DEFAULT_MODEL = "gemini-2.5-pro"
    
    def __init__(self, model: Optional[str] = None, retry_config: Optional[RetryConfig] = None):
        self.logger = get_agent_logger('GoogleProvider', __name__)
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")
        self.client = genai.Client(api_key=self.api_key)
        self.model = model if model is not None else self.DEFAULT_MODEL
        self.retry_config = retry_config if retry_config is not None else RetryConfig()
        self.logger.info(f"Initialized GoogleProvider with model: {self.model}")
    
    @exponential_backoff_retry
    def generate_response(self, messages: List[Dict[str, Any]], temperature: float) -> str:
        # Convert messages to Gemini format
        system_prompt = next((msg["content"] for msg in messages if msg["role"] == "system"), None)
        
        # Build the content for Gemini
        contents = []
        for msg in messages:
            if msg["role"] == "user":
                contents.append(msg["content"])
            elif msg["role"] == "assistant":
                # For conversation history, we can add assistant messages as context
                contents.append(f"Assistant: {msg['content']}")
        
        # Combine all content into a single prompt
        if system_prompt:
            full_content = f"System: {system_prompt}\n\n" + "\n\n".join(contents)
        else:
            full_content = "\n\n".join(contents)
        
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=full_content
            )
            
            if hasattr(response, 'text') and response.text:
                self.logger.info(f"Generated response from GoogleProvider (model: {self.model})")
                return response.text
            else:
                raise ValueError("Unexpected response format from Gemini API")
                
        except Exception as e:
            raise RuntimeError(f"Error generating response from Gemini API: {str(e)}")
    
    @exponential_backoff_retry
    def generate_vision_response(self, text_prompt: str, image_url: str, temperature: float = 0.1) -> str:
        """Generate response using Gemini vision capabilities"""
        
        # Combine text prompt and image URL for Gemini
        # Gemini can process images by including the URL in the content
        full_content = f"{text_prompt}\n\nImage URL: {image_url}"
        
        try:
            response = self.client.models.generate_content(
                model=self.model,  # gemini-2.5-pro supports vision
                contents=full_content
            )
            
            if hasattr(response, 'text') and response.text:
                self.logger.info(f"Generated vision response from GoogleProvider (model: {self.model})")
                return response.text
            else:
                raise ValueError("Unexpected response format from Gemini API")
                
        except Exception as e:
            raise RuntimeError(f"Error generating vision response from Gemini API: {str(e)}")