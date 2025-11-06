from typing import Dict, Any, List, Callable, Optional, Tuple, Literal
from uuid import UUID
import re
import inspect
from datetime import datetime, timezone
from dotenv import load_dotenv

from app.agents.models import Action, Message
from app.agents.model_providers import OpenAIProvider, AnthropicProvider, GoogleProvider, GrokProvider, RetryConfig, get_fallback_providers, is_provider_available, create_fallback_provider
from app.agents.prompt_templates import build_system_prompt
from app.utils.logging.component_loggers import get_agent_logger, log_agent_event
from app.config import INTELLIGENCE_MODEL_MAP, USE_ONE_HOUR_CACHE

load_dotenv()


"""
Utility functions for model and provider management.
"""

from typing import Optional


def get_provider_from_model(model: str) -> str:
    """
    Determine the provider name from a model name.
    
    Args:
        model: The model name (e.g., "gpt-4", "claude-sonnet-4-5-20250929", "gemini-2.5-pro")
        
    Returns:
        The provider name ("openai", "anthropic", "google", "xai")
        
    Raises:
        ValueError: If the model name doesn't match any known provider pattern
    """
    if not model:
        raise ValueError("Model name cannot be empty")
    
    model_lower = model.lower()
    
    # Check for x.ai/Grok models
    if "grok" in model_lower:
        return "xai"
    
    # Check for OpenAI models (o3, gpt)
    elif "o3" in model_lower or "gpt" in model_lower:
        return "openai"
    
    # Check for Anthropic models (claude)
    elif "claude" in model_lower:
        return "anthropic"
    
    # Check for Google models (gemini)
    elif "gemini" in model_lower:
        return "google"
    
    else:
        raise ValueError(f"Unsupported model: {model}")


def is_valid_model_for_provider(model: str, provider: str) -> bool:
    """
    Check if a model is valid for a given provider.
    
    Args:
        model: The model name
        provider: The provider name
        
    Returns:
        True if the model is valid for the provider, False otherwise
    """
    try:
        detected_provider = get_provider_from_model(model)
        return detected_provider == provider
    except ValueError:
        return False 



