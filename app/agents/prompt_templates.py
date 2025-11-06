from typing import Dict, Any, List, Optional, Union
from app.agents.models import Action
from datetime import datetime
from app.config import USE_ONE_HOUR_CACHE


RESPONSE_TEMPLATE = """=== Response Template ===
You must respond with valid JSON in one of the following two formats:

1. When an action IS needed (calling a tool or sub agent):
```json
{{
  "thought": "Your reasoning about what action to take",
  "type": "action",
  "action": {{
    "name": "action_name",
    "parameters": {{...}}
  }}
}}
```
OR

2. When NO action or NO FURTHER ACTION is needed:
```json
{{
  "thought": "Your reasoning about why no action is needed", 
  "type": "response",
  "response": "Your response"
}}
```

Always output valid JSON. Think first in the "thought" field, then specify the type and appropriate action or response.
"""

def format_action(action: Action) -> str:
    """Format a single action into a string description."""
    action_str = [
        f"Name: {action.name}:",
        f"Description: {action.description}",
        "Action Parameters:"
    ]

    for param_name, param_details in action.parameters.items():
        param_type = param_details.get("type", "any")
        param_desc = param_details.get("description", "")
        action_str.append(f"    - {param_name} ({param_type}): {param_desc}")

    action_str.append(f"Returns: {action.returns}")

    if action.example:
        # Ensure the example format is consistent with the expected JSON structure
        example = action.example

        # Check if the example is using the old string format instead of JSON
        if example.startswith(f'Action: {action.name}: "') and not example.startswith(f'Action: {action.name}: {{'):
            # Extract the parameter value from the string format
            param_value = example.split(f'Action: {action.name}: "', 1)[1].rsplit('"', 1)[0]

            # Determine the parameter name - use the first parameter name if there's only one parameter
            if len(action.parameters) == 1:
                param_name = list(action.parameters.keys())[0]
                example = f'Action: {action.name}: {{"{param_name}": "{param_value}"}}'
            elif 'request' in action.parameters:
                # Default to 'request' parameter for agent calls
                example = f'Action: {action.name}: {{"request": "{param_value}"}}'

        action_str.append(f"Example Invocation: {example}")

    return "\n".join(action_str)

def build_system_prompt(
    actions: List[Action],
    additional_context: str = "No additional context",
    general_instructions: Optional[str] = "No general instructions",
    examples: Optional[Union[str, List[Union[str, Dict[str, str]]]]] = None,
    calling_agent: str = None,
    enable_caching: bool = False,
    cache_static_content: bool = True
) -> Union[str, List[Dict[str, Any]]]:
    """
    Create a base system prompt that can be customized.
    
    Args:
        actions: List of available actions the agent can perform
        additional_context: Additional context about the agent's role and capabilities
        general_instructions: User's general instructions for the AI agent behavior
        examples: Optional examples as either a multiline string, list of example dictionaries, or list of example strings
        calling_agent: Name of the calling agent
        enable_caching: Whether to enable Anthropic prompt caching
        cache_static_content: Whether to cache static content (actions, base instructions)
        
    Returns:
        A formatted system prompt string (if caching disabled) or structured list (if caching enabled)
    """

    if not enable_caching:
        # Original behavior - return string
        # Remove calling agent information for consistency with cached version
        
        base_prompt = f"""=== Context ===
{additional_context}

{RESPONSE_TEMPLATE}

"""

        # Convert base prompt to list of lines
        prompt_sections = base_prompt.split('\n')

        if actions:
            # Add available actions section
            prompt_sections.extend([
                "",
                "=== Available Actions ===",
                "",
                "\n\n".join(f"```\n{format_action(action)}\n```" for action in actions),
                "",
            ])

        # Add instructions section after available actions
        prompt_sections.extend([
            "",
            "=== General Instructions ===",
            "- When your response to another agent includes cached data, include the cache key in your response rather than the cached content itself.  All agents have the fetch_from_cache tool.",
            "",
            "=== Additional Instructions/Context ===",
            f"{general_instructions}",
            "",
        ])

        # Add examples if provided
        if examples:
            prompt_sections.extend([
                "",
                "=== Examples of Full Flow ===",
                "",
            ])
            
            if isinstance(examples, str):
                prompt_sections.append(examples)
            else:
                # Format the list of examples
                formatted_examples = []
                for i, example in enumerate(examples):
                    formatted_examples.append(f"Example {i+1}:")
                    if isinstance(example, str):
                        formatted_examples.append(example)
                    else:
                        # Handle dictionary examples
                        formatted_example = []
                        for key, value in example.items():
                            formatted_example.append(f"{key}: {value}")
                        formatted_examples.append("\n".join(formatted_example))
                prompt_sections.append("\n\n".join(formatted_examples))
            
            prompt_sections.append("")

        # Combine all sections, filtering out empty strings
        return "\n".join(section for section in prompt_sections if section)
    
    else:
        # Caching enabled - return structured format
        # Consolidate static content into single cached block for maximum cache efficiency
        system_blocks = []
        
        # Combine all static content into one cached block: Context, Response Template, Actions
        static_content_parts = []
        
        # 1. Context (now static without calling agent info)
        static_content_parts.append(f"=== Context ===\n{additional_context}")
        
        # 2. Response Template (static)
        response_template = f"""{RESPONSE_TEMPLATE}
"""
        static_content_parts.append(response_template)
        
        # 3. Available Actions (static)
        if actions:
            actions_content = "\n=== Available Actions ===\n\n" + "\n\n".join(f"```\n{format_action(action)}\n```" for action in actions)
            static_content_parts.append(actions_content)
        
        # Add all static content as single cached block
        combined_static_content = "\n".join(static_content_parts)
        
        if cache_static_content:
            system_blocks.append({
                "type": "text",
                "text": combined_static_content,
                "cache_control": {"type": "ephemeral", "ttl": "1h" if USE_ONE_HOUR_CACHE else "5m"}
            })
        else:
            system_blocks.append({
                "type": "text",
                "text": combined_static_content
            })
        
        # 4. Dynamic content: General Instructions + Agent Specific Instructions (not cached)
        dynamic_content_parts = []
        
        general_instructions_content = """
=== General Instructions ===
- When your response to another agent includes cached data, include the cache key in your response rather than the cached content itself.  All agents have the fetch_from_cache tool.
"""
        dynamic_content_parts.append(general_instructions_content)
        
        # 5. Agent Specific Instructions (dynamic)
        agent_instructions_content = f"\n=== Additional Instructions/Context ===\n{general_instructions}\n"
        dynamic_content_parts.append(agent_instructions_content)
        
        # 6. Examples (if provided, add to dynamic content since they may vary by request)
        if examples:
            examples_content = "\n=== Examples of Full Flow ===\n\n"
            
            if isinstance(examples, str):
                examples_content += examples
            else:
                # Format the list of examples
                formatted_examples = []
                for i, example in enumerate(examples):
                    formatted_examples.append(f"Example {i+1}:")
                    if isinstance(example, str):
                        formatted_examples.append(example)
                    else:
                        # Handle dictionary examples
                        formatted_example = []
                        for key, value in example.items():
                            formatted_example.append(f"{key}: {value}")
                        formatted_examples.append("\n".join(formatted_example))
                examples_content += "\n\n".join(formatted_examples)
            
            dynamic_content_parts.append(examples_content)
        
        # Add all dynamic content as single uncached block
        combined_dynamic_content = "\n".join(dynamic_content_parts)
        system_blocks.append({
            "type": "text",
            "text": combined_dynamic_content
        })

        return system_blocks