class BaseAgent:
    def __init__(
        self,
        actions: List[Action],
        additional_context: str = None,
        general_instructions: Optional[str] = None,
        custom_examples: Optional[List[Dict[str, str]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_turns: int = 5,
        agent_name: str = "Agent",
        retry_config: Optional[RetryConfig] = None,
        calling_agent: str = None,
        enable_caching: bool = False,
        cache_static_content: bool = True
    ):
        """
        Initialize a base agent with customizable system prompt and actions.
        
        Args:
            actions: List of Action objects defining available actions with their handlers
            additional_context: The context that defines the agent's behavior
            general_instructions: User's general instructions for the AI agent behavior
            custom_examples: Optional list of example interactions
            model: The model to use
            temperature: The temperature parameter for generation
            max_turns: Maximum number of action/observation turns before returning
            agent_name: Name identifier for this agent instance
            retry_config: Configuration for retry logic with exponential backoff
            calling_agent: Name of the agent calling this agent
            enable_caching: Whether to enable Anthropic prompt caching (only for Anthropic models)
            cache_static_content: Whether to cache static content sections
        """
        provider = get_provider_from_model(model)

        # Store model for MAS logging
        self.model = model

        # Use default retry config if none provided
        if retry_config is None:
            retry_config = RetryConfig()
        
        # Initialize the appropriate model provider
        if provider == "openai":
            self.model_provider = OpenAIProvider(model=model, retry_config=retry_config)
        elif provider == "anthropic":
            self.model_provider = AnthropicProvider(model=model, retry_config=retry_config)
        elif provider == "xai":
            self.model_provider = GrokProvider(model=model, retry_config=retry_config)
        elif provider == "google":
            self.model_provider = GoogleProvider(model=model, retry_config=retry_config)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
            
        # Store provider info for fallback
        self.primary_provider = provider
        self.retry_config = retry_config
            
        self.temperature = temperature
        self.agent_name = agent_name
        self.calling_agent = calling_agent
        self.settings_updated = False  # Track if Config Agent updated settings
        self._last_observation = None  # Store the most recent observation for embedding
        self._log_sequence_counter = 0  # Counter for ensuring proper log ordering within milliseconds

        # Create logger with actual agent name
        self.logger = get_agent_logger(self.agent_name, __name__)
        
        all_actions = actions
        self.actions = {action.name: action for action in all_actions}  # Store actions by name
        
        self.messages = []
        self.action_re = re.compile('^Action: (\w+): (.*)', re.MULTILINE | re.IGNORECASE | re.DOTALL)
        self.max_turns = max_turns
        
        # Determine if caching should be enabled (only for Anthropic models)
        use_caching = enable_caching and provider == "anthropic"
        self.use_caching = use_caching  # Store for later use in observation messages
        
        # Create the system prompt using the template
        system_prompt = build_system_prompt(
            actions=all_actions,
            additional_context=additional_context,
            general_instructions=general_instructions,
            examples=custom_examples,
            calling_agent=calling_agent,
            enable_caching=use_caching,
            cache_static_content=cache_static_content
        )
        
        # Log the full system prompt and agent initialization
        # For cached prompts, provide verbose logging with cache indicators
        log_prompt = system_prompt
        if isinstance(system_prompt, list):
            # Build verbose log for cached prompt with clear cache indicators
            prompt_sections = []
            for i, section in enumerate(system_prompt):
                if isinstance(section, dict):
                    text = section.get("text", "")
                    has_cache = section.get("cache_control") is not None
                    
                    # Add cache indicator prefix if this section is cached
                    if has_cache:
                        cache_type = section.get("cache_control", {}).get("type", "unknown")
                        prompt_sections.append(f"[CACHED:{cache_type.upper()}]\n{text}")
                    else:
                        prompt_sections.append(f"[UNCACHED]\n{text}")
            
            # Join all sections with clear separators
            log_prompt = "\n\n" + "="*50 + "\n\n".join(prompt_sections)
            self.logger.info(f"{agent_name} using CACHED system prompt with {len(system_prompt)} sections")
        else:
            self.logger.info(f"{agent_name} using standard (uncached) system prompt")
            
        log_agent_event(
            self.logger,
            f"{agent_name} initialized with system prompt",
            agent_name=agent_name,
            action="agent_initialization",
            model=model,
            temperature=temperature,
            max_turns=max_turns,
            actions_count=len(all_actions),
            system_prompt=log_prompt,
            caching_enabled=use_caching
        )
        
        # Initialize with system prompt
        if system_prompt:
            self.messages.append({
                "role": "system",
                "content": system_prompt,
                "type": "text"
            })

    def _log_mas_step(self, log_type: str, content: str, user_id: UUID, request_id: str,
                      turn: int, supabase, action_name: str = None,
                      action_params: dict = None, metadata: dict = None):
        """Log a MAS step to the database with explicit timestamp for proper ordering."""
        if not supabase or not request_id:
            return

        try:
            # Generate application-level timestamp for consistent ordering
            # Use seconds precision with sequence counter for rapid sequential logs
            self._log_sequence_counter += 1
            base_timestamp = datetime.now(timezone.utc)

            # Add seconds offset for logs within the same second to maintain ordering
            timestamp_with_sequence = base_timestamp.replace(
                second=(base_timestamp.second + self._log_sequence_counter) % 60
            )

            log_data = {
                'request_id': request_id,
                'user_id': str(user_id),
                'type': log_type,
                'turn': turn,
                'agent_name': self.agent_name,
                'content': content[:10000],  # Limit content length
                'model': self.model,
                'action_name': action_name,
                'action_params': action_params,
                'metadata': metadata or {},
                'created_at': timestamp_with_sequence.isoformat()  # Explicit timestamp
            }

            supabase.from_('mas_logs').insert(log_data).execute()
            self.logger.debug(f"Logged MAS step: {log_type} for {self.agent_name}")

        except Exception as e:
            self.logger.error(f"Failed to log MAS step {log_type}: {str(e)}")

    def upgrade_intelligence(self, required_level: int):
        """Permanently upgrade agent to required intelligence level"""
        if required_level not in INTELLIGENCE_MODEL_MAP:
            self.logger.warning(f"Invalid intelligence level: {required_level}")
            return
            
        new_model = INTELLIGENCE_MODEL_MAP[required_level]
        if hasattr(self, 'model') and self.model == new_model:
            return  # Already at correct level
            
        old_model = getattr(self, 'model', 'unknown')
        
        # Update model and reinitialize provider
        self.model = new_model
        self._reinitialize_provider()
        
        # Log the upgrade
        log_agent_event(
            self.logger,
            f"Intelligence upgraded to level {required_level}",
            agent_name=self.agent_name,
            action="intelligence_upgrade",
            old_model=old_model,
            new_model=new_model,
            required_intelligence=required_level
        )

    def _reinitialize_provider(self):
        """Reinitialize model provider with current model"""
        provider = get_provider_from_model(self.model)
        
        # Initialize the appropriate model provider
        if provider == "openai":
            self.model_provider = OpenAIProvider(model=self.model, retry_config=self.retry_config)
        elif provider == "anthropic":
            self.model_provider = AnthropicProvider(model=self.model, retry_config=self.retry_config)
        elif provider == "xai":
            self.model_provider = GrokProvider(model=self.model, retry_config=self.retry_config)
        elif provider == "google":
            self.model_provider = GoogleProvider(model=self.model, retry_config=self.retry_config)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
            
        # Update provider info for fallback
        self.primary_provider = provider
    
    def _handle_max_turns_reached(self) -> str:
        """Handle max turns reached by requesting a progress summary from the agent."""

        # Add a message to the agent requesting a summary of progress
        summary_request = "You have reached your max turns for this task, please respond with a summary of your progress"

        self.logger.info(f"{self.agent_name} reached max turns, requesting progress summary")
        self.add_message(summary_request)

        try:
            # Execute one final turn to get the agent's summary
            response = self.execute()
            self.logger.info(f"{self.agent_name} progress summary: {response}")
            return response
        except Exception as e:
            self.logger.error(f"Error getting progress summary from {self.agent_name}: {str(e)}")
            # Fallback to a simple message if summary generation fails
            return f"I have reached my maximum number of actions ({self.max_turns}) and was unable to complete the task."

    def add_message(self, message: str | Dict[str, Any] | Message | List[Dict[str, Any] | Message], enable_caching: bool = False) -> None:
        """Add a message or list of messages to the conversation history."""
        if isinstance(message, list):
            for msg in message:
                if isinstance(msg, Message):
                    # Convert Pydantic model to dict
                    self.messages.append(msg.model_dump())
                elif isinstance(msg, dict) and 'role' in msg and 'content' in msg and 'type' in msg:
                    self.messages.append(msg)
                else:
                    raise ValueError("Each message must contain 'role', 'content', and 'type'.")
        elif isinstance(message, Message):
            # Convert Pydantic model to dict
            self.messages.append(message.model_dump())
        elif isinstance(message, dict):
            if 'role' in message and 'content' in message and 'type' in message:
                self.messages.append(message)
            else:
                raise ValueError("Message must contain 'role', 'content', and 'type'.")
        else:
            # Handle string input - support caching for observation messages
            message_content = str(message)
            
            # Determine if this should use structured content with cache_control
            if enable_caching and self.primary_provider == "anthropic":
                # Use structured content format for caching
                self.messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": message_content,
                            "cache_control": {"type": "ephemeral", "ttl": "1h" if USE_ONE_HOUR_CACHE else "5m"}
                        }
                    ]
                })
            else:
                # Use simple content format
                self.messages.append({
                    "role": "user",
                    "content": message_content,
                    "type": "text"
                })

    def execute(self) -> str:
        """Execute a single turn of conversation with the model."""
        self.logger.info(f"{self.agent_name} generating model response with {len(self.messages)} messages")
        
        # Try primary provider first
        try:
            response = self.model_provider.generate_response(self.messages, self.temperature)
            # Filter out the STOP HERE message that should not be visible to users
            response = response.replace("STOP HERE - You will be called again with the action result.", "").strip()
            response = response.replace("Stop your output here and you will be called again with the result of the action as an \"Observation\".", "").strip()

            self.logger.info(f"{self.agent_name} model response:\n{response}")
            return response
        except Exception as e:
            self.logger.warning(f"{self.agent_name} primary provider ({self.primary_provider}) failed: {str(e)}")
            
            # Try fallback providers if enabled
            if self.retry_config.enable_fallback:
                return self._try_fallback_providers(e)
            else:
                raise e
    
    def _try_fallback_providers(self, original_error: Exception) -> str:
        """Try fallback providers when primary provider fails"""
        fallback_providers = get_fallback_providers()
        
        # Remove the primary provider from fallback list
        if self.primary_provider in fallback_providers:
            fallback_providers.remove(self.primary_provider)
        
        for provider_name in fallback_providers:
            if not is_provider_available(provider_name):
                self.logger.info(f"Skipping {provider_name} fallback - API key not configured")
                continue
                
            try:
                self.logger.info(f"{self.agent_name} trying fallback provider: {provider_name}")
                fallback_provider = create_fallback_provider(provider_name, self.retry_config)
                
                if fallback_provider is None:
                    self.logger.warning(f"Failed to create {provider_name} fallback provider")
                    continue
                    
                response = fallback_provider.generate_response(self.messages, self.temperature)
                self.logger.info(f"{self.agent_name} fallback provider {provider_name} succeeded")
                return response
                
            except Exception as e:
                self.logger.warning(f"{self.agent_name} fallback provider {provider_name} failed: {str(e)}")
                continue
        
        # If all fallback providers failed, raise the original error
        self.logger.error(f"{self.agent_name} all providers failed, raising original error")
        raise original_error

    async def _execute_action_handler(self, action: Action, action_input: str) -> str:
        """Execute an action handler, handling both sync and async handlers."""
        if inspect.iscoroutinefunction(action.handler):
            # Async handler - use await
            return await action.handler(action_input)
        else:
            # Sync handler - call directly
            return action.handler(action_input)

    def _process_observation_embedding(self, response: str, observation: str) -> str:
        """
        Process response to embed observation content where $$$observation$$$ marker is found.

        Args:
            response: The agent's response that may contain $$$observation$$$ markers
            observation: The observation content to embed

        Returns:
            The response with embedded observation content
        """
        if not response or not observation:
            return response

        # Clean up the observation content - remove "Observation: " prefix if present
        clean_observation = observation
        if clean_observation.startswith("Observation: "):
            clean_observation = clean_observation[13:]  # Remove "Observation: " (13 characters)

        # Replace all occurrences of the marker with the observation content
        if "$$$observation$$$" in response:
            self.logger.info(f"{self.agent_name} embedding observation into response")
            response = response.replace("$$$observation$$$", clean_observation)

        return response

    async def process_actions(self, result: str) -> tuple[Optional[str], Optional[str]]:
        """Process actions with JSON parsing and fallback to legacy format."""
        import json
        
        self.logger.info(f"{self.agent_name} processing actions from response...")
        
        # Try JSON parsing first
        try:
            # Extract JSON from response (handle cases where there might be extra text)
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', result, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            elif result.strip().startswith('{') and result.strip().endswith('}'):
                json_str = result.strip()
            else:
                # Try to find JSON-like content
                json_match = re.search(r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', result, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    raise ValueError("No JSON found")
            
            parsed = json.loads(json_str)
            
            # Validate required fields
            if "thought" not in parsed:
                raise ValueError("Missing 'thought' field")
            if "type" not in parsed:
                raise ValueError("Missing 'type' field")
                
            thought = parsed["thought"]
            response_type = parsed["type"]

            self.logger.info(f"{self.agent_name} parsed JSON - thought: {thought[:100]}, type: {response_type}")

            # Log the thought step - we'll get turn number from the calling query method
            # For now, store the thought to log later when we have the turn number
            self._current_thought = thought
            
            if response_type == "action":
                if "action" not in parsed:
                    raise ValueError("Missing 'action' field for action type")
                
                action_data = parsed["action"]
                if "name" not in action_data:
                    raise ValueError("Missing action name")
                
                action_name = action_data["name"]
                action_params = action_data.get("parameters", {})
                
                # Validate action exists
                if action_name not in self.actions:
                    # Try case-insensitive match
                    action_name_lower = action_name.lower()
                    matched_action = None
                    for name in self.actions.keys():
                        if name.lower() == action_name_lower:
                            matched_action = name
                            break
                    
                    if not matched_action:
                        return f"Unknown action: {action_name}. Available: {', '.join(self.actions.keys())}", None
                    action_name = matched_action
                
                self.logger.info(f"{self.agent_name} processing action: {action_name}")

                # Store action info for logging later when we have turn number
                self._current_action = {
                    'name': action_name,
                    'params': action_params
                }

                try:
                    # Convert parameters to JSON string for handler
                    action_input = json.dumps(action_params) if action_params else "{}"
                    observation = await self._execute_action_handler(self.actions[action_name], action_input)
                    return None, f"Observation: {observation}"
                except Exception as e:
                    return f"Error executing {action_name}: {str(e)}", None
                    
            elif response_type == "response":
                if "response" not in parsed:
                    raise ValueError("Missing 'response' field for response type")
                
                self.logger.info(f"{self.agent_name} processing final response")
                return parsed["response"], None
            else:
                raise ValueError(f"Invalid type: {response_type}. Must be 'action' or 'response'")
                
        except (json.JSONDecodeError, ValueError, KeyError) as json_error:
            self.logger.warning(f"{self.agent_name} JSON parsing failed: {str(json_error)}, falling back to legacy parsing")
            
            # Fallback to legacy regex parsing
            thought_match = re.search(r'^(?:\d*\.?\s*)?(?:thought|thinking):\s*(.*?)(?=^(?:\d*\.?\s*)?(?:action|response):|\Z)', result, re.MULTILINE | re.IGNORECASE | re.DOTALL)
            action_match = re.search(r'^(?:\d*\.?\s*)?action:\s*(\w+):\s*(.*?)(?=^(?:\d*\.?\s*)?(?:observation|response|thought):|\Z)', result, re.MULTILINE | re.IGNORECASE | re.DOTALL)
            response_match = re.search(r'^(?:\d*\.?\s*)?response:\s*(.*?)(?=^(?:\d*\.?\s*)?(?:thought|action):|\Z)', result, re.MULTILINE | re.IGNORECASE | re.DOTALL)

            self.logger.info(f"{self.agent_name} legacy thought match: {thought_match.group(1)[:100] if thought_match else None}")
            self.logger.info(f"{self.agent_name} legacy action match: {action_match.group(1) + ': ' + action_match.group(2)[:50] if action_match else None}")
            self.logger.info(f"{self.agent_name} legacy response match: {response_match.group(1)[:100] if response_match else None}")

            # Store thought from legacy parsing
            if thought_match:
                self._current_thought = thought_match.group(1).strip()
            
            # Process action if found
            if action_match:
                action_name = action_match.group(1).strip()
                action_input = action_match.group(2).strip()
                
                # Validate action exists (prevents false positives)
                if action_name not in self.actions:
                    # Try case-insensitive match
                    action_name_lower = action_name.lower()
                    matched_action = None
                    for name in self.actions.keys():
                        if name.lower() == action_name_lower:
                            matched_action = name
                            break
                    
                    if not matched_action:
                        return f"Unknown action: {action_name}. Available: {', '.join(self.actions.keys())}", None
                    action_name = matched_action
                
                self.logger.info(f"{self.agent_name} processing legacy action: {action_name}")

                # Store action info for logging later when we have turn number
                self._current_action = {
                    'name': action_name,
                    'params': {'input': action_input}  # Legacy format stores as single input string
                }

                try:
                    observation = await self._execute_action_handler(self.actions[action_name], action_input)
                    return None, f"Observation: {observation}"
                except Exception as e:
                    return f"Error executing {action_name}: {str(e)}", None
            
            # Handle response
            if response_match:
                self.logger.info(f"{self.agent_name} processing legacy response")
                return response_match.group(1).strip(), None
            
            # Final fallback: treat as direct response only if no action-like patterns
            if not re.search(r'\b(?:' + '|'.join(self.actions.keys()) + r')\b', result, re.IGNORECASE):
                self.logger.info(f"{self.agent_name} treating as direct response (no action patterns found)")
                return result.strip(), None
            
            # If we see action names but can't parse, request retry
            self.logger.error(f"{self.agent_name} found action patterns but couldn't parse format")
            return "Could not parse response format. Please use valid JSON format as specified in the template.", None

    async def query(self, messages: List[Message], user_id: UUID, request_id: str = None, supabase = None) -> str:
        """
        Process a message through the agent, handling multiple turns of action/observation.
        
        Args:
            messages: The input message(s) to process
            user_id: The ID of the user making the request
            request_id: Optional request ID for cancellation checking
            supabase: Optional database connection for cancellation checking
            
        Returns:
            The final response string to send to the client
        """
        try:
            # Log query start with structured context
            log_agent_event(
                self.logger,
                f"Starting query processing for user {user_id}",
                agent_name=self.agent_name,
                user_id=str(user_id),
                request_id=request_id,
                action="query_start",
                message_count=len(messages) if isinstance(messages, list) else 1
            )

            # Extract and log the user's request
            user_request_content = ""
            if isinstance(messages, list):
                # Find the last user message in the list
                for msg in reversed(messages):
                    if isinstance(msg, Message) and msg.role == "user":
                        user_request_content = msg.content
                        break
                    elif isinstance(msg, dict) and msg.get('role') == 'user':
                        user_request_content = msg.get('content', '')
                        break
            elif isinstance(messages, Message) and messages.role == "user":
                user_request_content = messages.content
            elif isinstance(messages, dict) and messages.get('role') == 'user':
                user_request_content = messages.get('content', '')

            # Log the user request if we found one
            if user_request_content and supabase:
                self._log_mas_step('user_request', user_request_content, user_id, request_id, 0, supabase)

            self.add_message(messages)
            
            # Define cancellation check function
            def check_cancellation():
                if request_id and supabase:
                    from app.agents.chat_agent.chat_agent import check_cancellation_request
                    from fastapi import HTTPException
                    if check_cancellation_request(request_id, user_id, supabase):
                        raise HTTPException(status_code=499, detail="Request was cancelled during agent processing")
            
            action_count = 0
            
            while True:
                # Check for cancellation before each iteration
                check_cancellation()
                
                result = self.execute()
                self.messages.append({
                    "role": "assistant",
                    "content": result,
                    "type": "text"
                })
                
                response, observation = await self.process_actions(result)
                self.logger.info(f"Response: {response}")
                self.logger.info(f"Observation: {observation}")

                # Log MAS steps if we have the stored data
                if hasattr(self, '_current_thought') and self._current_thought:
                    self._log_mas_step('thought', self._current_thought, user_id, request_id,
                                     action_count, supabase)
                    self._current_thought = None  # Clear after logging

                if hasattr(self, '_current_action') and self._current_action:
                    self._log_mas_step('action',
                                     f"Action: {self._current_action['name']}",
                                     user_id, request_id, action_count, supabase,
                                     action_name=self._current_action['name'],
                                     action_params=self._current_action['params'])
                    self._current_action = None  # Clear after logging
                
                # If we got a response, return it
                if response is not None:
                    # Log the final response
                    self._log_mas_step('response', response, user_id, request_id,
                                     action_count, supabase)

                    # Process observation embedding if the response contains the marker
                    if self._last_observation and "$$$observation$$$" in response:
                        response = self._process_observation_embedding(response, self._last_observation)

                    # Check if this is the Config Agent and if settings were successfully updated
                    if self.agent_name == "Config Agent":
                        response_lower = response.lower()
                        self.settings_updated = (
                            "successfully" in response_lower
                        )
                        if self.settings_updated:
                            self.logger.info(f"Config Agent detected successful settings update: {response}")
                    
                    # For Chat Agent, check if any message contains settings update marker and preserve it
                    if self.agent_name == "Chat Agent":
                        for message in self.messages:
                            if isinstance(message, dict) and "[SETTINGS_UPDATED]" in message.get("content", ""):
                                if "[SETTINGS_UPDATED]" not in response:
                                    response += " [SETTINGS_UPDATED]"
                                    self.logger.info("Chat Agent preserving settings update marker in final response")
                                break
                    
                    # Log agent final response
                    log_agent_event(
                        self.logger,
                        "Agent produced final response",
                        agent_name=self.agent_name,
                        user_id=str(user_id),
                        request_id=request_id,
                        action="final_response",
                        response_preview=response[:200] if response else None,
                        response_length=len(response) if response else 0
                    )
                    
                    # Log successful query completion
                    log_agent_event(
                        self.logger,
                        f"Query completed successfully for user {user_id}",
                        agent_name=self.agent_name,
                        user_id=str(user_id),
                        request_id=request_id,
                        action="query_complete",
                        actions_executed=action_count,
                        settings_updated=getattr(self, 'settings_updated', False)
                    )
                    
                    return response
                    
                # If we got an observation, we executed an action
                if observation is not None:
                    action_count += 1
                    
                    # Increment turn count for each action execution
                    if request_id and supabase:
                        try:
                            turn_result = supabase.rpc('increment_request_turns', {'request_id_param': request_id}).execute()
                            if turn_result.data and turn_result.data != -1:
                                self.logger.info(f"Agent {self.agent_name} action {action_count} incremented turn count to {turn_result.data} for request {request_id}")
                            else:
                                self.logger.error(f"Failed to increment turn count for request {request_id} in agent {self.agent_name} action {action_count}")
                        except Exception as turn_error:
                            self.logger.error(f"Error incrementing turn count in agent {self.agent_name} action {action_count}: {str(turn_error)}")
                    
                    # Log the observation
                    log_agent_event(
                        self.logger,
                        "Agent produced observation",
                        agent_name=self.agent_name,
                        user_id=str(user_id),
                        request_id=request_id,
                        action="observation",
                        observation=observation[:500] if observation else None,
                        action_count=action_count,
                        max_turns=self.max_turns
                    )

                    # Log observation in MAS logs
                    self._log_mas_step('observation', observation, user_id, request_id,
                                     action_count, supabase)

                    # Store the observation for potential embedding in the final response
                    self._last_observation = observation

                    self.logger.info(f"Action {action_count}/{self.max_turns} executed")
                    
                    # Check for cancellation after action execution
                    check_cancellation()
                    
                    if action_count >= self.max_turns:
                        self.logger.warning("Max actions reached without final response")
                        return self._handle_max_turns_reached()
                        
                    self.logger.info(f"Adding observation to messages: {observation}")
                    self.add_message(observation)
                else:
                    self.logger.info("No observation to process, ending query")
                    break
                    
            self.logger.warning("Query ended without final response")
            return "Query ended without final response"
            
        except Exception as e:
            error_msg = f"Error in agent loop: {str(e)}"
            self.logger.error(error_msg)
            return error_msg 